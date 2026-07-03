from collections.abc import AsyncIterable, AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from internal.schemas import BaseResponse
from internal.schemas.agent import (
    AgentChatReqSchema,
    AgentChatRespSchema,
    AgentOrderSupportReqSchema,
    AgentOrderSupportRespSchema,
    AgentPaymentSupportReqSchema,
    AgentPaymentSupportRespSchema,
)
from internal.services.agents import (
    AgentRouterService,
    OrderAgentService,
    PaymentAgentService,
    new_agent_router_service,
    new_order_agent_service,
    new_payment_agent_service,
)
from internal.schemas.agent import AgentStreamEventDTO
from internal.utils.stream import stream_with_chunk_control
from pkg.request_context import get_user_id
from pkg.api_response import ResponsePayload, success_response, wrap_sse_event

router = APIRouter(prefix="/agent", tags=["api agent"])
_AGENT_STREAM_CHUNK_TIMEOUT_SECONDS = 70.0
_SSE_MEDIA_TYPE = "text/event-stream"
_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}

OrderAgentServiceDep = Annotated[OrderAgentService, Depends(new_order_agent_service)]
PaymentAgentServiceDep = Annotated[
    PaymentAgentService, Depends(new_payment_agent_service)
]
AgentRouterServiceDep = Annotated[AgentRouterService, Depends(new_agent_router_service)]


@router.post(
    "/chat",
    response_model=BaseResponse[AgentChatRespSchema],
    summary="统一 Agent 聊天入口",
)
async def agent_chat(
    req: AgentChatReqSchema,
    agent_router_service: AgentRouterServiceDep,
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
    agent_router_service: AgentRouterServiceDep,
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
    order_agent_service: OrderAgentServiceDep,
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
    order_agent_service: OrderAgentServiceDep,
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
    payment_agent_service: PaymentAgentServiceDep,
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
    payment_agent_service: PaymentAgentServiceDep,
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
