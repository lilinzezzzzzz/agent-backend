from __future__ import annotations

from internal.agents.order import OrderAgentBuilder
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
from internal.services.dto.agent import AgentRunDTO
from internal.services.order import OrderService, new_order_service
from pkg.logger import logger
from pkg.toolkit.string import uuid6_unique_str_id


class OrderAgentService:
    def __init__(
        self,
        *,
        llm_client: AgentLLMClient,
        order_service: OrderService,
        audit_service: AgentAuditWriter,
    ):
        self._llm_client = llm_client
        self._order_service = order_service
        self._audit_service = audit_service

    async def answer_order_support_question(
        self,
        *,
        user_id: int,
        question: str,
        max_steps: int = 4,
        confirmation_token: str | None = None,
        idempotency_key: str | None = None,
    ) -> AgentRunDTO:
        """使用 ReActAgent 回答订单支持问题。"""
        audit_context = AgentAuditContext.start(
            agent_name="order_support",
            user_id=user_id,
            user_input=question,
            max_steps=max_steps,
        )
        try:
            if confirmation_token is not None:
                if idempotency_key is None:
                    raise AppException(errors.BadRequest, message="确认动作缺少幂等键")
                confirmed = await self._order_service.confirm_invoice_request(
                    user_id=user_id,
                    confirmation_token=confirmation_token,
                    idempotency_key=idempotency_key,
                )
                confirmation_result = AgentRunDTO.from_confirmed_invoice(confirmed)
                await record_agent_audit(
                    audit_writer=self._audit_service,
                    audit_context=audit_context,
                    result=confirmation_result,
                    metadata={"flow": "confirmation"},
                )
                return confirmation_result

            agent = OrderAgentBuilder(
                llm_client=AuditedAgentLLMClient(
                    llm_client=self._llm_client,
                    audit_context=audit_context,
                ),
                order_service=self._order_service,
                user_id=user_id,
                max_steps=max_steps,
            ).build()
            run_result = await agent.run(user_input=question)
            dto = AgentRunDTO.from_agent_result(run_result)
            await record_agent_audit(
                audit_writer=self._audit_service,
                audit_context=audit_context,
                result=dto,
                metadata={"flow": "react"},
            )
            return dto
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
            logger.warning(f"Order support agent failed: {type(exc).__name__}")
            await record_agent_audit(
                audit_writer=self._audit_service,
                audit_context=audit_context,
                result=failed_agent_result(
                    run_id=uuid6_unique_str_id(),
                    error_type=type(exc).__name__,
                ),
            )
            raise AppException(
                errors.ServiceUnavailable, message="订单支持 Agent 暂不可用"
            ) from exc


_order_agent_service: OrderAgentService | None = None


def new_order_agent_service() -> OrderAgentService:
    """依赖注入：获取 OrderAgentService 单例。"""
    global _order_agent_service
    if _order_agent_service is None:
        _order_agent_service = OrderAgentService(
            llm_client=new_default_llm_client(),
            order_service=new_order_service(),
            audit_service=new_agent_audit_service(),
        )
    return _order_agent_service
