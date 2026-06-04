from typing import Annotated

from fastapi import APIRouter, Depends

from internal.schemas import BaseResponse
from internal.schemas.agent import (
    AgentOrderSupportReqSchema,
    AgentOrderSupportRespSchema,
)
from internal.services.agents import OrderAgentService, new_order_agent_service
from pkg.toolkit.response import ResponsePayload, success_response

router = APIRouter(prefix="/agent", tags=["api agent"])

OrderAgentServiceDep = Annotated[OrderAgentService, Depends(new_order_agent_service)]


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
        使用结构化 ReAct / Tool Calling Agent 回答订单状态和退货政策相关问题。

    权限边界:
        需要有效的用户 token（`/v1` 前缀默认认证）；当前示例未做订单归属校验，
        生产场景应在工具或 Service 层校验订单是否属于当前用户。

    业务边界:
        只读接口；会调用配置的 OpenAI-compatible LLM provider，并在 Service 内部调用
        示例工具读取内存中的订单和退货政策数据；不写数据库、不写 Redis、不创建工单。

    Args:
        req: 请求体，包含用户问题和最大 Agent 执行步数。
        order_agent_service: 通过依赖注入获取的 `OrderAgentService` 实例。

    Returns:
        `BaseResponse[AgentOrderSupportRespSchema]`：成功时返回最终回答和每步执行记录；
        LLM provider 未配置时返回 `errors.ServiceUnavailable`。
    """
    result = await order_agent_service.answer_order_support_question(
        question=req.question, max_steps=req.max_steps
    )
    return success_response(data=result.to_schema())
