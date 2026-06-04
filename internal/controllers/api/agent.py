from typing import Annotated

from fastapi import APIRouter, Depends

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
from pkg.toolkit.context import get_user_id
from pkg.toolkit.response import ResponsePayload, success_response

router = APIRouter(prefix="/agent", tags=["api agent"])

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
        req: 请求体，包含问题、最大执行步数，以及可选的确认 token 与幂等键。
        agent_router_service: 通过依赖注入获取的 `AgentRouterService` 实例。

    Returns:
        `BaseResponse[AgentChatRespSchema]`：返回 Router 选择的业务域和专业 Agent 结果；
        无效确认 token 返回 `errors.BadRequest`，资源归属不匹配返回 `errors.Forbidden`，
        LLM provider、Redis 或专业 Agent 不可用时返回 `errors.ServiceUnavailable`。
    """
    result = await agent_router_service.chat(
        user_id=get_user_id(),
        question=req.question,
        max_steps=req.max_steps,
        confirmation_token=req.confirmation_token,
        idempotency_key=req.idempotency_key,
    )
    return success_response(data=result.to_schema())


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
        req: 请求体，包含用户问题、最大执行步数，以及可选的确认 token 与幂等键。
        order_agent_service: 通过依赖注入获取的 `OrderAgentService` 实例。

    Returns:
        `BaseResponse[AgentOrderSupportRespSchema]`：成功时返回最终回答和每步执行记录；
        无效确认 token 返回 `errors.BadRequest`，资源归属不匹配返回 `errors.Forbidden`，
        LLM provider 或 Redis 不可用时返回 `errors.ServiceUnavailable`。
    """
    result = await order_agent_service.answer_order_support_question(
        user_id=get_user_id(),
        question=req.question,
        max_steps=req.max_steps,
        confirmation_token=req.confirmation_token,
        idempotency_key=req.idempotency_key,
    )
    return success_response(data=result.to_schema())


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
        req: 请求体，包含问题、最大执行步数，以及当前不支持的确认 token 与幂等键字段。
        payment_agent_service: 通过依赖注入获取的 `PaymentAgentService` 实例。

    Returns:
        `BaseResponse[AgentPaymentSupportRespSchema]`：返回支付 Agent 执行结果；
        携带确认 token 返回 `errors.BadRequest`，LLM provider 不可用时返回
        `errors.ServiceUnavailable`。
    """
    result = await payment_agent_service.answer_payment_support_question(
        user_id=get_user_id(),
        question=req.question,
        max_steps=req.max_steps,
        confirmation_token=req.confirmation_token,
        idempotency_key=req.idempotency_key,
    )
    return success_response(data=result.to_schema())
