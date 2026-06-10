from __future__ import annotations

from collections.abc import AsyncIterator

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
from internal.services.agents.conversation import (
    AgentConversationService,
    new_agent_conversation_service,
)
from internal.services.agents.stream import (
    app_exception_stream_event,
    service_unavailable_stream_event,
    stream_event_from_run_event,
    stream_events_from_result,
)
from internal.services.dto.agent import AgentRunResultDTO, AgentStreamEventDTO
from internal.services.order import OrderService, new_order_service
from internal.services.rag import RagService, new_rag_service
from pkg.logger import logger
from pkg.toolkit import context
from pkg.toolkit.string import uuid6_unique_str_id


class OrderAgentService:
    def __init__(
        self,
        *,
        llm_client: AgentLLMClient,
        order_service: OrderService,
        rag_service: RagService,
        audit_service: AgentAuditWriter,
        conversation_service: AgentConversationService | None = None,
    ):
        self._llm_client = llm_client
        self._order_service = order_service
        self._rag_service = rag_service
        self._audit_service = audit_service
        self._conversation_service = conversation_service

    async def answer_order_support_question(
        self,
        *,
        user_id: int,
        question: str,
        max_steps: int = 4,
        session_id: str | None = None,
        confirmation_token: str | None = None,
        idempotency_key: str | None = None,
    ) -> AgentRunResultDTO:
        """回答订单支持问题，并返回一次完整的 Agent 运行结果。

        该方法覆盖两个业务入口：
        1. `confirmation_token` 存在时，不再调用 LLM，而是执行上一次 Agent 提示用户确认的发票申请动作。
        2. 普通问答时，构造订单支持 ReActAgent，让 Agent 根据订单工具和 RAG 知识库完成多步推理。

        同步接口会在可用时写入会话运行记录，便于后续问题带上历史上下文；无论成功、业务异常还是
        未预期异常，都会尝试写入 Agent 审计记录。
        """
        run_start = None
        # 审计上下文在任何业务分支之前创建，确保确认流、ReAct 流和失败流使用同一组基础元数据。
        audit_context = AgentAuditContext.start(
            agent_name="order_support",
            user_id=user_id,
            user_input=question,
            max_steps=max_steps,
        )
        try:
            # 会话服务是可选依赖：存在时先登记本次 run，后续 result / failure 都绑定这个 run_id。
            if self._conversation_service is not None:
                run_start = await self._conversation_service.start_run(
                    user_id=user_id,
                    session_id=session_id,
                    entrypoint="order_support",
                    agent_name="order_support",
                    question=question,
                    max_steps=max_steps,
                    trace_id=context.get_trace_id(),
                )

            # 确认流处理用户对上一次 Agent 建议动作的确认。该分支会产生副作用，因此必须携带幂等键。
            if confirmation_token is not None:
                if idempotency_key is None:
                    raise AppException(errors.BadRequest, message="确认动作缺少幂等键")
                confirmed = await self._order_service.confirm_invoice_request(
                    user_id=user_id,
                    confirmation_token=confirmation_token,
                    idempotency_key=idempotency_key,
                )
                confirmation_result = AgentRunResultDTO.from_confirmed_invoice(
                    confirmed,
                    run_id=run_start.run_id if run_start is not None else None,
                    session_id=run_start.session_id if run_start is not None else None,
                )
                # 将确认结果写入会话历史，保证后续同一 session 的追问能看到确认动作已经完成。
                if self._conversation_service is not None and run_start is not None:
                    await self._conversation_service.complete_run(
                        user_id=user_id,
                        session_id=run_start.session_id,
                        run_id=run_start.run_id,
                        route="order",
                        result=confirmation_result,
                    )
                await record_agent_audit(
                    audit_writer=self._audit_service,
                    audit_context=audit_context,
                    result=confirmation_result,
                    metadata={"flow": "confirmation"},
                )
                return confirmation_result

            # 普通 ReAct 问答会读取最近会话消息，并排除当前刚创建的 run，避免把未完成问题重复注入提示词。
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

            # AuditedAgentLLMClient 包装底层 LLM client，用于把模型调用过程沉淀到同一个审计上下文中。
            agent = OrderAgentBuilder(
                llm_client=AuditedAgentLLMClient(
                    llm_client=self._llm_client,
                    audit_context=audit_context,
                ),
                order_service=self._order_service,
                rag_service=self._rag_service,
                user_id=user_id,
                max_steps=max_steps,
                session_context=session_context,
            ).build()
            # ReActAgent 内部负责工具调用和推理步数控制；Service 层只负责编排输入、上下文和结果落库。
            run_result = await agent.run(
                user_input=question,
                run_id=run_start.run_id if run_start is not None else None,
            )
            dto = AgentRunResultDTO.from_agent_result(
                run_result,
                session_id=run_start.session_id if run_start is not None else None,
            )
            # 完成会话 run 后再记录审计结果，避免会话状态成功但审计内容缺少最终 DTO。
            if self._conversation_service is not None and run_start is not None:
                await self._conversation_service.complete_run(
                    user_id=user_id,
                    session_id=run_start.session_id,
                    run_id=run_start.run_id,
                    route="order",
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
            # 业务异常保持原错误码向上抛出；这里补齐会话失败状态和审计失败结果。
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
            # 未预期异常不向 API 暴露内部类型或堆栈；日志和审计只记录可定位但不泄密的错误类型。
            logger.warning(f"Order support agent failed: {type(exc).__name__}")
            if self._conversation_service is not None and run_start is not None:
                await self._conversation_service.fail_run(
                    user_id=user_id,
                    session_id=run_start.session_id,
                    run_id=run_start.run_id,
                    error_code=type(exc).__name__,
                    error_message="订单支持 Agent 暂不可用",
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
                errors.ServiceUnavailable, message="订单支持 Agent 暂不可用"
            ) from exc

    async def stream_order_support_question(
        self,
        *,
        user_id: int,
        question: str,
        max_steps: int = 4,
        session_id: str | None = None,
        confirmation_token: str | None = None,
        idempotency_key: str | None = None,
    ) -> AsyncIterator[AgentStreamEventDTO]:
        """流式回答订单支持问题。

        该方法把确认流或 ReActAgent 的运行事件转换为 `AgentStreamEventDTO`：
        - 确认流会立即执行确认动作，并把完整结果拆成流式事件返回。
        - 普通问答会逐个转发 Agent 的 step / tool / final 事件，便于 Controller 以 SSE 等方式输出。

        与同步接口不同，当前流式路径不创建会话 run，也不会加载 session 历史；`session_id` 保留在签名中，
        用于与非流式接口保持调用契约一致或后续补齐会话持久化。
        """
        # 流式响应不能在异常时直接抛给调用方，否则客户端可能拿不到结构化错误事件；因此失败会转成 event。
        audit_context = AgentAuditContext.start(
            agent_name="order_support",
            user_id=user_id,
            user_input=question,
            max_steps=max_steps,
        )
        # run_id 会在确认结果或 Agent 首个事件出现后确定；异常发生太早时使用临时 ID 写审计。
        run_id: str | None = None
        try:
            # 确认流同样有真实业务副作用，必须要求调用方提供幂等键，避免客户端重试造成重复确认。
            if confirmation_token is not None:
                if idempotency_key is None:
                    raise AppException(errors.BadRequest, message="确认动作缺少幂等键")
                confirmed = await self._order_service.confirm_invoice_request(
                    user_id=user_id,
                    confirmation_token=confirmation_token,
                    idempotency_key=idempotency_key,
                )
                confirmation_result = AgentRunResultDTO.from_confirmed_invoice(confirmed)
                run_id = confirmation_result.run_id
                await record_agent_audit(
                    audit_writer=self._audit_service,
                    audit_context=audit_context,
                    result=confirmation_result,
                    metadata={"flow": "confirmation"},
                )
                # 将一次性确认结果转换为与 ReAct final result 一致的事件序列，保持前端消费模型统一。
                for event in stream_events_from_result(confirmation_result):
                    yield event
                return

            # 流式 ReAct 不等待完整结果，Agent 每产生一个运行事件就立刻转换并向上游 yield。
            agent = OrderAgentBuilder(
                llm_client=AuditedAgentLLMClient(
                    llm_client=self._llm_client,
                    audit_context=audit_context,
                ),
                order_service=self._order_service,
                rag_service=self._rag_service,
                user_id=user_id,
                max_steps=max_steps,
            ).build()
            async for run_event in agent.run_events(user_input=question):
                run_id = run_event.run_id
                stream_event = stream_event_from_run_event(run_event)
                # 只有 final 事件携带完整 result；审计结果在这里记录一次，避免对中间事件重复落审计。
                if stream_event.result is not None:
                    await record_agent_audit(
                        audit_writer=self._audit_service,
                        audit_context=audit_context,
                        result=stream_event.result,
                        metadata={"flow": "react"},
                    )
                yield stream_event
        except AppException as exc:
            # 业务异常转成结构化错误事件继续 yield，让流式客户端按正常事件协议收尾。
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
            # 未预期异常统一降级为 ServiceUnavailable 事件，避免泄漏内部异常细节。
            logger.warning(f"Order support agent stream failed: {type(exc).__name__}")
            await record_agent_audit(
                audit_writer=self._audit_service,
                audit_context=audit_context,
                result=failed_agent_result(
                    run_id=run_id or uuid6_unique_str_id(),
                    error_type=type(exc).__name__,
                ),
            )
            yield service_unavailable_stream_event(
                detail="订单支持 Agent 暂不可用",
                run_id=run_id,
            )


_order_agent_service: OrderAgentService | None = None


def new_order_agent_service() -> OrderAgentService:
    """依赖注入：获取 OrderAgentService 单例。"""
    global _order_agent_service
    if _order_agent_service is None:
        _order_agent_service = OrderAgentService(
            llm_client=new_default_llm_client(),
            order_service=new_order_service(),
            rag_service=new_rag_service(),
            audit_service=new_agent_audit_service(),
            conversation_service=new_agent_conversation_service(),
        )
    return _order_agent_service
