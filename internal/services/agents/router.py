from internal.agents.router import AgentRoute, HybridAgentRouter
from internal.core import AppException, errors
from internal.infra.llm import AgentLLMClient, new_default_llm_client
from internal.services.agents.order import OrderAgentService, new_order_agent_service
from internal.services.agents.payment import (
    PaymentAgentService,
    new_payment_agent_service,
)
from internal.services.dto.agent import AgentChatDTO, AgentRunDTO
from pkg.logger import logger


class AgentRouterService:
    """将统一聊天请求路由到受支持的专业 Agent。"""

    def __init__(
        self,
        *,
        llm_client: AgentLLMClient,
        order_agent_service: OrderAgentService,
        payment_agent_service: PaymentAgentService,
    ):
        self._router_agent = HybridAgentRouter(llm_client=llm_client)
        self._order_agent_service = order_agent_service
        self._payment_agent_service = payment_agent_service

    async def chat(
        self,
        *,
        user_id: int,
        question: str,
        max_steps: int = 4,
        confirmation_token: str | None = None,
        idempotency_key: str | None = None,
    ) -> AgentChatDTO:
        """路由统一聊天请求并返回专业 Agent 结果。"""
        if confirmation_token is not None:
            route = AgentRoute.ORDER
        else:
            try:
                route = await self._router_agent.route(question=question)
            except AppException:
                raise
            except Exception as exc:
                logger.warning(f"Agent router failed: {type(exc).__name__}")
                raise AppException(
                    errors.ServiceUnavailable, message="Agent Router 暂不可用"
                ) from exc

        if route is AgentRoute.ORDER:
            result = await self._order_agent_service.answer_order_support_question(
                user_id=user_id,
                question=question,
                max_steps=max_steps,
                confirmation_token=confirmation_token,
                idempotency_key=idempotency_key,
            )
            return AgentChatDTO(route=route.value, result=result)

        if route is AgentRoute.PAYMENT:
            result = await self._payment_agent_service.answer_payment_support_question(
                user_id=user_id,
                question=question,
                max_steps=max_steps,
                confirmation_token=confirmation_token,
                idempotency_key=idempotency_key,
            )
            return AgentChatDTO(route=route.value, result=result)

        return AgentChatDTO(
            route=AgentRoute.UNSUPPORTED.value,
            result=AgentRunDTO.final(
                answer="当前仅支持订单、物流、售后、退款、发票和支付相关问题。"
            ),
        )


_agent_router_service: AgentRouterService | None = None


def new_agent_router_service() -> AgentRouterService:
    """依赖注入：获取 AgentRouterService 单例。"""
    global _agent_router_service
    if _agent_router_service is None:
        _agent_router_service = AgentRouterService(
            llm_client=new_default_llm_client(),
            order_agent_service=new_order_agent_service(),
            payment_agent_service=new_payment_agent_service(),
        )
    return _agent_router_service
