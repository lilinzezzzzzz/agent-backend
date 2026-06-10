from collections.abc import AsyncIterator

from internal.agents.router import AgentRoute, HybridAgentRouter
from internal.cache import AgentActionCache, new_agent_action_cache
from internal.core import AppException, errors
from internal.infra.llm import OpenAIClient, new_default_llm_client
from internal.services.agents.audit import (
    AgentAuditContext,
    AgentAuditService,
    AuditedAgentLLMClient,
    failed_agent_result,
    new_agent_audit_service,
    record_agent_audit,
)
from internal.services.agents.confirmation import (
    resolve_agent_confirmation_context,
)
from internal.services.agents.conversation import (
    AgentConversationService,
    new_agent_conversation_service,
)
from internal.services.agents.order import OrderAgentService, new_order_agent_service
from internal.services.agents.payment import (
    PaymentAgentService,
    new_payment_agent_service,
)
from internal.services.agents.stream import (
    app_exception_stream_event,
    service_unavailable_stream_event,
    stream_events_from_result,
)
from internal.services.dto.agent import (
    AgentChatDTO,
    AgentRunResultDTO,
    AgentStreamEventDTO,
    AgentStreamEventName,
)
from pkg.logger import logger
from pkg.toolkit import context
from pkg.toolkit.string import uuid6_unique_str_id


class AgentRouterService:
    """将统一聊天请求路由到受支持的专业 Agent。"""

    def __init__(
        self,
        *,
        llm_client: OpenAIClient,
        order_agent_service: OrderAgentService,
        payment_agent_service: PaymentAgentService,
        audit_service: AgentAuditService,
        action_store: AgentActionCache,
        conversation_service: AgentConversationService | None = None,
    ):
        self._llm_client = llm_client
        self._order_agent_service = order_agent_service
        self._payment_agent_service = payment_agent_service
        self._audit_service = audit_service
        self._action_store = action_store
        self._conversation_service = conversation_service

    async def chat(
        self,
        *,
        user_id: int,
        question: str,
        max_steps: int = 4,
        session_id: str | None = None,
        confirmation_token: str | None = None,
        idempotency_key: str | None = None,
    ) -> AgentChatDTO:
        """路由统一聊天请求并返回专业 Agent 结果。

        这里的 Router Service 只负责“选路 + 汇总审计”，不直接执行订单/支付
        ReAct 逻辑：

        1. 如果请求携带 confirmation_token，说明这是服务端已签发的副作用确认请求。
           Router 会先从服务端 pending action 解析可信 route，避免再让 LLM
           参与路由或重新解释用户问题；route 不能由客户端请求体决定。
        2. 普通请求先调用 HybridAgentRouter 判定业务域，再委托给对应专业 Agent。
           专业 Agent 自己负责创建 session/run、执行 ReAct、写入 message/step。
        3. unsupported 不会进入任何专业 Agent，所以需要在本方法内自己创建并完成
           agent_chat run，保证 `/v1/agent/chat` 也能返回并保存 session_id/run_id。
        4. 本方法会为 Router 层写一条 agent_chat 审计；专业 Agent 还会写自己的
           order_support/payment_support 审计。这两类审计语义不同，不是重复写入。
        """
        run_start = None
        audit_context = AgentAuditContext.start(
            agent_name="agent_chat",
            user_id=user_id,
            user_input=question,
            max_steps=max_steps,
        )
        try:
            # 确认请求必须走确定性路径。这里不能让 LLM Router 重新判定路由，
            # 也不能相信客户端传 route；业务域必须来自服务端保存的 pending action。
            if confirmation_token is not None:
                confirmation_context = await resolve_agent_confirmation_context(
                    action_store=self._action_store,
                    user_id=user_id,
                    confirmation_token=confirmation_token,
                )
                route = confirmation_context.route
            else:
                # 普通请求先由 HybridAgentRouter 做业务域判定；路由阶段的 LLM 调用
                # 通过 AuditedAgentLLMClient 写入当前 agent_chat 审计上下文。
                router_agent = HybridAgentRouter(
                    llm_client=AuditedAgentLLMClient(
                        llm_client=self._llm_client,
                        audit_context=audit_context,
                    )
                )
                route = await router_agent.route(question=question)

            # 命中订单域后，把 session_id、确认 token 和幂等键原样透传给订单 Agent。
            # 订单 Agent 会负责自己的会话存储、权限校验、ReAct 执行和确认路径。
            if route is AgentRoute.ORDER:
                result = await self._order_agent_service.answer_order_support_question(
                    user_id=user_id,
                    question=question,
                    max_steps=max_steps,
                    session_id=session_id,
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

            # 支付域同理只做转发；支付 Agent 当前不支持 confirmation_token，
            # 如果调用方错误传入，会在支付 Agent 内转换为 BadRequest。
            if route is AgentRoute.PAYMENT:
                result = (
                    await self._payment_agent_service.answer_payment_support_question(
                        user_id=user_id,
                        question=question,
                        max_steps=max_steps,
                        session_id=session_id,
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

            # unsupported 没有下游专业 Agent 可委托，因此由 Router 自己补齐存储闭环：
            # start_run 写入 user message/running run，final 构造固定回复，
            # complete_run 写入 assistant message 并把 run 标记为 completed。
            if self._conversation_service is not None:
                run_start = await self._conversation_service.start_run(
                    user_id=user_id,
                    session_id=session_id,
                    entrypoint="agent_chat",
                    agent_name="agent_chat",
                    question=question,
                    max_steps=max_steps,
                    trace_id=context.get_trace_id(),
                )
            result = AgentRunResultDTO.final(
                run_id=run_start.run_id if run_start is not None else None,
                session_id=run_start.session_id if run_start is not None else None,
                answer="当前仅支持订单、物流、售后、退款、发票和支付相关问题。",
            )
            if self._conversation_service is not None and run_start is not None:
                await self._conversation_service.complete_run(
                    user_id=user_id,
                    session_id=run_start.session_id,
                    run_id=run_start.run_id,
                    route=AgentRoute.UNSUPPORTED.value,
                    result=result,
                )
            await record_agent_audit(
                audit_writer=self._audit_service,
                audit_context=audit_context,
                result=result,
                metadata={"route": AgentRoute.UNSUPPORTED.value},
            )
            return AgentChatDTO(route=AgentRoute.UNSUPPORTED.value, result=result)
        except AppException as exc:
            # 只有 Router 自己创建过 run_start 的路径才需要在这里 fail_run。
            # 订单/支付路径的 run 由专业 Agent 自己创建，也由专业 Agent 自己失败补偿。
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
                    run_id=(
                        run_start.run_id
                        if run_start is not None
                        else uuid6_unique_str_id()
                    ),
                    error_type=f"AppException:{exc.error.code}",
                    message=exc.message,
                ),
            )
            raise
        except Exception as exc:
            logger.warning(f"Agent router failed: {type(exc).__name__}")
            # 非业务异常统一映射为 ServiceUnavailable；同时如果 unsupported 路径
            # 已经创建 run，需要把 running run 收敛到 failed，避免产生悬挂状态。
            if self._conversation_service is not None and run_start is not None:
                await self._conversation_service.fail_run(
                    user_id=user_id,
                    session_id=run_start.session_id,
                    run_id=run_start.run_id,
                    error_code=type(exc).__name__,
                    error_message="Agent Router 暂不可用",
                )
            await record_agent_audit(
                audit_writer=self._audit_service,
                audit_context=audit_context,
                result=failed_agent_result(
                    run_id=(
                        run_start.run_id
                        if run_start is not None
                        else uuid6_unique_str_id()
                    ),
                    error_type=type(exc).__name__,
                ),
            )
            raise AppException(
                errors.ServiceUnavailable, message="Agent Router 暂不可用"
            ) from exc

    async def stream_chat(
        self,
        *,
        user_id: int,
        question: str,
        max_steps: int = 4,
        session_id: str | None = None,
        confirmation_token: str | None = None,
        idempotency_key: str | None = None,
    ) -> AsyncIterator[AgentStreamEventDTO]:
        """流式路由统一聊天请求并转发专业 Agent 事件。"""
        audit_context = AgentAuditContext.start(
            agent_name="agent_chat",
            user_id=user_id,
            user_input=question,
            max_steps=max_steps,
        )
        route: AgentRoute | None = None
        run_id: str | None = None
        try:
            if confirmation_token is not None:
                confirmation_context = await resolve_agent_confirmation_context(
                    action_store=self._action_store,
                    user_id=user_id,
                    confirmation_token=confirmation_token,
                )
                route = confirmation_context.route
            else:
                router_agent = HybridAgentRouter(
                    llm_client=AuditedAgentLLMClient(
                        llm_client=self._llm_client,
                        audit_context=audit_context,
                    )
                )
                route = await router_agent.route(question=question)

            yield AgentStreamEventDTO.route(route=route.value)

            if route is AgentRoute.ORDER:
                async for (
                    event
                ) in self._order_agent_service.stream_order_support_question(
                    user_id=user_id,
                    question=question,
                    max_steps=max_steps,
                    session_id=session_id,
                    confirmation_token=confirmation_token,
                    idempotency_key=idempotency_key,
                ):
                    routed_event = event.with_route(route.value)
                    run_id = _event_run_id(routed_event) or run_id
                    if routed_event.result is not None:
                        await record_agent_audit(
                            audit_writer=self._audit_service,
                            audit_context=audit_context,
                            result=routed_event.result,
                            metadata={"route": route.value},
                        )
                    elif routed_event.event is AgentStreamEventName.ERROR:
                        await record_agent_audit(
                            audit_writer=self._audit_service,
                            audit_context=audit_context,
                            result=failed_agent_result(
                                run_id=run_id or uuid6_unique_str_id(),
                                error_type="StreamError",
                                message=str(routed_event.data.get("message") or ""),
                            ),
                        )
                    yield routed_event
                return

            if route is AgentRoute.PAYMENT:
                async for (
                    event
                ) in self._payment_agent_service.stream_payment_support_question(
                    user_id=user_id,
                    question=question,
                    max_steps=max_steps,
                    session_id=session_id,
                    confirmation_token=confirmation_token,
                    idempotency_key=idempotency_key,
                ):
                    routed_event = event.with_route(route.value)
                    run_id = _event_run_id(routed_event) or run_id
                    if routed_event.result is not None:
                        await record_agent_audit(
                            audit_writer=self._audit_service,
                            audit_context=audit_context,
                            result=routed_event.result,
                            metadata={"route": route.value},
                        )
                    elif routed_event.event is AgentStreamEventName.ERROR:
                        await record_agent_audit(
                            audit_writer=self._audit_service,
                            audit_context=audit_context,
                            result=failed_agent_result(
                                run_id=run_id or uuid6_unique_str_id(),
                                error_type="StreamError",
                                message=str(routed_event.data.get("message") or ""),
                            ),
                        )
                    yield routed_event
                return

            result = AgentRunResultDTO.final(
                answer="当前仅支持订单、物流、售后、退款、发票和支付相关问题。"
            )
            run_id = result.run_id
            await record_agent_audit(
                audit_writer=self._audit_service,
                audit_context=audit_context,
                result=result,
                metadata={"route": AgentRoute.UNSUPPORTED.value},
            )
            for event in stream_events_from_result(result):
                yield event.with_route(AgentRoute.UNSUPPORTED.value)
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
            yield app_exception_stream_event(
                exc,
                run_id=run_id,
                route=route.value if route is not None else None,
            )
        except Exception as exc:
            logger.warning(f"Agent router stream failed: {type(exc).__name__}")
            await record_agent_audit(
                audit_writer=self._audit_service,
                audit_context=audit_context,
                result=failed_agent_result(
                    run_id=run_id or uuid6_unique_str_id(),
                    error_type=type(exc).__name__,
                ),
            )
            yield service_unavailable_stream_event(
                detail="Agent Router 暂不可用",
                run_id=run_id,
                route=route.value if route is not None else None,
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
            audit_service=new_agent_audit_service(),
            action_store=new_agent_action_cache(),
            conversation_service=new_agent_conversation_service(),
        )
    return _agent_router_service


def _event_run_id(event: AgentStreamEventDTO) -> str | None:
    run_id = event.data.get("run_id")
    return run_id if isinstance(run_id, str) else None
