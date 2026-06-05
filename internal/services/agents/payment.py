from __future__ import annotations

from internal.agents.payment import PaymentAgentBuilder
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
from pkg.logger import logger
from pkg.toolkit.string import uuid6_unique_str_id


class PaymentAgentService:
    def __init__(self, *, llm_client: AgentLLMClient, audit_service: AgentAuditWriter):
        self._llm_client = llm_client
        self._audit_service = audit_service

    async def answer_payment_support_question(
        self,
        *,
        user_id: int,
        question: str,
        max_steps: int = 4,
        confirmation_token: str | None = None,
        idempotency_key: str | None = None,
    ) -> AgentRunDTO:
        """使用 ReActAgent 回答支付支持问题。"""
        audit_context = AgentAuditContext.start(
            agent_name="payment_support",
            user_id=user_id,
            user_input=question,
            max_steps=max_steps,
        )
        try:
            if confirmation_token is not None or idempotency_key is not None:
                raise AppException(
                    errors.BadRequest, message="支付 Agent 暂不支持确认动作"
                )

            agent = PaymentAgentBuilder(
                llm_client=AuditedAgentLLMClient(
                    llm_client=self._llm_client,
                    audit_context=audit_context,
                ),
                max_steps=max_steps,
            ).build()
            result = await agent.run(user_input=question)
            dto = AgentRunDTO.from_agent_result(result)
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
            logger.warning(f"Payment support agent failed: {type(exc).__name__}")
            await record_agent_audit(
                audit_writer=self._audit_service,
                audit_context=audit_context,
                result=failed_agent_result(
                    run_id=uuid6_unique_str_id(),
                    error_type=type(exc).__name__,
                ),
            )
            raise AppException(
                errors.ServiceUnavailable, message="支付支持 Agent 暂不可用"
            ) from exc


_payment_agent_service: PaymentAgentService | None = None


def new_payment_agent_service() -> PaymentAgentService:
    """依赖注入：获取 PaymentAgentService 单例。"""
    global _payment_agent_service
    if _payment_agent_service is None:
        _payment_agent_service = PaymentAgentService(
            llm_client=new_default_llm_client(),
            audit_service=new_agent_audit_service(),
        )
    return _payment_agent_service
