from internal.agents.router import AgentRoute, HybridAgentRouter
from internal.core import AppException, errors
from internal.infra.llm import AgentLLMClient, new_default_llm_client
from internal.services.agents.audit import (
    AgentAuditContext,
    AgentAuditWriter,
    AuditedAgentLLMClient,
    failed_agent_result,
    new_agent_audit_service,
    record_agent_audit,
)
from internal.services.agents.order import OrderAgentService, new_order_agent_service
from internal.services.agents.payment import (
    PaymentAgentService,
    new_payment_agent_service,
)
from internal.services.dto.agent import AgentChatDTO, AgentRunDTO
from pkg.logger import logger
from pkg.toolkit.string import uuid6_unique_str_id


class AgentRouterService:
    """将统一聊天请求路由到受支持的专业 Agent。"""

    def __init__(
        self,
        *,
        llm_client: AgentLLMClient,
        order_agent_service: OrderAgentService,
        payment_agent_service: PaymentAgentService,
        audit_service: AgentAuditWriter,
    ):
        self._llm_client = llm_client
        self._order_agent_service = order_agent_service
        self._payment_agent_service = payment_agent_service
        self._audit_service = audit_service

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
        audit_context = AgentAuditContext.start(
            agent_name="agent_chat",
            user_id=user_id,
            user_input=question,
            max_steps=max_steps,
        )
        try:
            if confirmation_token is not None:
                route = AgentRoute.ORDER
            else:
                router_agent = HybridAgentRouter(
                    llm_client=AuditedAgentLLMClient(
                        llm_client=self._llm_client,
                        audit_context=audit_context,
                    )
                )
                route = await router_agent.route(question=question)

            if route is AgentRoute.ORDER:
                result = await self._order_agent_service.answer_order_support_question(
                    user_id=user_id,
                    question=question,
                    max_steps=max_steps,
                    confirmation_token=confirmation_token,
                    idempotency_key=idempotency_key,
                )
                await record_agent_audit(
                    audit_writer=self._audit_service,
                    audit_context=audit_context,
                    result=result,
                    metadata={"route": route.value},
                )
                return AgentChatDTO(route=route.value, result=result)

            if route is AgentRoute.PAYMENT:
                result = (
                    await self._payment_agent_service.answer_payment_support_question(
                        user_id=user_id,
                        question=question,
                        max_steps=max_steps,
                        confirmation_token=confirmation_token,
                        idempotency_key=idempotency_key,
                    )
                )
                await record_agent_audit(
                    audit_writer=self._audit_service,
                    audit_context=audit_context,
                    result=result,
                    metadata={"route": route.value},
                )
                return AgentChatDTO(route=route.value, result=result)

            result = AgentRunDTO.final(
                answer="当前仅支持订单、物流、售后、退款、发票和支付相关问题。"
            )
            await record_agent_audit(
                audit_writer=self._audit_service,
                audit_context=audit_context,
                result=result,
                metadata={"route": AgentRoute.UNSUPPORTED.value},
            )
            return AgentChatDTO(route=AgentRoute.UNSUPPORTED.value, result=result)
        except AppException as exc:
            await record_agent_audit(
                audit_writer=self._audit_service,
                audit_context=audit_context,
                result=failed_agent_result(
                    run_id=uuid6_unique_str_id(),
                    error_type=f"AppException:{exc.error.code}",
                    message=exc.message,
                ),
            )
            raise
        except Exception as exc:
            logger.warning(f"Agent router failed: {type(exc).__name__}")
            await record_agent_audit(
                audit_writer=self._audit_service,
                audit_context=audit_context,
                result=failed_agent_result(
                    run_id=uuid6_unique_str_id(),
                    error_type=type(exc).__name__,
                ),
            )
            raise AppException(
                errors.ServiceUnavailable, message="Agent Router 暂不可用"
            ) from exc


_agent_router_service: AgentRouterService | None = None


def new_agent_router_service() -> AgentRouterService:
    """依赖注入：获取 AgentRouterService 单例。"""
    global _agent_router_service
    if _agent_router_service is None:
        _agent_router_service = AgentRouterService(
            llm_client=new_default_llm_client(),
            order_agent_service=new_order_agent_service(),
            payment_agent_service=new_payment_agent_service(),
            audit_service=new_agent_audit_service(),
        )
    return _agent_router_service
