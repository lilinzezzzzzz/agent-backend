"""受管理 Agent run 的共享执行编排。

订单、支付和统一路由共用同一份 claim / lease / checkpoint / 恢复逻辑：

- `AgentExecutionService` 负责用户授权、claim、恢复校验、Builder 选择、请求生命周期
  和 DTO 组装；
- `DatabaseAgentRunRuntime` 把存储后端适配成 `pkg.agents.AgentRunRuntime`，持有本次
  attempt 的 fencing token 并校验 revision；
- 业务 Builder 只负责 prompt、工具装配和步数上限，不感知 lease 或 checkpoint；
- Controller 只做输入校验和响应转换，SSE 适配集中在 `stream.py`。

执行请求的取消被转换成打断意图：执行 scope 与响应发送 scope 分离但共同归属本次请求，
工具等待与持久化收尾使用带期限的 shield，慢客户端或断连都不会破坏已提交的现场。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from functools import cache
from hashlib import sha256
from typing import Any
from uuid import UUID

import anyio

from internal.agents.registry import (
    AGENT_CHAT_ROUTER,
    SUPPORTED_AGENT_NAMES,
    AgentDefinitionRegistry,
)
from internal.agents.router import AgentRoute, HybridAgentRouter
from internal.cache import AgentActionCache, new_agent_action_cache
from internal.config import settings
from internal.core import AppException, errors
from internal.infra.llm import OpenAIResponsesClient, new_default_llm_client
from internal.schemas.agent import (
    AgentRunClaimDTO,
    AgentRunCommitDTO,
    AgentRunCreateDTO,
    AgentRunEventContextDTO,
    AgentRunInterruptDTO,
    AgentRunResultDTO,
    AgentRunResumeDTO,
    AgentRunStateDTO,
    AgentRunViewDTO,
    AgentStepDTO,
    AgentStreamEventDTO,
    run_view_to_schema,
)
from internal.services.agents.audit import (
    AgentAuditContext,
    AgentAuditService,
    AuditedAgentLLMClient,
    new_agent_audit_service,
    record_agent_audit,
)
from internal.services.agents.conversation import (
    DatabaseAgentStorageBackend,
    MANAGED_RUN_INTERRUPTED_STATUS,
    MANAGED_RUN_RECOVERY_REQUIRED_STATUS,
    new_database_agent_storage_backend,
)
from internal.services.agents.stream import (
    app_exception_stream_event,
    managed_stream_event_from_run_event,
)
from internal.services.order import new_order_service
from internal.services.rag import new_rag_service
from pkg import request_context as context
from pkg.agents import (
    AgentCheckpoint,
    AgentCheckpointCommit,
    AgentControlState,
    AgentControlStatus,
    AgentControlStopReason,
    AgentRunEvent,
    AgentRunEventType,
    AgentRunPhase,
    AgentRunResult,
    AgentRunStatus,
    AgentStepRecord,
    CheckpointError,
    ReActAgent,
    ToolReplayPolicy,
    UnsafeAgentResumeError,
    UnknownToolError,
    checkpoint_step_to_record,
    stable_tool_call_id,
    step_record_to_checkpoint_step,
)
from pkg.ids import uuid7_unique_str_id
from pkg.logger import logger

UNSUPPORTED_ANSWER = "当前仅支持订单、物流、售后、退款、发票和支付相关问题。"
"""统一入口无法可靠路由时的确定性降级回答。"""

ORDER_SUPPORT_ROUTE = "order"
PAYMENT_SUPPORT_ROUTE = "payment"

_ROUTE_AGENT_NAMES: dict[AgentRoute, str] = {
    AgentRoute.ORDER: "order_support",
    AgentRoute.PAYMENT: "payment_support",
}


@dataclass(frozen=True, slots=True)
class AgentExecutionLimits:
    """受管理执行的时限与通道配置。

    初始值只是待验证默认：lease 30 秒、heartbeat/control poll 5 秒、取消收尾 70 秒。
    配置层必须校验 `control_poll_seconds < lease_seconds`，并保证取消收尾预算覆盖当前
    工具的剩余超时和提交预算。
    """

    lease_seconds: int = 30
    control_poll_seconds: float = 5.0
    cancel_grace_seconds: float = 70.0
    stream_buffer_size: int = 16


class _ManagedRunControl:
    """执行 scope 与响应发送 scope 之间共享的本地控制状态。"""

    def __init__(self) -> None:
        self._done = anyio.Event()
        self.stop_requested = False
        self.slow_client = False
        self.error: BaseException | None = None
        self.result: AgentRunResult | None = None
        self.execution_scope: anyio.CancelScope | None = None
        self.shutdown_deadline: float | None = None

    def start_shutdown(self, grace_seconds: float) -> None:
        """保护当前调用，同时为状态提交预留总收尾预算的五分之一。"""
        self.request_stop()
        if self.shutdown_deadline is None:
            now = anyio.current_time()
            self.shutdown_deadline = now + grace_seconds
            if self.execution_scope is not None:
                self.execution_scope.deadline = now + grace_seconds * 0.8

    def request_stop(self) -> None:
        """请求停止推送事件；执行者仍会走到当前安全点。"""
        self.stop_requested = True

    def mark_slow_client(self) -> None:
        """标记慢客户端；调用方据此提交打断意图。"""
        self.slow_client = True
        self.stop_requested = True

    def mark_done(self) -> None:
        """执行者已完成收尾。"""
        self._done.set()

    async def wait_done(self) -> None:
        """等待执行者收尾；调用方必须用带期限的 shield 包裹。"""
        await self._done.wait()


class DatabaseAgentRunRuntime:
    """把存储后端适配为 `AgentRunRuntime`，并持有本次 attempt 的 fencing token。"""

    def __init__(
        self,
        *,
        storage: DatabaseAgentStorageBackend,
        user_id: UUID,
        run_id: str,
        lease_token: str,
        checkpoint: AgentCheckpoint,
    ):
        self._storage = storage
        self._user_id = user_id
        self._run_id = run_id
        self._lease_token = lease_token
        self._checkpoint = checkpoint
        self._revision = checkpoint.revision

    @property
    def checkpoint(self) -> AgentCheckpoint:
        """最后一次提交的 checkpoint。"""
        return self._checkpoint

    async def load_checkpoint(self) -> AgentCheckpoint | None:
        """返回本次 attempt 当前的执行现场。"""
        return self._checkpoint

    async def load_control_state(self) -> AgentControlState:
        """读取打断意图、lease 归属与 checkpoint 版本。"""
        control = await self._storage.read_managed_control_state(
            user_id=self._user_id,
            run_id=self._run_id,
            lease_token=self._lease_token,
        )
        return AgentControlState(
            status=AgentControlStatus(control.status),
            revision=control.revision,
            attempt_no=control.attempt_no,
            interrupt_requested=control.interrupt_requested,
            lease_owned=control.lease_owned,
            stop_reason=(
                None if control.lease_owned else AgentControlStopReason.LEASE_LOST
            ),
        )

    async def commit_checkpoint(
        self,
        *,
        checkpoint: AgentCheckpoint,
        pause_when_interrupted: bool = False,
    ) -> AgentCheckpointCommit:
        """提交新的阶段/待执行动作；存在打断请求时保留上一个安全现场。"""
        commit = await self._storage.commit_managed_checkpoint(
            user_id=self._user_id,
            run_id=self._run_id,
            lease_token=self._lease_token,
            expected_revision=self._revision,
            committed_checkpoint=checkpoint,
            pause_checkpoint=(
                self._checkpoint.next_revision()
                if checkpoint.phase is AgentRunPhase.TOOL_IN_FLIGHT
                else checkpoint
            ),
            pause_when_interrupted=pause_when_interrupted,
        )
        return self._apply(commit)

    async def commit_step(
        self,
        *,
        step: AgentStepRecord,
        checkpoint: AgentCheckpoint,
        pause_when_interrupted: bool = False,
    ) -> AgentCheckpointCommit:
        """在同一事务中提交已完成步骤与更新后的运行现场。"""
        committed = self._with_step(checkpoint, step)
        commit = await self._storage.commit_managed_step(
            user_id=self._user_id,
            run_id=self._run_id,
            lease_token=self._lease_token,
            expected_revision=self._revision,
            committed_checkpoint=committed,
            pause_checkpoint=committed,
            step=AgentStepDTO.from_step_record(step),
            pause_when_interrupted=pause_when_interrupted,
        )
        return self._apply(commit)

    async def commit_terminal(
        self,
        *,
        checkpoint: AgentCheckpoint,
        result: AgentRunResult,
        pause_when_interrupted: bool = False,
    ) -> AgentCheckpointCommit:
        """提交终态、final step 与唯一 assistant 消息。"""
        committed = self._with_result_steps(checkpoint, result)
        commit = await self._storage.commit_managed_terminal(
            user_id=self._user_id,
            run_id=self._run_id,
            lease_token=self._lease_token,
            expected_revision=self._revision,
            committed_checkpoint=committed,
            pause_checkpoint=checkpoint,
            terminal_status=result.status.value,
            result=AgentRunResultDTO.from_agent_result(result),
            pause_when_interrupted=pause_when_interrupted,
        )
        return self._apply(commit)

    def _apply(self, commit: AgentRunCommitDTO) -> AgentCheckpointCommit:
        """更新本地 revision 并转换停止原因。"""
        self._revision = commit.revision
        self._checkpoint = commit.checkpoint
        return AgentCheckpointCommit(
            checkpoint=commit.checkpoint,
            paused=commit.paused,
            stop_reason=(
                AgentControlStopReason.INTERRUPT_REQUESTED if commit.paused else None
            ),
        )

    def _with_step(
        self, checkpoint: AgentCheckpoint, step: AgentStepRecord
    ) -> AgentCheckpoint:
        """把新完成的步骤追加进 checkpoint 快照。"""
        if step.index < len(checkpoint.steps):
            return checkpoint
        steps = checkpoint.steps + (
            step_record_to_checkpoint_step(
                step,
                tool_call_id=stable_tool_call_id(
                    run_id=self._run_id, step_index=step.index
                ),
            ),
        )
        return replace(checkpoint, steps=steps, next_step_index=len(steps))

    def _with_result_steps(
        self, checkpoint: AgentCheckpoint, result: AgentRunResult
    ) -> AgentCheckpoint:
        """把结果中尚未提交的步骤追加进 checkpoint 快照。"""
        steps = checkpoint.steps
        for step in result.steps:
            if step.index < len(steps):
                continue
            steps = steps + (
                step_record_to_checkpoint_step(
                    step,
                    tool_call_id=stable_tool_call_id(
                        run_id=self._run_id, step_index=step.index
                    ),
                ),
            )
        if len(steps) == len(checkpoint.steps):
            return checkpoint
        return replace(checkpoint, steps=steps, next_step_index=len(steps))


class AgentExecutionService:
    """受管理 run 的创建、查询、打断与恢复用例服务。"""

    def __init__(
        self,
        *,
        storage: DatabaseAgentStorageBackend,
        definitions: AgentDefinitionRegistry,
        llm_client: OpenAIResponsesClient,
        audit_service: AgentAuditService,
        action_store: AgentActionCache,
        limits: AgentExecutionLimits | None = None,
    ):
        self._storage = storage
        self._definitions = definitions
        self._llm_client = llm_client
        self._audit_service = audit_service
        self._action_store = action_store
        self._limits = limits or AgentExecutionLimits()

    # ==========================================================================
    # 创建 / 查询 / 打断
    # ==========================================================================

    async def create_run(
        self,
        *,
        user_id: UUID,
        entrypoint: str,
        question: str,
        session_id: str | None,
        max_steps: int,
        request_key: str,
    ) -> AgentRunCreateDTO:
        """创建受管理 run 并冻结初始执行现场；不调用模型。"""
        agent_name, route, phase = _entrypoint_definition(entrypoint)
        run_id = uuid7_unique_str_id()
        resolved_session_id = session_id or uuid7_unique_str_id()
        user_message_id = uuid7_unique_str_id()

        session_context: dict[str, Any] = {}
        if session_id is not None:
            conversation_context = await self._storage.load_context(
                user_id=user_id,
                session_id=session_id,
                max_recent_messages=20,
                max_recent_chars=12000,
            )
            session_context = conversation_context.to_prompt_context()

        checkpoint = AgentCheckpoint(
            run_id=run_id,
            phase=phase,
            max_steps=max_steps,
            user_input=question,
            definition_version=self._definitions.definition_version,
            next_step_index=0,
            session_context=session_context,
            route=route,
            agent_name=agent_name,
            model_config=self._current_model_config(),
            revision=0,
        )
        started = await self._storage.create_managed_run(
            user_id=user_id,
            run_id=run_id,
            session_id=resolved_session_id,
            user_message_id=user_message_id,
            entrypoint=entrypoint,
            agent_name=agent_name,
            question=question,
            max_steps=max_steps,
            trace_id=context.get_trace_id(),
            request_key=request_key,
            request_digest=_create_request_digest(
                entrypoint=entrypoint,
                question=question,
                session_id=session_id,
                max_steps=max_steps,
            ),
            execution_version=self._definitions.definition_version,
            checkpoint=checkpoint,
            requested_session_id=session_id,
            route=route,
        )
        return AgentRunCreateDTO(
            run_id=started.run_id,
            session_id=started.session_id,
            entrypoint=entrypoint,
            status="ready",
            max_steps=max_steps,
        )

    async def get_run(self, *, user_id: UUID, run_id: str) -> AgentRunViewDTO:
        """返回本用户可见的运行状态视图；不存在时统一 NotFound 防止枚举。"""
        state = await self._load_state(user_id=user_id, run_id=run_id)
        return AgentRunViewDTO.from_state(state)

    async def interrupt_run(
        self, *, user_id: UUID, run_id: str, reason: str | None
    ) -> AgentRunInterruptDTO:
        """提交打断意图；只承诺已受理，不承诺响应返回时已暂停。"""
        status = await self._storage.request_run_interrupt(
            user_id=user_id, run_id=run_id, reason=reason
        )
        return AgentRunInterruptDTO(
            run_id=run_id,
            status=status,
            accepted=status in ("interrupt_requested", MANAGED_RUN_INTERRUPTED_STATUS),
        )

    # ==========================================================================
    # 恢复执行
    # ==========================================================================

    async def resume_run(
        self, *, user_id: UUID, run_id: str, request_key: str
    ) -> AgentRunResumeDTO:
        """从 ready/interrupted 恢复执行到暂停或终态。"""
        claim = await self._claim(
            user_id=user_id, run_id=run_id, request_key=request_key
        )
        if claim.replayed or claim.status != "running":
            return await self._replayed_resume(user_id=user_id, claim=claim)

        control = _ManagedRunControl()
        audit_context = self._start_audit_context(user_id=user_id, claim=claim)
        async for _event in self._execute_managed_run(
            user_id=user_id, claim=claim, control=control
        ):
            pass
        if control.error is not None:
            raise control.error

        view = await self.get_run(user_id=user_id, run_id=run_id)
        result = (
            AgentRunResultDTO.from_agent_result(
                control.result, session_id=claim.session_id
            )
            if control.result is not None
            else _result_from_checkpoint(
                run_id=claim.run_id,
                session_id=claim.session_id,
                status=claim.status,
                checkpoint=claim.checkpoint,
            )
        )
        await self._record_audit(
            audit_context=audit_context, result=result, route=view.route
        )
        return AgentRunResumeDTO(run=view, result=result)

    async def resume_run_stream(
        self, *, user_id: UUID, run_id: str, request_key: str
    ) -> AsyncIterator[AgentStreamEventDTO]:
        """恢复执行并以事件流返回；重复请求只输出当前状态，不接管原流。"""
        try:
            async for event in self._resume_run_stream(
                user_id=user_id, run_id=run_id, request_key=request_key
            ):
                yield event
        except AppException as exc:
            yield app_exception_stream_event(exc, run_id=run_id)

    async def _resume_run_stream(
        self, *, user_id: UUID, run_id: str, request_key: str
    ) -> AsyncIterator[AgentStreamEventDTO]:
        claim = await self._claim(
            user_id=user_id, run_id=run_id, request_key=request_key
        )
        if claim.replayed or claim.status != "running":
            replayed = await self._replayed_resume(user_id=user_id, claim=claim)
            yield AgentStreamEventDTO.run_status(data=_run_status_fields(replayed))
            return

        control = _ManagedRunControl()
        audit_context = self._start_audit_context(user_id=user_id, claim=claim)
        event_context = AgentRunEventContextDTO(
            run_id=claim.run_id,
            session_id=claim.session_id,
            attempt_no=claim.attempt_no,
            checkpoint_revision=claim.checkpoint.revision,
            route=claim.checkpoint.route,
        )
        async for run_event in self._execute_managed_run(
            user_id=user_id, claim=claim, control=control
        ):
            event_context = replace(
                event_context, checkpoint_revision=run_event.checkpoint_revision
            )
            if run_event.result is not None:
                await self._record_audit(
                    audit_context=audit_context,
                    result=AgentRunResultDTO.from_agent_result(
                        run_event.result, session_id=claim.session_id
                    ),
                    route=claim.checkpoint.route,
                )
            yield managed_stream_event_from_run_event(run_event, context=event_context)

        if control.error is not None:
            yield app_exception_stream_event(
                control.error
                if isinstance(control.error, AppException)
                else AppException(
                    errors.ServiceUnavailable, message="Agent 受管理执行暂不可用"
                ),
                run_id=claim.run_id,
                route=claim.checkpoint.route,
            )

    # ==========================================================================
    # 内部编排
    # ==========================================================================

    def _start_audit_context(
        self, *, user_id: UUID, claim: AgentRunClaimDTO
    ) -> AgentAuditContext:
        """为一次受管理执行创建审计上下文。"""
        return AgentAuditContext.start(
            agent_name=claim.checkpoint.agent_name or AGENT_CHAT_ROUTER,
            user_id=user_id,
            user_input=claim.checkpoint.user_input,
            max_steps=claim.checkpoint.max_steps,
        )

    async def _replayed_resume(
        self, *, user_id: UUID, claim: AgentRunClaimDTO
    ) -> AgentRunResumeDTO:
        """重复 resume 只返回原 attempt 的当前状态或已保存结果。"""
        view = await self.get_run(user_id=user_id, run_id=claim.run_id)
        return AgentRunResumeDTO(
            run=view,
            result=_result_from_checkpoint(
                run_id=claim.run_id,
                session_id=claim.session_id,
                status="interrupted"
                if view.status == MANAGED_RUN_INTERRUPTED_STATUS
                else view.status,
                checkpoint=claim.checkpoint,
            ),
        )

    async def _load_state(self, *, user_id: UUID, run_id: str) -> AgentRunStateDTO:
        """读取受管理 run 状态。"""
        state = await self._storage.load_managed_run(
            user_id=user_id,
            run_id=run_id,
            resolve_stale_status=self._resolve_stale_status,
        )
        if state is None:
            raise AppException(errors.NotFound, message="Agent run 不存在")
        return state

    async def _claim(
        self, *, user_id: UUID, run_id: str, request_key: str
    ) -> AgentRunClaimDTO:
        """原子 claim；过期执行者按 checkpoint 判定后续状态。"""
        return await self._storage.claim_managed_run(
            user_id=user_id,
            run_id=run_id,
            request_key=request_key,
            request_digest=_resume_request_digest(run_id=run_id),
            trace_id=context.get_trace_id(),
            lease_seconds=self._limits.lease_seconds,
            resolve_stale_status=self._resolve_stale_status,
        )

    def _resolve_stale_status(self, state: AgentRunStateDTO) -> tuple[str, str | None]:
        """按最近阶段与工具重放策略判定过期现场能否继续（设计 §5.3）。"""
        if state.checkpoint is None:
            return (
                MANAGED_RUN_RECOVERY_REQUIRED_STATUS,
                "执行现场缺失或无法解析，禁止自动重放",
            )
        if not self._definitions.supports(state.checkpoint.definition_version):
            return (
                MANAGED_RUN_RECOVERY_REQUIRED_STATUS,
                "definition_version 不兼容，需由支持该版本的实例处理",
            )

        phase = state.checkpoint.phase
        if phase in (
            AgentRunPhase.ROUTING,
            AgentRunPhase.BEFORE_ACTION,
            AgentRunPhase.BEFORE_TOOL,
            AgentRunPhase.FINAL_READY,
        ):
            # 未完成的模型调用允许重做；工具尚未开始；final 已生成可直接提交。
            return MANAGED_RUN_INTERRUPTED_STATUS, "stale_executor"

        tool_name = (state.checkpoint.pending_action or {}).get("tool")
        if not isinstance(tool_name, str) or not tool_name:
            return (
                MANAGED_RUN_RECOVERY_REQUIRED_STATUS,
                "tool_in_flight 现场缺少工具标识",
            )
        policy = self._tool_replay_policy(
            agent_name=state.agent_name,
            tool_name=tool_name,
            user_id=state.user_id,
        )
        if policy is ToolReplayPolicy.NON_REPLAYABLE:
            return (
                MANAGED_RUN_RECOVERY_REQUIRED_STATUS,
                f"工具 {tool_name} 在飞行中且声明为 non_replayable",
            )
        return MANAGED_RUN_INTERRUPTED_STATUS, "stale_executor"

    def _tool_replay_policy(
        self, *, agent_name: str | None, tool_name: str, user_id: UUID
    ) -> ToolReplayPolicy:
        """解析工具声明的重放策略；未声明或未知一律按 non_replayable 处理。"""
        if agent_name not in SUPPORTED_AGENT_NAMES:
            return ToolReplayPolicy.NON_REPLAYABLE
        try:
            tools = self._definitions.resolve_tools(
                agent_name=agent_name,
                definition_version=self._definitions.definition_version,
                user_id=user_id,
            )
        except AppException:
            return ToolReplayPolicy.NON_REPLAYABLE
        tool = tools.get(tool_name)
        return (
            tool.replay_policy if tool is not None else ToolReplayPolicy.NON_REPLAYABLE
        )

    async def _execute_managed_run(
        self,
        *,
        user_id: UUID,
        claim: AgentRunClaimDTO,
        control: _ManagedRunControl,
    ) -> AsyncIterator[AgentRunEvent]:
        """执行受管理 run：生产者在独立 task 中推进，消费者按需转发事件。

        消费者取消（HTTP 断连或请求取消）时先把打断意图写入数据库，再在带期限的
        shield 内等待生产者走到当前安全点，避免取消直接破坏 checkpoint 提交。
        """
        runtime = DatabaseAgentRunRuntime(
            storage=self._storage,
            user_id=user_id,
            run_id=claim.run_id,
            lease_token=claim.lease_token or "",
            checkpoint=claim.checkpoint,
        )
        send_stream, receive_stream = anyio.create_memory_object_stream[AgentRunEvent](
            self._limits.stream_buffer_size
        )
        pending_error: BaseException | None = None
        async with anyio.create_task_group() as task_group:
            await task_group.start(
                self._produce_managed_events,
                user_id,
                claim,
                runtime,
                send_stream,
                control,
            )
            try:
                async for event in receive_stream:
                    yield event
            except BaseException as exc:  # noqa: BLE001 - 延后到 task group 之外再抛出
                # anyio 会把逃出 task group body 的异常包装成 ExceptionGroup；
                # 这里先保存原始异常，等收尾完成后再按原类型抛出。
                pending_error = exc
            finally:
                control.start_shutdown(self._limits.cancel_grace_seconds)
                with anyio.move_on_after(self._limits.cancel_grace_seconds) as scope:
                    scope.shield = True
                    if control.result is None:
                        # 只有尚未进入终态/暂停的现场才需要写入打断意图。
                        await self._interrupt_after_cancel(
                            user_id=user_id, claim=claim, control=control
                        )
                    await control.wait_done()
                if scope.cancelled_caught and control.execution_scope is not None:
                    control.execution_scope.cancel()
        if pending_error is not None:
            raise pending_error

    async def _produce_managed_events(
        self,
        user_id: UUID,
        claim: AgentRunClaimDTO,
        runtime: DatabaseAgentRunRuntime,
        send_stream: Any,
        control: _ManagedRunControl,
        *,
        task_status: anyio.abc.TaskStatus[None] = anyio.TASK_STATUS_IGNORED,
    ) -> None:
        """生产者：续租、执行、按有界通道推送事件，并负责 attempt 收尾。"""
        with anyio.CancelScope(shield=True) as execution_scope:
            control.execution_scope = execution_scope
            task_status.started()
            await self._drive_managed_events(
                user_id, claim, runtime, send_stream, control
            )

    async def _drive_managed_events(
        self,
        user_id: UUID,
        claim: AgentRunClaimDTO,
        runtime: DatabaseAgentRunRuntime,
        send_stream: Any,
        control: _ManagedRunControl,
    ) -> None:
        status: str | None = None
        error_code: str | None = None
        error_message: str | None = None
        interrupt_sent = False
        try:
            async with (
                send_stream,
                self._lease_supervisor(user_id=user_id, claim=claim),
            ):
                # 业务异常必须在本 task group 内部消化：anyio 会把逃出 body 的异常
                # 包装成 ExceptionGroup，调用方将无法按稳定错误码处理。
                try:
                    async for event in self._run_agent_events(
                        user_id=user_id, claim=claim, runtime=runtime
                    ):
                        if event.result is not None:
                            control.result = event.result
                        if not control.stop_requested:
                            self._emit(
                                send_stream=send_stream,
                                control=control,
                                event=event,
                            )
                        if control.slow_client and not interrupt_sent:
                            # 通道持续积压说明客户端消费过慢：写入打断意图，让执行者在
                            # 下一个安全点暂停，而不是继续产生无法投递的事件。
                            interrupt_sent = True
                            with anyio.move_on_after(
                                self._limits.cancel_grace_seconds
                            ) as slow_scope:
                                slow_scope.shield = True
                                await self._interrupt_after_cancel(
                                    user_id=user_id,
                                    claim=claim,
                                    control=control,
                                )
                        if event.type is AgentRunEventType.RUN_COMPLETED:
                            status = event.status.value
                        elif event.type is AgentRunEventType.RUN_INTERRUPTED:
                            status = MANAGED_RUN_INTERRUPTED_STATUS
                except AppException as exc:
                    control.error = exc
                    error_code = str(exc.error.code)
                    error_message = exc.message
                    status = (
                        MANAGED_RUN_RECOVERY_REQUIRED_STATUS
                        if exc.error
                        in (
                            errors.AgentResumeUnsafe,
                            errors.AgentDefinitionIncompatible,
                        )
                        or runtime.checkpoint.phase is AgentRunPhase.TOOL_IN_FLIGHT
                        else "failed"
                    )
                except (
                    UnknownToolError,
                    UnsafeAgentResumeError,
                    CheckpointError,
                ) as exc:
                    control.error = AppException(
                        errors.AgentResumeUnsafe,
                        message=f"执行现场无法安全恢复: {exc}",
                    )
                    error_code = "AgentResumeUnsafe"
                    error_message = str(exc)
                    status = MANAGED_RUN_RECOVERY_REQUIRED_STATUS
                except Exception as exc:
                    _log_warning(f"Managed agent run failed: {type(exc).__name__}")
                    control.error = AppException(
                        errors.ServiceUnavailable,
                        message="Agent 受管理执行暂不可用",
                    )
                    error_code = type(exc).__name__
                    error_message = "Agent 受管理执行暂不可用"
                    status = (
                        MANAGED_RUN_RECOVERY_REQUIRED_STATUS
                        if runtime.checkpoint.phase is AgentRunPhase.TOOL_IN_FLIGHT
                        else "failed"
                    )
        finally:
            remaining = self._limits.cancel_grace_seconds
            if control.shutdown_deadline is not None:
                remaining = max(0, control.shutdown_deadline - anyio.current_time())
            with anyio.move_on_after(remaining) as scope:
                scope.shield = True
                if status is None:
                    status = (
                        MANAGED_RUN_RECOVERY_REQUIRED_STATUS
                        if runtime.checkpoint.phase is AgentRunPhase.TOOL_IN_FLIGHT
                        else MANAGED_RUN_INTERRUPTED_STATUS
                    )
                await self._finalize_attempt(
                    user_id=user_id,
                    claim=claim,
                    status=status or "interrupted",
                    error_code=error_code,
                    error_message=error_message,
                )
            control.mark_done()

    async def _run_agent_events(
        self,
        *,
        user_id: UUID,
        claim: AgentRunClaimDTO,
        runtime: DatabaseAgentRunRuntime,
    ) -> AsyncIterator[AgentRunEvent]:
        """路由（仅统一入口）、构建 Builder 并推进 ReAct 执行。"""
        checkpoint = claim.checkpoint
        self._verify_compatibility(checkpoint)

        if checkpoint.phase is AgentRunPhase.ROUTING or checkpoint.route is None:
            audit_context = self._start_audit_context(user_id=user_id, claim=claim)
            route = await self._resolve_route(
                question=checkpoint.user_input, audit_context=audit_context
            )
            agent_name = _ROUTE_AGENT_NAMES.get(route)
            if agent_name is None:
                async for event in self._complete_degraded(
                    run_id=claim.run_id,
                    checkpoint=checkpoint,
                    runtime=runtime,
                    route=route.value,
                ):
                    yield event
                return

            routed = checkpoint.next_revision(
                phase=AgentRunPhase.BEFORE_ACTION,
                route=route.value,
                agent_name=agent_name,
                model_config=self._current_model_config(),
            )
            commit = await runtime.commit_checkpoint(
                checkpoint=routed, pause_when_interrupted=True
            )
            if commit.paused:
                yield _event(
                    type=AgentRunEventType.RUN_INTERRUPTED,
                    run_id=claim.run_id,
                    status=AgentRunStatus.INTERRUPTED,
                    checkpoint=commit.checkpoint,
                    stop_reason=AgentControlStopReason.INTERRUPT_REQUESTED.value,
                )
                return
            checkpoint = commit.checkpoint

        if checkpoint.agent_name == AGENT_CHAT_ROUTER:
            async for event in self._complete_degraded(
                run_id=claim.run_id,
                checkpoint=checkpoint,
                runtime=runtime,
                route=checkpoint.route or AgentRoute.UNSUPPORTED.value,
            ):
                yield event
            return

        await self._verify_temporary_dependencies(checkpoint)

        agent = self._definitions.build_agent(
            agent_name=checkpoint.agent_name or "",
            definition_version=checkpoint.definition_version,
            user_id=user_id,
            max_steps=checkpoint.max_steps,
            session_context=checkpoint.session_context,
        )
        self._require_safe_resume(agent=agent, checkpoint=checkpoint)

        if claim.attempt_no <= 1:
            events = agent.run_events(
                user_input=checkpoint.user_input,
                run_id=claim.run_id,
                runtime=runtime,
            )
        else:
            events = agent.resume_events(checkpoint=checkpoint, runtime=runtime)
        async for event in events:
            yield event

    async def _complete_degraded(
        self,
        *,
        run_id: str,
        checkpoint: AgentCheckpoint,
        runtime: DatabaseAgentRunRuntime,
        route: str,
    ) -> AsyncIterator[AgentRunEvent]:
        """完成统一入口的确定性降级回答；恢复时不再重新路由或调用模型。"""
        answer = checkpoint.final_answer or UNSUPPORTED_ANSWER
        changes: dict[str, Any] = {}
        if checkpoint.route is None:
            changes["route"] = route
        if checkpoint.agent_name is None:
            changes["agent_name"] = AGENT_CHAT_ROUTER
        if checkpoint.phase is not AgentRunPhase.FINAL_READY:
            changes["phase"] = AgentRunPhase.FINAL_READY
            changes["final_answer"] = answer
        if changes:
            checkpoint = checkpoint.next_revision(**changes)
        result = AgentRunResult(
            run_id=run_id,
            status=AgentRunStatus.COMPLETED,
            final_answer=answer,
            steps=(),
        )
        commit = await runtime.commit_terminal(checkpoint=checkpoint, result=result)
        yield _event(
            type=AgentRunEventType.RUN_COMPLETED,
            run_id=run_id,
            status=AgentRunStatus.COMPLETED,
            checkpoint=commit.checkpoint,
            result=result,
        )

    async def _resolve_route(
        self, *, question: str, audit_context: AgentAuditContext
    ) -> AgentRoute:
        """调用统一 Router 判定业务域；路由阶段失败不进入任何专业 Agent。"""
        router_agent = HybridAgentRouter(
            llm_client=AuditedAgentLLMClient(
                llm_client=self._llm_client,
                audit_context=audit_context,
            )
        )
        return await router_agent.route(question=question)

    def _verify_compatibility(self, checkpoint: AgentCheckpoint) -> None:
        """校验 definition_version 与冻结模型配置，不做静默降级。"""
        self._definitions.require_supported(checkpoint.definition_version)
        frozen = dict(checkpoint.model_config or {})
        if not frozen:
            return
        current = self._current_model_config()
        for field in ("provider", "model"):
            frozen_value = frozen.get(field)
            if frozen_value is not None and frozen_value != current.get(field):
                raise AppException(
                    errors.AgentDefinitionIncompatible,
                    message=(
                        f"checkpoint 冻结的模型 {field}={frozen_value} 与当前配置不一致"
                    ),
                )

    async def _verify_temporary_dependencies(self, checkpoint: AgentCheckpoint) -> None:
        """校验 checkpoint 中已保存的临时依赖（确认 token）是否仍然有效。

        过期的确认 token 不能延长授权、自动重新签发，也不能当作可执行凭据继续使用；
        检查失败时返回恢复不安全并停止，用户可发起新的普通业务请求。
        """
        for token in _pending_confirmation_tokens(checkpoint):
            try:
                pending = await self._action_store.get_pending_action(token=token)
            except Exception as exc:  # noqa: BLE001 - 无法确认依赖有效性时必须拒绝恢复
                raise AppException(
                    errors.AgentResumeUnsafe,
                    message="无法校验执行现场中的待确认动作，拒绝恢复",
                ) from exc
            if pending is None:
                raise AppException(
                    errors.AgentResumeUnsafe,
                    message="执行现场中的待确认动作已过期或被消费，拒绝恢复",
                )

    def _require_safe_resume(
        self, *, agent: ReActAgent, checkpoint: AgentCheckpoint
    ) -> None:
        """恢复前校验工具存在性与重放策略。"""
        assessment = agent.assess_resume(checkpoint)
        if assessment.decision.value == "recovery_required":
            raise UnsafeAgentResumeError(assessment.reason or "resume is not safe")

    async def request_cancel_interrupt(
        self, *, user_id: UUID, run_id: str, slow_client: bool = False
    ) -> str:
        """把客户端取消或慢消费转换成持久化打断意图。

        best-effort 且可重复调用：run 已进入终态或不处于运行中时返回空串，不改变已提交状态。
        """
        reason = "slow_client" if slow_client else "client_cancelled"
        try:
            return await self._storage.request_run_interrupt(
                user_id=user_id, run_id=run_id, reason=reason
            )
        except AppException as exc:
            # run 已进入终态或不可打断时无需再写意图；仅记录可定位信息。
            _log_warning(f"Managed run interrupt intent skipped: code={exc.error.code}")
            return ""

    async def _interrupt_after_cancel(
        self, *, user_id: UUID, claim: AgentRunClaimDTO, control: _ManagedRunControl
    ) -> None:
        """执行请求取消后的收尾：提交打断意图并按期限等待安全点。"""
        control.start_shutdown(self._limits.cancel_grace_seconds)
        await self.request_cancel_interrupt(
            user_id=user_id,
            run_id=claim.run_id,
            slow_client=control.slow_client,
        )

    def _emit(
        self, *, send_stream: Any, control: _ManagedRunControl, event: AgentRunEvent
    ) -> None:
        """向有界通道推送事件；通道满或已关闭时丢弃并转为打断意图。"""
        try:
            send_stream.send_nowait(event)
        except anyio.WouldBlock:
            control.mark_slow_client()
        except anyio.ClosedResourceError, anyio.BrokenResourceError:
            control.request_stop()

    @asynccontextmanager
    async def _lease_supervisor(self, *, user_id: UUID, claim: AgentRunClaimDTO):
        """在执行期间持续续租，覆盖有界工具等待，不能只依赖步骤边界续租。"""
        async with anyio.create_task_group() as task_group:
            task_group.start_soon(
                self._heartbeat_loop, user_id, claim.run_id, claim.lease_token or ""
            )
            try:
                yield
            finally:
                task_group.cancel_scope.cancel()

    async def _heartbeat_loop(
        self, user_id: UUID, run_id: str, lease_token: str
    ) -> None:
        """按 control poll 周期续租；lease 失效后停止续租并让提交自然失败。"""
        while True:
            await anyio.sleep(self._limits.control_poll_seconds)
            try:
                control = await self._storage.heartbeat_managed_run(
                    user_id=user_id,
                    run_id=run_id,
                    lease_token=lease_token,
                    lease_seconds=self._limits.lease_seconds,
                )
            except AppException:
                return
            except Exception as exc:  # noqa: BLE001 - 续租失败只记录，不做无界重试
                _log_warning(f"Managed run heartbeat failed: {type(exc).__name__}")
                return
            if not control.lease_owned:
                return

    async def _finalize_attempt(
        self,
        *,
        user_id: UUID,
        claim: AgentRunClaimDTO,
        status: str,
        error_code: str | None,
        error_message: str | None,
    ) -> None:
        """结束 attempt 并累计实际执行耗时。"""
        try:
            await self._storage.finish_managed_attempt(
                user_id=user_id,
                run_id=claim.run_id,
                lease_token=claim.lease_token or "",
                attempt_no=claim.attempt_no,
                status=status,
                error_code=error_code,
                error_message=error_message,
            )
        except Exception as exc:  # noqa: BLE001 - 收尾失败不能掩盖原始结果
            _log_warning(f"Managed run attempt finalize failed: {type(exc).__name__}")

    async def _record_audit(
        self,
        *,
        audit_context: AgentAuditContext,
        result: AgentRunResultDTO,
        route: str | None,
    ) -> None:
        """记录受管理执行的审计结果（best-effort）。"""
        await record_agent_audit(
            audit_writer=self._audit_service,
            audit_context=audit_context,
            result=result,
            metadata={"route": route, "managed": True},
        )

    def _current_model_config(self) -> dict[str, Any]:
        """冻结影响行为的非敏感模型配置；不包含任何凭据。"""
        provider = getattr(self._llm_client, "provider", None)
        model = getattr(self._llm_client, "model", None)
        return {
            "provider": provider or "unknown",
            "model": model or "unknown",
        }


def _log_warning(message: str) -> None:
    """记录一条执行期告警，且不允许观测失败改变执行结果。

    这些调用点全部位于 `except` 分支内：如果日志写入自身抛出异常，异常映射、中断收尾和
    attempt 终结都会被跳过，调用方拿到的会是与业务无关的次生异常。日志不可用时只放弃
    这一条记录。
    """
    try:
        logger.warning(message)
    except Exception:  # noqa: BLE001 - 观测失败不得改变已定的执行结果
        return


def _pending_confirmation_tokens(checkpoint: AgentCheckpoint) -> list[str]:
    """从已提交步骤中提取待确认动作 token，用于恢复前的临时依赖校验。"""
    tokens: list[str] = []
    for step in checkpoint.steps:
        action_result = step.action_result
        if not isinstance(action_result, Mapping):
            continue
        confirmation = action_result.get("confirmation")
        if not isinstance(confirmation, Mapping):
            continue
        token = confirmation.get("token")
        if isinstance(token, str) and token and token not in tokens:
            tokens.append(token)
    return tokens


def _entrypoint_definition(entrypoint: str) -> tuple[str, str | None, AgentRunPhase]:
    """返回入口对应的初始 agent_name、route 与阶段。"""
    if entrypoint == "chat":
        return AGENT_CHAT_ROUTER, None, AgentRunPhase.ROUTING
    if entrypoint == "order_support":
        return "order_support", ORDER_SUPPORT_ROUTE, AgentRunPhase.BEFORE_ACTION
    if entrypoint == "payment_support":
        return "payment_support", PAYMENT_SUPPORT_ROUTE, AgentRunPhase.BEFORE_ACTION
    raise AppException(errors.BadRequest, message=f"不支持的入口: {entrypoint}")


def _create_request_digest(
    *,
    entrypoint: str,
    question: str,
    session_id: str | None,
    max_steps: int,
) -> str:
    """创建请求摘要；同键不同输入必须返回冲突。"""
    payload = "\x1f".join([entrypoint, question, session_id or "", str(max_steps)])
    return sha256(payload.encode("utf-8")).hexdigest()


def _resume_request_digest(*, run_id: str) -> str:
    """resume 请求摘要；同一个 key 只绑定同一个 run。"""
    return sha256(f"resume\x1f{run_id}".encode()).hexdigest()


def _event(
    *,
    type: AgentRunEventType,
    run_id: str,
    status: AgentRunStatus,
    checkpoint: AgentCheckpoint | None,
    result: AgentRunResult | None = None,
    stop_reason: str | None = None,
) -> AgentRunEvent:
    """构造受管理路径使用的运行事件。"""
    return AgentRunEvent(
        type=type,
        run_id=run_id,
        status=status,
        result=result,
        checkpoint_revision=checkpoint.revision if checkpoint is not None else None,
        stop_reason=stop_reason,
    )


def _result_from_checkpoint(
    *,
    run_id: str,
    session_id: str,
    status: str,
    checkpoint: AgentCheckpoint,
) -> AgentRunResultDTO:
    """从已提交现场重建结果，用于重复 resume 不接管原流。"""
    return AgentRunResultDTO(
        run_id=run_id,
        status=status,
        answer=checkpoint.final_answer,
        steps=[
            AgentStepDTO.from_step_record(checkpoint_step_to_record(step))
            for step in checkpoint.steps
        ],
        session_id=session_id,
    )


def _run_status_fields(result: AgentRunResumeDTO) -> dict[str, Any]:
    """构造 run_status 事件载荷。"""
    return {
        "run": run_view_to_schema(result.run).model_dump(mode="json"),
        "result": result.result.to_schema().model_dump(mode="json"),
    }


@cache
def new_agent_execution_service() -> AgentExecutionService:
    """依赖注入：获取 AgentExecutionService 单例。"""
    llm_client = new_default_llm_client()
    return AgentExecutionService(
        storage=new_database_agent_storage_backend(),
        definitions=AgentDefinitionRegistry(
            llm_client=llm_client,
            order_service=new_order_service(),
            rag_service=new_rag_service(),
        ),
        llm_client=llm_client,
        audit_service=new_agent_audit_service(),
        action_store=new_agent_action_cache(),
        limits=AgentExecutionLimits(
            lease_seconds=settings.AGENT_LEASE_SECONDS,
            control_poll_seconds=settings.AGENT_CONTROL_POLL_SECONDS,
            cancel_grace_seconds=settings.AGENT_CANCEL_GRACE_SECONDS,
            stream_buffer_size=settings.AGENT_STREAM_BUFFER_SIZE,
        ),
    )
