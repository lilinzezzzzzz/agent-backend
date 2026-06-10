from __future__ import annotations

from collections.abc import AsyncIterator

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
from internal.services.agents.conversation import (
    AgentConversationService,
    new_agent_conversation_service,
)
from internal.services.agents.stream import (
    app_exception_stream_event,
    service_unavailable_stream_event,
    stream_event_from_run_event,
)
from internal.services.dto.agent import AgentRunResultDTO, AgentStreamEventDTO
from internal.services.rag import RagService, new_rag_service
from pkg.logger import logger
from pkg.toolkit import context
from pkg.toolkit.string import uuid6_unique_str_id


class PaymentAgentService:
    def __init__(
        self,
        *,
        llm_client: AgentLLMClient,
        rag_service: RagService,
        audit_service: AgentAuditWriter,
        conversation_service: AgentConversationService | None = None,
    ):
        self._llm_client = llm_client
        self._rag_service = rag_service
        self._audit_service = audit_service
        self._conversation_service = conversation_service

    async def answer_payment_support_question(
        self,
        *,
        user_id: int,
        question: str,
        max_steps: int = 4,
        session_id: str | None = None,
        confirmation_token: str | None = None,
        idempotency_key: str | None = None,
    ) -> AgentRunResultDTO:
        """使用 ReActAgent 回答支付支持问题。"""
        run_start = None
        audit_context = AgentAuditContext.start(
            agent_name="payment_support",
            user_id=user_id,
            user_input=question,
            max_steps=max_steps,
        )
        try:
            if self._conversation_service is not None:
                run_start = await self._conversation_service.start_run(
                    user_id=user_id,
                    session_id=session_id,
                    entrypoint="payment_support",
                    agent_name="payment_support",
                    question=question,
                    max_steps=max_steps,
                    trace_id=context.get_trace_id(),
                )

            if confirmation_token is not None or idempotency_key is not None:
                raise AppException(
                    errors.BadRequest, message="支付 Agent 暂不支持确认动作"
                )

            session_context = None
            if self._conversation_service is not None and run_start is not None:
                conversation_context = await self._conversation_service.load_context(
                    user_id=user_id,
                    session_id=run_start.session_id,
                    max_recent_messages=20,
                    max_recent_chars=12000,
                    exclude_run_id=run_start.run_id,
                )
                session_context = conversation_context.to_prompt_context()

            agent = PaymentAgentBuilder(
                llm_client=AuditedAgentLLMClient(
                    llm_client=self._llm_client,
                    audit_context=audit_context,
                ),
                rag_service=self._rag_service,
                user_id=user_id,
                max_steps=max_steps,
                session_context=session_context,
            ).build()
            result = await agent.run(
                user_input=question,
                run_id=run_start.run_id if run_start is not None else None,
            )
            dto = AgentRunResultDTO.from_agent_result(
                result,
                session_id=run_start.session_id if run_start is not None else None,
            )
            if self._conversation_service is not None and run_start is not None:
                await self._conversation_service.complete_run(
                    user_id=user_id,
                    session_id=run_start.session_id,
                    run_id=run_start.run_id,
                    route="payment",
                    result=dto,
                )
            await record_agent_audit(
                audit_writer=self._audit_service,
                audit_context=audit_context,
                result=dto,
                metadata={"flow": "react"},
            )
            return dto
        except AppException as exc:
            if self._conversation_service is not None and run_start is not None:
                await self._conversation_service.fail_run(
                    user_id=user_id,
                    session_id=run_start.session_id,
                    run_id=run_start.run_id,
                    error_code=str(exc.error.code),
                    error_message=exc.message,
                )
            await record_agent_audit(
                audit_writer=self._audit_service,
                audit_context=audit_context,
                result=failed_agent_result(
                    run_id=run_start.run_id if run_start is not None else uuid6_unique_str_id(),
                    error_type=f"AppException:{exc.error.code}",
                    message=exc.message,
                ),
            )
            raise
        except Exception as exc:
            logger.warning(f"Payment support agent failed: {type(exc).__name__}")
            if self._conversation_service is not None and run_start is not None:
                await self._conversation_service.fail_run(
                    user_id=user_id,
                    session_id=run_start.session_id,
                    run_id=run_start.run_id,
                    error_code=type(exc).__name__,
                    error_message="支付支持 Agent 暂不可用",
                )
            await record_agent_audit(
                audit_writer=self._audit_service,
                audit_context=audit_context,
                result=failed_agent_result(
                    run_id=run_start.run_id if run_start is not None else uuid6_unique_str_id(),
                    error_type=type(exc).__name__,
                ),
            )
            raise AppException(
                errors.ServiceUnavailable, message="支付支持 Agent 暂不可用"
            ) from exc

    async def stream_payment_support_question(
        self,
        *,
        user_id: int,
        question: str,
        max_steps: int = 4,
        session_id: str | None = None,
        confirmation_token: str | None = None,
        idempotency_key: str | None = None,
    ) -> AsyncIterator[AgentStreamEventDTO]:
        """流式回答支付支持问题。"""
        audit_context = AgentAuditContext.start(
            agent_name="payment_support",
            user_id=user_id,
            user_input=question,
            max_steps=max_steps,
        )
        run_id: str | None = None
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
                rag_service=self._rag_service,
                user_id=user_id,
                max_steps=max_steps,
            ).build()
            async for run_event in agent.run_events(user_input=question):
                run_id = run_event.run_id
                stream_event = stream_event_from_run_event(run_event)
                if stream_event.result is not None:
                    await record_agent_audit(
                        audit_writer=self._audit_service,
                        audit_context=audit_context,
                        result=stream_event.result,
                        metadata={"flow": "react"},
                    )
                yield stream_event
        except AppException as exc:
            await record_agent_audit(
                audit_writer=self._audit_service,
                audit_context=audit_context,
                result=failed_agent_result(
                    run_id=run_id or uuid6_unique_str_id(),
                    error_type=f"AppException:{exc.error.code}",
                    message=exc.message,
                ),
            )
            yield app_exception_stream_event(exc, run_id=run_id)
        except Exception as exc:
            logger.warning(f"Payment support agent stream failed: {type(exc).__name__}")
            await record_agent_audit(
                audit_writer=self._audit_service,
                audit_context=audit_context,
                result=failed_agent_result(
                    run_id=run_id or uuid6_unique_str_id(),
                    error_type=type(exc).__name__,
                ),
            )
            yield service_unavailable_stream_event(
                detail="支付支持 Agent 暂不可用",
                run_id=run_id,
            )


_payment_agent_service: PaymentAgentService | None = None


def new_payment_agent_service() -> PaymentAgentService:
    """依赖注入：获取 PaymentAgentService 单例。"""
    global _payment_agent_service
    if _payment_agent_service is None:
        _payment_agent_service = PaymentAgentService(
            llm_client=new_default_llm_client(),
            rag_service=new_rag_service(),
            audit_service=new_agent_audit_service(),
            conversation_service=new_agent_conversation_service(),
        )
    return _payment_agent_service
