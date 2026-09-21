from collections.abc import AsyncIterable, AsyncIterator
from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, Path
from fastapi.responses import StreamingResponse

from internal.config import settings
from internal.services.agents import (
    AgentExecutionService,
    AgentRouterService,
    OrderAgentService,
    PaymentAgentService,
    new_agent_execution_service,
    new_agent_router_service,
    new_order_agent_service,
    new_payment_agent_service,
)
from internal.schemas import BaseResponse
from internal.schemas.agent import (
    AgentChatReqSchema,
    AgentChatRespSchema,
    AgentOrderSupportReqSchema,
    AgentOrderSupportRespSchema,
    AgentPaymentSupportReqSchema,
    AgentPaymentSupportRespSchema,
    AgentRunCreateReqSchema,
    AgentRunCreateRespSchema,
    AgentRunDetailRespSchema,
    AgentRunInterruptReqSchema,
    AgentRunInterruptRespSchema,
    AgentRunResumeReqSchema,
    AgentRunResumeRespSchema,
    AgentStreamEventDTO,
    run_view_to_schema,
)
from internal.utils.stream import stream_with_chunk_control
from pkg.api_response import ResponsePayload, success_response, wrap_sse_event
from pkg.request_context import get_user_id

router = APIRouter(prefix="/agent", tags=["api agent"])
_AGENT_STREAM_CHUNK_TIMEOUT_SECONDS = 70.0
_SSE_MEDIA_TYPE = "text/event-stream"
_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}
_MANAGED_SSE_BUFFER_SIZE = 32
_SSE_HEARTBEAT_COMMENT = ": heartbeat\n\n"


@router.post(
    "/chat",
    response_model=BaseResponse[AgentChatRespSchema],
    summary="统一 Agent 聊天入口",
)
async def agent_chat(
    req: AgentChatReqSchema,
    agent_router_service: Annotated[
        AgentRouterService,
        Depends(new_agent_router_service),
    ],
) -> ResponsePayload:
    """路由统一 Agent 聊天请求。

    业务摘要:
        识别用户问题所属业务域，并路由到对应专业 Agent；当前支持订单和支付业务域。

    权限边界:
        需要有效的用户 token（`/v1` 前缀默认认证）；专业 Service 会校验资源归属，
        确认 token 只能由签发该 token 的用户使用。

    业务边界:
        普通请求会调用 LLM Router 和专业 Agent；副作用动作首次只返回待确认信息。
        携带服务端确认 token 和幂等键的请求绕过 LLM Router，由确定性 Service 执行。

    Args:
        req: 请求体，包含可选 session_id、问题、最大执行步数，以及可选的确认 token 与幂等键；
            首次普通对话可不传 session_id，继续对话时传入上次响应的 session_id。
        agent_router_service: 通过依赖注入获取的 `AgentRouterService` 实例。

    Returns:
        `BaseResponse[AgentChatRespSchema]`：返回 Router 选择的业务域和专业 Agent 结果；
        无效确认 token 返回 `errors.BadRequest`，资源归属不匹配返回 `errors.Forbidden`，
        LLM provider、Redis 或专业 Agent 不可用时返回 `errors.ServiceUnavailable`。
    """
    result = await agent_router_service.chat(
        user_id=get_user_id(),
        session_id=req.session_id,
        question=req.question,
        max_steps=req.max_steps,
        confirmation_token=req.confirmation_token,
        idempotency_key=req.idempotency_key,
    )
    return success_response(data=result.to_schema())


@router.post(
    "/chat/stream",
    response_class=StreamingResponse,
    summary="统一 Agent 聊天流式入口",
)
async def agent_chat_stream(
    req: AgentChatReqSchema,
    agent_router_service: Annotated[
        AgentRouterService,
        Depends(new_agent_router_service),
    ],
) -> StreamingResponse:
    """流式路由统一 Agent 聊天请求。

    业务摘要:
        识别用户问题所属业务域，并以 SSE 事件流转发专业 Agent 的执行过程和最终结果。

    权限边界:
        需要有效的用户 token（`/v1` 前缀默认认证）；专业 Service 会校验资源归属，
        确认 token 只能由签发该 token 的用户使用。

    业务边界:
        新增流式 API，不替代现有 JSON 接口；响应为 `text/event-stream`，
        不使用 `BaseResponse[T]` 信封。事件名包括 `route`、`run_started`、
        `step_completed`、`run_completed` 和 `error`，每条 `data` 都是 JSON object。

    Args:
        req: 请求体，包含可选 session_id、问题、最大执行步数，以及可选的确认 token 与幂等键。
        agent_router_service: 通过依赖注入获取的 `AgentRouterService` 实例。

    Returns:
        `StreamingResponse`：持续返回 Agent 路由和执行事件；业务错误以 `error`
        SSE 事件携带稳定错误码，流式超时由 `errors.StreamTimeout` 语义的数据块表示。
    """
    return _agent_streaming_response(
        agent_router_service.stream_chat(
            user_id=get_user_id(),
            session_id=req.session_id,
            question=req.question,
            max_steps=req.max_steps,
            confirmation_token=req.confirmation_token,
            idempotency_key=req.idempotency_key,
        )
    )


@router.post(
    "/order/support",
    response_model=BaseResponse[AgentOrderSupportRespSchema],
    summary="订单支持 Agent",
)
async def order_support_agent(
    req: AgentOrderSupportReqSchema,
    order_agent_service: Annotated[
        OrderAgentService,
        Depends(new_order_agent_service),
    ],
) -> ResponsePayload:
    """订单支持 Agent。

    业务摘要:
        使用结构化 ReAct / Tool Calling Agent 回答订单、物流、售后、退款和发票问题，
        并为需要副作用的开票动作生成待确认信息。

    权限边界:
        需要有效的用户 token（`/v1` 前缀默认认证）；订单查询和开票确认均由
        Service 校验订单归属，确认 token 只能由签发该 token 的用户使用。

    业务边界:
        普通问题会调用配置的 OpenAI-compatible LLM provider；开票请求首次只在 Redis
        保存短期待确认动作。携带确认 token 和幂等键再次请求时，确定性 Service 幂等受理
        开票申请；当前示例返回 queued，不代表发票已开具完成。

    Args:
        req: 请求体，包含可选 session_id、用户问题、最大执行步数，以及可选的确认 token 与幂等键；
            首次普通对话可不传 session_id，继续对话或确认同一会话动作时传入上次响应的 session_id。
        order_agent_service: 通过依赖注入获取的 `OrderAgentService` 实例。

    Returns:
        `BaseResponse[AgentOrderSupportRespSchema]`：成功时返回最终回答和每步执行记录；
        无效确认 token 返回 `errors.BadRequest`，资源归属不匹配返回 `errors.Forbidden`，
        LLM provider 或 Redis 不可用时返回 `errors.ServiceUnavailable`。
    """
    result = await order_agent_service.answer_order_support_question(
        user_id=get_user_id(),
        session_id=req.session_id,
        question=req.question,
        max_steps=req.max_steps,
        confirmation_token=req.confirmation_token,
        idempotency_key=req.idempotency_key,
    )
    return success_response(data=result.to_schema())


@router.post(
    "/order/support/stream",
    response_class=StreamingResponse,
    summary="订单支持 Agent 流式接口",
)
async def order_support_agent_stream(
    req: AgentOrderSupportReqSchema,
    order_agent_service: Annotated[
        OrderAgentService,
        Depends(new_order_agent_service),
    ],
) -> StreamingResponse:
    """流式执行订单支持 Agent。

    业务摘要:
        使用 SSE 事件流返回订单、物流、售后、退款和发票 Agent 的执行过程与最终回答。

    权限边界:
        需要有效的用户 token（`/v1` 前缀默认认证）；订单查询和开票确认均由
        Service 校验订单归属，确认 token 只能由签发该 token 的用户使用。

    业务边界:
        新增流式 API，不替代现有 JSON 接口；普通请求会流式输出 ReAct step，
        确认请求仍由确定性 Service 执行后映射为事件序列。响应为 `text/event-stream`，
        不使用 `BaseResponse[T]` 信封。

    Args:
        req: 请求体，包含可选 session_id、用户问题、最大执行步数，以及可选的确认 token 与幂等键。
        order_agent_service: 通过依赖注入获取的 `OrderAgentService` 实例。

    Returns:
        `StreamingResponse`：事件名包括 `run_started`、`step_completed`、
        `run_completed` 和 `error`；业务错误以 `error` SSE 事件携带稳定错误码。
    """
    return _agent_streaming_response(
        order_agent_service.stream_order_support_question(
            user_id=get_user_id(),
            session_id=req.session_id,
            question=req.question,
            max_steps=req.max_steps,
            confirmation_token=req.confirmation_token,
            idempotency_key=req.idempotency_key,
        )
    )


@router.post(
    "/payment/support",
    response_model=BaseResponse[AgentPaymentSupportRespSchema],
    summary="支付支持 Agent",
)
async def payment_support_agent(
    req: AgentPaymentSupportReqSchema,
    payment_agent_service: Annotated[
        PaymentAgentService,
        Depends(new_payment_agent_service),
    ],
) -> ResponsePayload:
    """支付支持 Agent。

    业务摘要:
        使用结构化 ReAct / Tool Calling Agent 回答支付方式、付款失败、扣款异常、
        账单、分期和支付金额计算相关问题。

    权限边界:
        需要有效的用户 token（`/v1` 前缀默认认证）；当前支付 Agent 不读取用户支付资产，
        后续接入支付事实查询时必须由 Service 校验资源归属。

    业务边界:
        普通问题会调用配置的 OpenAI-compatible LLM provider；当前只提供支付咨询和纯金额计算，
        不发起真实支付、扣款、退款、解绑银行卡或账单修改，也不接受确认 token 执行副作用动作。

    Args:
        req: 请求体，包含可选 session_id、问题、最大执行步数，以及当前不支持的确认 token 与幂等键字段；
            首次普通对话可不传 session_id，继续对话时传入上次响应的 session_id。
        payment_agent_service: 通过依赖注入获取的 `PaymentAgentService` 实例。

    Returns:
        `BaseResponse[AgentPaymentSupportRespSchema]`：返回支付 Agent 执行结果；
        携带确认 token 返回 `errors.BadRequest`，LLM provider 不可用时返回
        `errors.ServiceUnavailable`。
    """
    result = await payment_agent_service.answer_payment_support_question(
        user_id=get_user_id(),
        session_id=req.session_id,
        question=req.question,
        max_steps=req.max_steps,
        confirmation_token=req.confirmation_token,
        idempotency_key=req.idempotency_key,
    )
    return success_response(data=result.to_schema())


@router.post(
    "/payment/support/stream",
    response_class=StreamingResponse,
    summary="支付支持 Agent 流式接口",
)
async def payment_support_agent_stream(
    req: AgentPaymentSupportReqSchema,
    payment_agent_service: Annotated[
        PaymentAgentService,
        Depends(new_payment_agent_service),
    ],
) -> StreamingResponse:
    """流式执行支付支持 Agent。

    业务摘要:
        使用 SSE 事件流返回支付方式、付款失败、扣款异常、账单和分期 Agent 的执行过程与最终回答。

    权限边界:
        需要有效的用户 token（`/v1` 前缀默认认证）；当前支付 Agent 不读取用户支付资产，
        后续接入支付事实查询时必须由 Service 校验资源归属。

    业务边界:
        新增流式 API，不替代现有 JSON 接口；当前只提供支付咨询和纯金额计算，
        不发起真实支付、扣款、退款、解绑银行卡或账单修改，也不接受确认 token 执行副作用动作。
        响应为 `text/event-stream`，不使用 `BaseResponse[T]` 信封。

    Args:
        req: 请求体，包含可选 session_id、问题、最大执行步数，以及当前不支持的确认 token 与幂等键字段。
        payment_agent_service: 通过依赖注入获取的 `PaymentAgentService` 实例。

    Returns:
        `StreamingResponse`：事件名包括 `run_started`、`step_completed`、
        `run_completed` 和 `error`；业务错误以 `error` SSE 事件携带稳定错误码。
    """
    return _agent_streaming_response(
        payment_agent_service.stream_payment_support_question(
            user_id=get_user_id(),
            session_id=req.session_id,
            question=req.question,
            max_steps=req.max_steps,
            confirmation_token=req.confirmation_token,
            idempotency_key=req.idempotency_key,
        )
    )


def _agent_streaming_response(
    events: AsyncIterable[AgentStreamEventDTO],
) -> StreamingResponse:
    generator = stream_with_chunk_control(
        generator=_serialize_agent_events(events),
        chunk_timeout=_AGENT_STREAM_CHUNK_TIMEOUT_SECONDS,
        is_sse=True,
    )
    return StreamingResponse(
        generator,
        media_type=_SSE_MEDIA_TYPE,
        headers=_SSE_HEADERS,
    )


async def _serialize_agent_events(
    events: AsyncIterable[AgentStreamEventDTO],
) -> AsyncIterator[str]:
    async for event in events:
        yield wrap_sse_event(event.event.value, event.data)


# =============================================================================
# 受管理 run：创建、查询、打断、恢复
# =============================================================================


@router.post(
    "/runs/create",
    response_model=BaseResponse[AgentRunCreateRespSchema],
    summary="创建受管理 Agent 运行",
)
async def create_managed_run(
    req: AgentRunCreateReqSchema,
    execution_service: Annotated[
        AgentExecutionService,
        Depends(new_agent_execution_service),
    ],
) -> ResponsePayload:
    """创建受管理 run 并冻结执行现场。

    业务摘要:
        只创建会话、用户消息和初始 checkpoint，返回处于 `ready` 的 run/session ID，
        让客户端在模型调用前就拿到可打断的 run_id。

    权限边界:
        需要有效的用户 token（`/v1` 前缀默认认证）；传入 `session_id` 时 Service 校验会话归属。

    业务边界:
        不调用模型、不执行工具；`request_key` 同键不同输入返回冲突，同键同输入返回已有 run。
        该接口不替代现有聊天接口，旧接口语义保持不变。

    Args:
        req: 入口、问题、可选 session_id、最大步数与必填创建幂等键。
        execution_service: 通过依赖注入获取的 `AgentExecutionService` 实例。

    Returns:
        `BaseResponse[AgentRunCreateRespSchema]`：ready 状态的 run_id 与 session_id。
    """
    result = await execution_service.create_run(
        user_id=get_user_id(),
        entrypoint=req.entrypoint.value,
        question=req.question,
        session_id=req.session_id,
        max_steps=req.max_steps,
        request_key=req.request_key,
    )
    return success_response(data=result.to_schema())


@router.get(
    "/runs/{run_id}",
    response_model=BaseResponse[AgentRunDetailRespSchema],
    summary="查询受管理 Agent 运行状态",
)
async def get_managed_run(
    run_id: Annotated[str, Path(description="运行 ID", min_length=1, max_length=64)],
    execution_service: Annotated[
        AgentExecutionService,
        Depends(new_agent_execution_service),
    ],
) -> ResponsePayload:
    """查询受管理 run 已提交的进度。

    业务摘要:
        返回状态、阶段、checkpoint 版本、attempt、完成步骤数、可恢复性、停止原因和
        本用户可见的最终回答；不暴露原始 checkpoint。

    权限边界:
        需要有效的用户 token；查询带用户归属，其他用户的 run 统一返回 NotFound 防止枚举。

    业务边界:
        返回已提交进度，不用于重建遗漏的 SSE 事件；运行中 lease 过期的现场由查询或
        resume 触发一次原子废止与判定。

    Args:
        run_id: 运行 ID。
        execution_service: 通过依赖注入获取的 `AgentExecutionService` 实例。

    Returns:
        `BaseResponse[AgentRunDetailRespSchema]`：裁剪后的运行状态视图。
    """
    view = await execution_service.get_run(user_id=get_user_id(), run_id=run_id)
    return success_response(data=run_view_to_schema(view))


@router.post(
    "/runs/{run_id}/interrupt",
    response_model=BaseResponse[AgentRunInterruptRespSchema],
    summary="打断受管理 Agent 运行",
)
async def interrupt_managed_run(
    req: AgentRunInterruptReqSchema,
    run_id: Annotated[str, Path(description="运行 ID", min_length=1, max_length=64)],
    execution_service: Annotated[
        AgentExecutionService,
        Depends(new_agent_execution_service),
    ],
) -> ResponsePayload:
    """提交打断意图。

    业务摘要:
        把打断请求持久化到 run 行；执行者会在下一个安全点暂停，因此响应返回
        `interrupt_requested` 表示已受理，客户端继续查询状态。

    权限边界:
        需要有效的用户 token；请求带用户归属，其他用户的 run 统一 NotFound。

    业务边界:
        重复打断返回当前状态；终态或 `ready` 返回状态冲突。工具默认不可被主动取消，
        执行者会等待其有界执行结束后保存结果再暂停。

    Args:
        req: 可选的有界打断原因。
        run_id: 运行 ID。
        execution_service: 通过依赖注入获取的 `AgentExecutionService` 实例。

    Returns:
        `BaseResponse[AgentRunInterruptRespSchema]`：服务端实际状态与是否已受理。
    """
    result = await execution_service.interrupt_run(
        user_id=get_user_id(),
        run_id=run_id,
        reason=req.reason,
    )
    return success_response(data=result.to_schema())


@router.post(
    "/runs/{run_id}/resume",
    response_model=BaseResponse[AgentRunResumeRespSchema],
    summary="恢复受管理 Agent 运行",
)
async def resume_managed_run(
    req: AgentRunResumeReqSchema,
    run_id: Annotated[str, Path(description="运行 ID", min_length=1, max_length=64)],
    execution_service: Annotated[
        AgentExecutionService,
        Depends(new_agent_execution_service),
    ],
) -> ResponsePayload:
    """从 ready/interrupted 恢复同一个 run。

    业务摘要:
        按已提交 checkpoint 继续执行到暂停或终态，不重新路由、不重建历史、不重做已提交步骤。

    权限边界:
        需要有效的用户 token；恢复重新校验用户归属，不因 checkpoint 绕过权限变化。

    业务边界:
        不接受 question、max_steps、工具参数或替换上下文；重复 request_key 只返回原 attempt
        的当前状态或已保存结果，不创建第二个执行者。版本不兼容或现场无法安全重放时返回
        明确的冲突错误，不自动改配置。

    Args:
        req: 本次执行尝试的幂等键。
        run_id: 运行 ID。
        execution_service: 通过依赖注入获取的 `AgentExecutionService` 实例。

    Returns:
        `BaseResponse[AgentRunResumeRespSchema]`：run 状态与本次执行结果。
    """
    result = await execution_service.resume_run(
        user_id=get_user_id(),
        run_id=run_id,
        request_key=req.request_key,
    )
    return success_response(data=result.to_schema())


@router.post(
    "/runs/{run_id}/resume/stream",
    response_class=StreamingResponse,
    summary="流式恢复受管理 Agent 运行",
)
async def resume_managed_run_stream(
    req: AgentRunResumeReqSchema,
    run_id: Annotated[str, Path(description="运行 ID", min_length=1, max_length=64)],
    execution_service: Annotated[
        AgentExecutionService,
        Depends(new_agent_execution_service),
    ],
) -> StreamingResponse:
    """以 SSE 流式恢复受管理 run。

    业务摘要:
        执行并返回 `route`、`run_resumed`、`step_completed`、`run_interrupted`、
        `run_status`、`run_completed` 和 `error` 事件；等待工具期间发送 SSE 注释心跳。

    权限边界:
        需要有效的用户 token；恢复重新校验用户归属和工具资源权限。

    业务边界:
        响应为 `text/event-stream`，不使用 `BaseResponse[T]` 信封；重复 request_key 只输出当前
        状态后关闭，不接管或重放原流。客户端断连或慢消费会转换为打断意图，不继续启动下一步骤，
        也不等待向已断开的客户端发送 `run_interrupted`。不支持 Last-Event-ID 或历史事件回放。

    Args:
        req: 本次执行尝试的幂等键。
        run_id: 运行 ID。
        execution_service: 通过依赖注入获取的 `AgentExecutionService` 实例。

    Returns:
        `StreamingResponse`：受管理运行事件流，含等待工具期间的 SSE 注释心跳。
    """
    return _managed_streaming_response(
        execution_service.resume_run_stream(
            user_id=get_user_id(),
            run_id=run_id,
            request_key=req.request_key,
        )
    )


def _managed_streaming_response(
    events: AsyncIterable[AgentStreamEventDTO],
) -> StreamingResponse:
    """构造受管理运行的事件流响应。

    受管理路径不使用旧的单 chunk 超时包装：执行器已通过 heartbeat/control 轮询和带期限的
    shield 管理等待与收尾，这里只负责发送心跳注释和有界事件转发。
    """
    return StreamingResponse(
        _serialize_managed_events(
            events,
            heartbeat_seconds=float(settings.AGENT_CONTROL_POLL_SECONDS),
        ),
        media_type=_SSE_MEDIA_TYPE,
        headers=_SSE_HEADERS,
    )


async def _serialize_managed_events(
    events: AsyncIterable[AgentStreamEventDTO],
    *,
    heartbeat_seconds: float,
) -> AsyncIterator[str]:
    """转发事件；等待期间发送 SSE 注释心跳，慢客户端不会无限积压事件。

    事件先进入有界通道，因此上游执行者不会因为客户端消费过慢而持有无界缓冲。
    """
    send_stream, receive_stream = anyio.create_memory_object_stream[
        AgentStreamEventDTO
    ](_MANAGED_SSE_BUFFER_SIZE)
    async with anyio.create_task_group() as task_group:
        task_group.start_soon(_pump_managed_events, events, send_stream)
        while True:
            with anyio.move_on_after(heartbeat_seconds) as scope:
                try:
                    item = await receive_stream.receive()
                except anyio.EndOfStream:
                    return
            if scope.cancelled_caught:
                yield _SSE_HEARTBEAT_COMMENT
                continue
            yield wrap_sse_event(item.event.value, item.data)


async def _pump_managed_events(
    events: AsyncIterable[AgentStreamEventDTO],
    send_stream: anyio.abc.ObjectSendStream[AgentStreamEventDTO],
) -> None:
    """把 Service 事件推入有界通道；通道关闭时结束转发。"""
    async with send_stream:
        async for event in events:
            try:
                await send_stream.send(event)
            except anyio.ClosedResourceError:
                return
