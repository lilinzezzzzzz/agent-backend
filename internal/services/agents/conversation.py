from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from functools import cache
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from internal.config import settings
from internal.core import AppException, errors
from internal.dao.agent_conversation import (
    AgentMessageDao,
    AgentRunAttemptDao,
    AgentRunCheckpointDao,
    AgentRunDao,
    AgentRunStepDao,
    AgentSessionDao,
    new_agent_message_dao,
    new_agent_run_attempt_dao,
    new_agent_run_checkpoint_dao,
    new_agent_run_dao,
    new_agent_run_step_dao,
    new_agent_session_dao,
)
from internal.models.agent_conversation import (
    AgentMessage,
    AgentRun,
    AgentRunAttempt,
    AgentRunCheckpoint,
    AgentSession,
)
from internal.schemas.agent import (
    AgentConversationContextDTO,
    AgentMessageDTO,
    AgentRunClaimDTO,
    AgentRunCommitDTO,
    AgentRunControlDTO,
    AgentRunResultDTO,
    AgentRunStartDTO,
    AgentRunStateDTO,
    AgentStepDTO,
    to_json_object,
    to_json_value,
)
from pkg.agents import (
    DEFAULT_CHECKPOINT_MAX_BYTES,
    AgentCheckpoint,
    CheckpointError,
    CheckpointSerializationError,
    decode_checkpoint,
    encode_checkpoint,
)
from pkg.database.audit import AuditActor
from pkg.database.dao import BaseDao
from pkg.logger import logger
from pkg.ids import uuid7_unique_str_id
from pkg.toolkit.timer import utc_now_naive

MANAGED_RUN_TERMINAL_STATUSES = frozenset(
    {"completed", "max_steps_reached", "failed", "recovery_required"}
)
"""受管理 run 的终态；出现后不允许再恢复为运行中。"""

MANAGED_RUN_ACTIVE_STATUS = "running"
MANAGED_RUN_READY_STATUS = "ready"
MANAGED_RUN_INTERRUPT_REQUESTED_STATUS = "interrupt_requested"
MANAGED_RUN_INTERRUPTED_STATUS = "interrupted"
MANAGED_RUN_RECOVERY_REQUIRED_STATUS = "recovery_required"
MANAGED_RUN_RESUMABLE_STATUSES = frozenset(
    {MANAGED_RUN_READY_STATUS, MANAGED_RUN_INTERRUPTED_STATUS}
)

_SYSTEM_INTERRUPT_REASONS = frozenset({"client_cancelled", "slow_client"})
"""由服务端生成、可以原样写入日志的打断原因。"""

type StaleRunResolver = Callable[[AgentRunStateDTO], tuple[str, str | None]]
"""过期 `running` 现场判定：返回要落库的状态和诊断原因。"""


class AgentConversationService:
    """Agent 会话存储用例服务。"""

    def __init__(self, *, storage_backend: DatabaseAgentStorageBackend):
        self._storage_backend = storage_backend

    async def start_run(self, **kwargs) -> AgentRunStartDTO:
        """创建或复用会话，并创建 running run。"""
        return await self._storage_backend.start_run(**kwargs)

    async def load_context(self, **kwargs) -> AgentConversationContextDTO:
        """读取会话上下文窗口。"""
        return await self._storage_backend.load_context(**kwargs)

    async def complete_run(self, **kwargs) -> None:
        """标记 run 完成并写入最终消息与步骤。"""
        await self._storage_backend.complete_run(**kwargs)

    async def fail_run(self, **kwargs) -> None:
        """标记 run 失败。"""
        await self._storage_backend.fail_run(**kwargs)


class DatabaseAgentStorageBackend:
    """基于当前主数据库的 Agent 会话与受管理运行存储后端。"""

    def __init__(
        self,
        *,
        session_dao: AgentSessionDao,
        message_dao: AgentMessageDao,
        run_dao: AgentRunDao,
        run_step_dao: AgentRunStepDao,
        checkpoint_dao: AgentRunCheckpointDao,
        attempt_dao: AgentRunAttemptDao,
        checkpoint_max_bytes: int = DEFAULT_CHECKPOINT_MAX_BYTES,
    ):
        self._session_dao = session_dao
        self._message_dao = message_dao
        self._run_dao = run_dao
        self._run_step_dao = run_step_dao
        self._checkpoint_dao = checkpoint_dao
        self._attempt_dao = attempt_dao
        self._checkpoint_max_bytes = checkpoint_max_bytes

    # ==========================================================================
    # 旧的非受管理会话存储路径
    # ==========================================================================

    async def start_run(
        self,
        *,
        user_id: UUID,
        session_id: str | None,
        entrypoint: str,
        agent_name: str,
        question: str,
        max_steps: int,
        trace_id: str | None,
    ) -> AgentRunStartDTO:
        """创建或校验会话，并写入用户消息和 running run。"""
        now = utc_now_naive()
        resolved_session_id = session_id or uuid7_unique_str_id()
        run_id = uuid7_unique_str_id()
        user_message_id = uuid7_unique_str_id()
        actor = AuditActor.user(user_id)

        async def _tx(sess: AsyncSession) -> None:
            session = await _get_session_for_update(
                sess=sess,
                session_id=resolved_session_id,
                user_id=user_id,
            )
            if session_id is not None and session is None:
                raise AppException(errors.NotFound, message="Agent 会话不存在")

            if session is None:
                session = AgentSession.create(
                    audit_actor=actor,
                    session_id=resolved_session_id,
                    user_id=user_id,
                    entrypoint=entrypoint,
                    status="active",
                    title=_build_session_title(question),
                    rolling_summary=None,
                    working_state={},
                    recent_token_count=0,
                    message_count=1,
                    last_message_at=now,
                    expires_at=None,
                    created_at=now,
                    updated_at=now,
                )
                sess.add(session)
            else:
                await self._session_dao.execute_update(
                    self._session_dao.update_stmt(
                        AgentSession.id == session.id,
                        values={
                            "message_count": AgentSession.message_count + 1,
                            "last_message_at": now,
                        },
                        audit_actor=actor,
                    ),
                    session=sess,
                )

            sess.add(
                AgentMessage.create(
                    audit_actor=actor,
                    message_id=user_message_id,
                    session_id=resolved_session_id,
                    run_id=run_id,
                    user_id=user_id,
                    role="user",
                    content=question,
                    content_summary=None,
                    token_count=0,
                    message_metadata=None,
                    created_at=now,
                    updated_at=now,
                )
            )
            sess.add(
                AgentRun.create(
                    audit_actor=actor,
                    run_id=run_id,
                    session_id=resolved_session_id,
                    user_id=user_id,
                    entrypoint=entrypoint,
                    agent_name=agent_name,
                    route=None,
                    status="running",
                    max_steps=max_steps,
                    trace_id=trace_id,
                    started_at=now,
                    ended_at=None,
                    elapsed_ms=0,
                    error_code=None,
                    error_message=None,
                    run_metadata=None,
                    created_at=now,
                    updated_at=now,
                )
            )

        await _execute_storage_transaction(self._session_dao, _tx)
        return AgentRunStartDTO(
            session_id=resolved_session_id,
            run_id=run_id,
            user_message_id=user_message_id,
        )

    async def load_context(
        self,
        *,
        user_id: UUID,
        session_id: str,
        max_recent_messages: int,
        max_recent_chars: int,
        exclude_run_id: str | None = None,
    ) -> AgentConversationContextDTO:
        """读取滚动摘要和最近消息窗口。"""
        session = await self._session_dao.get_by_session_id_for_user(
            session_id=session_id,
            user_id=user_id,
        )
        if session is None:
            raise AppException(errors.NotFound, message="Agent 会话不存在")

        recent_messages = await self._message_dao.list_recent_messages(
            user_id=user_id,
            session_id=session_id,
            limit=max_recent_messages,
            max_chars=max_recent_chars,
            exclude_run_id=exclude_run_id,
        )
        return AgentConversationContextDTO(
            session_id=session_id,
            rolling_summary=session.rolling_summary,
            recent_messages=tuple(
                AgentMessageDTO(role=message.role, content=message.content)
                for message in recent_messages
            ),
            working_state=to_json_object(session.working_state or {}),
            truncated=len(recent_messages) >= max_recent_messages,
        )

    async def complete_run(
        self,
        *,
        user_id: UUID,
        session_id: str,
        run_id: str,
        route: str | None,
        result: AgentRunResultDTO,
    ) -> None:
        """写入 assistant 消息、缺失的 steps，并把 run 标记为终态。

        受管理 run 的步骤由 checkpoint 事务增量提交，这里只补齐尚未落库的步骤，
        避免重复插入同一 `(run_id, step_index)`。
        """
        now = utc_now_naive()
        actor = AuditActor.user(user_id)

        async def _tx(sess: AsyncSession) -> None:
            run = await _get_run_for_update(
                sess=sess,
                run_id=run_id,
                session_id=session_id,
                user_id=user_id,
            )
            if run is None:
                raise AppException(errors.NotFound, message="Agent run 不存在")

            await self._insert_assistant_message_once(
                sess=sess,
                actor=actor,
                now=now,
                run=run,
                answer=result.answer or "",
            )
            await self._insert_missing_steps(
                sess=sess,
                actor=actor,
                now=now,
                run=run,
                steps=result.steps,
            )

            await self._run_dao.execute_update(
                self._run_dao.update_stmt(
                    AgentRun.id == run.id,
                    values={
                        "route": route,
                        "status": result.status,
                        "ended_at": now,
                        "elapsed_ms": _elapsed_ms(run.started_at, now),
                    },
                    audit_actor=actor,
                ),
                session=sess,
            )
            await self._bump_session_message_count(
                sess=sess, actor=actor, now=now, run=run
            )

        await _execute_storage_transaction(self._run_dao, _tx)

    async def fail_run(
        self,
        *,
        user_id: UUID,
        session_id: str,
        run_id: str,
        error_code: str,
        error_message: str,
    ) -> None:
        """把 running run 标记为 failed。"""
        now = utc_now_naive()
        actor = AuditActor.user(user_id)

        async def _tx(sess: AsyncSession) -> None:
            run = await _get_run_for_update(
                sess=sess,
                run_id=run_id,
                session_id=session_id,
                user_id=user_id,
            )
            if run is None:
                return
            await self._run_dao.execute_update(
                self._run_dao.update_stmt(
                    AgentRun.id == run.id,
                    values={
                        "status": "failed",
                        "ended_at": now,
                        "elapsed_ms": _elapsed_ms(run.started_at, now),
                        "error_code": error_code,
                        "error_message": error_message[:2000],
                    },
                    audit_actor=actor,
                ),
                session=sess,
            )

        await _execute_storage_transaction(self._run_dao, _tx)

    # ==========================================================================
    # 受管理 run：创建、查询、claim / lease / interrupt
    # ==========================================================================

    async def create_managed_run(
        self,
        *,
        user_id: UUID,
        run_id: str,
        session_id: str,
        user_message_id: str,
        entrypoint: str,
        agent_name: str,
        question: str,
        max_steps: int,
        trace_id: str | None,
        request_key: str,
        request_digest: str,
        execution_version: str,
        checkpoint: AgentCheckpoint,
        requested_session_id: str | None = None,
        route: str | None = None,
    ) -> AgentRunStartDTO:
        """在同一事务中创建会话、用户消息、ready run 与初始 checkpoint。

        创建请求通过 `(user_id, create_request_key)` 唯一约束去重；同键不同输入返回冲突，
        同键同输入返回已有 run，不重复创建用户消息。
        """
        now = utc_now_naive()
        actor = AuditActor.user(user_id)
        payload = self._encode(checkpoint)

        async def _tx(sess: AsyncSession) -> AgentRunStartDTO:
            existing = await _get_run_by_create_key_for_update(
                sess=sess,
                user_id=user_id,
                request_key=request_key,
            )
            if existing is not None:
                if existing.create_request_digest != request_digest:
                    raise AppException(
                        errors.IdempotencyConflict,
                        message="创建幂等键已绑定不同的输入",
                    )
                return AgentRunStartDTO(
                    session_id=existing.session_id,
                    run_id=existing.run_id,
                    user_message_id="",
                )

            session = await _get_session_for_update(
                sess=sess,
                session_id=session_id,
                user_id=user_id,
            )
            if requested_session_id is not None and session is None:
                raise AppException(errors.NotFound, message="Agent 会话不存在")

            if session is None:
                sess.add(
                    AgentSession.create(
                        audit_actor=actor,
                        session_id=session_id,
                        user_id=user_id,
                        entrypoint=entrypoint,
                        status="active",
                        title=_build_session_title(question),
                        rolling_summary=None,
                        working_state={},
                        recent_token_count=0,
                        message_count=1,
                        last_message_at=now,
                        expires_at=None,
                        created_at=now,
                        updated_at=now,
                    )
                )
            else:
                await self._session_dao.execute_update(
                    self._session_dao.update_stmt(
                        AgentSession.id == session.id,
                        values={
                            "message_count": AgentSession.message_count + 1,
                            "last_message_at": now,
                        },
                        audit_actor=actor,
                    ),
                    session=sess,
                )

            sess.add(
                AgentMessage.create(
                    audit_actor=actor,
                    message_id=user_message_id,
                    session_id=session_id,
                    run_id=run_id,
                    user_id=user_id,
                    role="user",
                    content=question,
                    content_summary=None,
                    token_count=0,
                    message_metadata=None,
                    created_at=now,
                    updated_at=now,
                )
            )
            sess.add(
                AgentRun.create(
                    audit_actor=actor,
                    run_id=run_id,
                    session_id=session_id,
                    user_id=user_id,
                    entrypoint=entrypoint,
                    agent_name=agent_name,
                    route=route,
                    status=MANAGED_RUN_READY_STATUS,
                    max_steps=max_steps,
                    trace_id=trace_id,
                    started_at=now,
                    ended_at=None,
                    elapsed_ms=0,
                    error_code=None,
                    error_message=None,
                    execution_version=execution_version,
                    checkpoint_revision=checkpoint.revision,
                    lease_token=None,
                    lease_expires_at=None,
                    interrupt_requested_at=None,
                    interrupted_at=None,
                    interrupt_reason=None,
                    create_request_key=request_key,
                    create_request_digest=request_digest,
                    run_metadata=None,
                    created_at=now,
                    updated_at=now,
                )
            )
            sess.add(
                AgentRunCheckpoint.create(
                    audit_actor=actor,
                    run_id=run_id,
                    session_id=session_id,
                    user_id=user_id,
                    schema_version=checkpoint.schema_version,
                    revision=checkpoint.revision,
                    phase=checkpoint.phase.value,
                    next_step_index=checkpoint.next_step_index,
                    payload=payload,
                    created_at=now,
                    updated_at=now,
                )
            )
            return AgentRunStartDTO(
                session_id=session_id,
                run_id=run_id,
                user_message_id=user_message_id,
            )

        try:
            return await _execute_storage_transaction_value(self._run_dao, _tx)
        except IntegrityError as exc:
            if not _is_create_key_conflict(exc):
                raise
            # 唯一约束竞争失败后，原事务已回滚；从主库读取获胜请求。
            async with self._run_dao.transaction() as sess:
                existing = await _get_run_by_create_key_for_update(
                    sess=sess, user_id=user_id, request_key=request_key
                )
                if existing is None:
                    raise
                if existing.create_request_digest != request_digest:
                    raise AppException(
                        errors.IdempotencyConflict,
                        message="创建幂等键已绑定不同的输入",
                    ) from exc
                return AgentRunStartDTO(
                    session_id=existing.session_id,
                    run_id=existing.run_id,
                    user_message_id="",
                )

    async def load_managed_run(
        self,
        *,
        user_id: UUID,
        run_id: str,
        resolve_stale_status: StaleRunResolver | None = None,
    ) -> AgentRunStateDTO | None:
        """从主库原子读取运行现场；可选地收敛失去执行者的运行。"""
        async with self._run_dao.transaction() as sess:
            run = await _get_managed_run_for_update(
                sess=sess, run_id=run_id, user_id=user_id
            )
            if run is None:
                return None
            now = utc_now_naive()
            if (
                resolve_stale_status is not None
                and run.execution_version is not None
                and run.status
                in (MANAGED_RUN_ACTIVE_STATUS, MANAGED_RUN_INTERRUPT_REQUESTED_STATUS)
                and (run.lease_expires_at is None or run.lease_expires_at <= now)
            ):
                await self._recover_stale_run(
                    sess=sess,
                    run=run,
                    now=now,
                    resolve_stale_status=resolve_stale_status,
                )
            attempt = await _get_latest_attempt(
                sess=sess, run_id=run_id, user_id=user_id
            )
            return await self._build_run_state(
                run=run,
                attempt_no=attempt.attempt_no if attempt is not None else 0,
                sess=sess,
            )

    async def _recover_stale_run(
        self,
        *,
        sess: AsyncSession,
        run: AgentRun,
        now: datetime,
        resolve_stale_status: StaleRunResolver,
    ) -> AgentRunStateDTO:
        """持有 run 行锁时废止旧执行权，并根据最后提交现场收敛状态。"""
        state = await self._build_run_state(run=run, attempt_no=0, sess=sess)
        status, reason = resolve_stale_status(state)
        actor = AuditActor.user(run.user_id)
        await self._run_dao.execute_update(
            self._run_dao.update_stmt(
                AgentRun.id == run.id,
                values={
                    "status": status,
                    "lease_token": None,
                    "lease_expires_at": None,
                    "interrupted_at": now
                    if status == MANAGED_RUN_INTERRUPTED_STATUS
                    else None,
                    "interrupt_requested_at": None,
                    "interrupt_reason": reason,
                    "error_code": "recovery_required"
                    if status == MANAGED_RUN_RECOVERY_REQUIRED_STATUS
                    else None,
                    "error_message": reason
                    if status == MANAGED_RUN_RECOVERY_REQUIRED_STATUS
                    else None,
                },
                audit_actor=actor,
            ),
            session=sess,
        )
        await sess.refresh(run)
        return await self._build_run_state(run=run, attempt_no=0, sess=sess)

    async def claim_managed_run(
        self,
        *,
        user_id: UUID,
        run_id: str,
        request_key: str,
        request_digest: str,
        trace_id: str | None,
        lease_seconds: int,
        resolve_stale_status: StaleRunResolver,
    ) -> AgentRunClaimDTO:
        """在单个事务中 claim 受管理 run 并获取新的 fencing token。

        同一 `request_key` 重试返回原 attempt 的当前状态，不创建新执行者；
        不同 key 对运行中 run 返回状态冲突。
        """
        now = utc_now_naive()
        actor = AuditActor.user(user_id)

        async def _tx(sess: AsyncSession) -> AgentRunClaimDTO:
            run = await _get_managed_run_for_update(
                sess=sess, run_id=run_id, user_id=user_id
            )
            if run is None:
                raise AppException(errors.NotFound, message="Agent run 不存在")
            if run.execution_version is None:
                raise AppException(
                    errors.AgentResumeUnsafe,
                    message="该 run 不是受管理运行，无法恢复",
                )

            existing_attempt = await _get_attempt_by_request_key(
                sess=sess, run_id=run_id, request_key=request_key
            )
            if existing_attempt is not None:
                if existing_attempt.request_digest != request_digest:
                    raise AppException(
                        errors.IdempotencyConflict,
                        message="幂等键已绑定不同的执行请求",
                    )
                state = await self._build_run_state(
                    run=run, attempt_no=existing_attempt.attempt_no, sess=sess
                )
                return AgentRunClaimDTO(
                    run_id=run.run_id,
                    session_id=run.session_id,
                    status=existing_attempt.status,
                    attempt_no=existing_attempt.attempt_no,
                    checkpoint=state.checkpoint,
                    lease_token=None,
                    replayed=True,
                )

            if run.status in MANAGED_RUN_RESUMABLE_STATUSES:
                pass
            elif run.status in (
                MANAGED_RUN_ACTIVE_STATUS,
                MANAGED_RUN_INTERRUPT_REQUESTED_STATUS,
            ):
                if run.lease_expires_at is not None and run.lease_expires_at > now:
                    raise AppException(
                        errors.AgentRunStateConflict,
                        message="该 run 已有活动的执行者",
                    )
                state = await self._recover_stale_run(
                    sess=sess,
                    run=run,
                    now=now,
                    resolve_stale_status=resolve_stale_status,
                )
                if state.status != MANAGED_RUN_INTERRUPTED_STATUS:
                    return AgentRunClaimDTO(
                        run_id=run.run_id,
                        session_id=run.session_id,
                        status=state.status,
                        attempt_no=0,
                        checkpoint=state.checkpoint,
                        lease_token=None,
                        replayed=False,
                    )
            else:
                raise AppException(
                    errors.AgentRunStateConflict,
                    message=f"当前状态 {run.status} 不允许恢复",
                )

            attempt_no = (
                await self._attempt_dao.max_attempt_no(sess=sess, run_id=run_id) + 1
            )
            lease_token = uuid7_unique_str_id()
            sess.add(
                AgentRunAttempt.create(
                    audit_actor=actor,
                    run_id=run_id,
                    user_id=user_id,
                    attempt_no=attempt_no,
                    request_key=request_key,
                    request_digest=request_digest,
                    status=MANAGED_RUN_ACTIVE_STATUS,
                    trace_id=trace_id,
                    started_at=now,
                    ended_at=None,
                    elapsed_ms=0,
                    created_at=now,
                    updated_at=now,
                )
            )
            await self._run_dao.execute_update(
                self._run_dao.update_stmt(
                    AgentRun.id == run.id,
                    values={
                        "status": MANAGED_RUN_ACTIVE_STATUS,
                        "lease_token": lease_token,
                        "lease_expires_at": now + timedelta(seconds=lease_seconds),
                        "interrupt_requested_at": None,
                        "interrupted_at": None,
                    },
                    audit_actor=actor,
                ),
                session=sess,
            )
            state = await self._build_run_state(
                run=run, attempt_no=attempt_no, sess=sess
            )
            return AgentRunClaimDTO(
                run_id=run.run_id,
                session_id=run.session_id,
                status=MANAGED_RUN_ACTIVE_STATUS,
                attempt_no=attempt_no,
                checkpoint=state.checkpoint,
                lease_token=lease_token,
                replayed=False,
            )

        return await _execute_storage_transaction_value(self._run_dao, _tx)

    async def request_run_interrupt(
        self,
        *,
        user_id: UUID,
        run_id: str,
        reason: str | None,
    ) -> str:
        """持久化打断意图；重复请求返回当前状态，终态返回冲突。"""
        now = utc_now_naive()
        actor = AuditActor.user(user_id)

        async def _tx(sess: AsyncSession) -> str:
            run = await _get_managed_run_for_update(
                sess=sess, run_id=run_id, user_id=user_id
            )
            if run is None:
                raise AppException(errors.NotFound, message="Agent run 不存在")
            if run.execution_version is None:
                raise AppException(
                    errors.AgentResumeUnsafe,
                    message="该 run 不是受管理运行，无法打断",
                )

            if run.status in (
                MANAGED_RUN_INTERRUPT_REQUESTED_STATUS,
                MANAGED_RUN_INTERRUPTED_STATUS,
            ):
                return run.status
            if run.status != MANAGED_RUN_ACTIVE_STATUS:
                raise AppException(
                    errors.AgentRunStateConflict,
                    message=f"当前状态 {run.status} 不允许打断",
                )

            await self._run_dao.execute_update(
                self._run_dao.update_stmt(
                    AgentRun.id == run.id,
                    values={
                        "status": MANAGED_RUN_INTERRUPT_REQUESTED_STATUS,
                        "interrupt_requested_at": now,
                        "interrupt_reason": reason,
                    },
                    audit_actor=actor,
                ),
                session=sess,
            )
            return MANAGED_RUN_INTERRUPT_REQUESTED_STATUS

        return await _execute_storage_transaction_value(self._run_dao, _tx)

    async def renew_managed_lease(
        self,
        *,
        user_id: UUID,
        run_id: str,
        lease_token: str,
        lease_seconds: int,
    ) -> bool:
        """按 fencing token 续租；租约已失效或 token 不匹配时返回 False。"""
        now = utc_now_naive()
        actor = AuditActor.user(user_id)
        rowcount = await self._run_dao.execute_update(
            self._run_dao.update_stmt(
                AgentRun.run_id == run_id,
                AgentRun.user_id == user_id,
                AgentRun.lease_token == lease_token,
                AgentRun.status.in_(
                    (
                        MANAGED_RUN_ACTIVE_STATUS,
                        MANAGED_RUN_INTERRUPT_REQUESTED_STATUS,
                    )
                ),
                AgentRun.lease_expires_at.is_not(None),
                AgentRun.lease_expires_at > now,
                values={
                    "lease_expires_at": now + timedelta(seconds=lease_seconds),
                },
                audit_actor=actor,
            )
        )
        return rowcount > 0

    async def read_managed_control_state(
        self,
        *,
        user_id: UUID,
        run_id: str,
        lease_token: str,
    ) -> AgentRunControlDTO:
        """读取执行者需要的控制状态：打断意图、lease 归属与 checkpoint 版本。

        控制状态决定执行者是否继续启动动作，必须以**主库**为权威：只读副本的复制延迟会
        让 `interrupt_requested` 晚一个复制周期才可见。这里显式复用主库连接，并让 run 与
        attempt 两次读取落在同一份快照上。
        """
        now = utc_now_naive()
        async with self._run_dao.session_provider() as session:
            run = await self._run_dao.get_by_run_id_for_user(
                run_id=run_id, user_id=user_id, session=session
            )
            if run is None:
                raise AppException(errors.NotFound, message="Agent run 不存在")
            attempt = await self._attempt_dao.latest_attempt(
                run_id=run_id, user_id=user_id, session=session
            )
        return AgentRunControlDTO(
            status=run.status,
            revision=run.checkpoint_revision or 0,
            attempt_no=attempt.attempt_no if attempt is not None else 0,
            lease_owned=(
                run.lease_token == lease_token
                and run.lease_expires_at is not None
                and run.lease_expires_at > now
            ),
            interrupt_requested=(run.status == MANAGED_RUN_INTERRUPT_REQUESTED_STATUS),
        )

    async def heartbeat_managed_run(
        self,
        *,
        user_id: UUID,
        run_id: str,
        lease_token: str,
        lease_seconds: int,
    ) -> AgentRunControlDTO:
        """续租并返回控制状态；token 不匹配或 lease 已失效时 `lease_owned=False`。

        续租必须匹配 fencing token 且 lease 尚未失效；打断请求期间也允许续租，
        以便执行者把当前工具的有界结果保存下来。
        """
        now = utc_now_naive()
        actor = AuditActor.user(user_id)

        async def _tx(sess: AsyncSession) -> AgentRunControlDTO:
            run = await _get_managed_run_for_update(
                sess=sess, run_id=run_id, user_id=user_id
            )
            if run is None:
                raise AppException(errors.NotFound, message="Agent run 不存在")
            lease_owned = (
                run.lease_token == lease_token
                and run.lease_expires_at is not None
                and run.lease_expires_at > now
                and run.status
                in (
                    MANAGED_RUN_ACTIVE_STATUS,
                    MANAGED_RUN_INTERRUPT_REQUESTED_STATUS,
                )
            )
            if lease_owned:
                await self._run_dao.execute_update(
                    self._run_dao.update_stmt(
                        AgentRun.id == run.id,
                        values={
                            "lease_expires_at": now + timedelta(seconds=lease_seconds)
                        },
                        audit_actor=actor,
                    ),
                    session=sess,
                )
            attempt = await _get_latest_attempt(
                sess=sess, run_id=run_id, user_id=user_id
            )
            return AgentRunControlDTO(
                status=run.status,
                revision=run.checkpoint_revision or 0,
                attempt_no=attempt.attempt_no if attempt is not None else 0,
                lease_owned=lease_owned,
                interrupt_requested=(
                    run.status == MANAGED_RUN_INTERRUPT_REQUESTED_STATUS
                ),
            )

        return await _execute_storage_transaction_value(self._run_dao, _tx)

    async def mark_run_recovery_required(
        self,
        *,
        user_id: UUID,
        run_id: str,
        lease_token: str,
        reason: str,
    ) -> None:
        """把无法安全重放的现场明确停在 recovery_required。"""
        now = utc_now_naive()
        actor = AuditActor.user(user_id)
        await self._run_dao.execute_update(
            self._run_dao.update_stmt(
                AgentRun.run_id == run_id,
                AgentRun.user_id == user_id,
                AgentRun.lease_token == lease_token,
                values={
                    "status": MANAGED_RUN_RECOVERY_REQUIRED_STATUS,
                    "lease_token": None,
                    "lease_expires_at": None,
                    "ended_at": now,
                    "error_code": MANAGED_RUN_RECOVERY_REQUIRED_STATUS,
                    "error_message": reason[:2000],
                },
                audit_actor=actor,
            )
        )

    async def finish_managed_attempt(
        self,
        *,
        user_id: UUID,
        run_id: str,
        lease_token: str,
        attempt_no: int,
        status: str,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """结束一次 attempt：累加实际执行耗时并释放 lease。"""
        now = utc_now_naive()
        actor = AuditActor.user(user_id)

        async def _tx(sess: AsyncSession) -> None:
            run = await _get_managed_run_for_update(
                sess=sess, run_id=run_id, user_id=user_id
            )
            if run is None:
                return
            attempt = await _get_attempt(
                sess=sess, run_id=run_id, attempt_no=attempt_no
            )
            if attempt is not None and attempt.ended_at is None:
                attempt_elapsed = _elapsed_ms(attempt.started_at, now)
                await self._attempt_dao.execute_update(
                    self._attempt_dao.update_stmt(
                        AgentRunAttempt.id == attempt.id,
                        values={
                            "status": status,
                            "ended_at": now,
                            "elapsed_ms": attempt_elapsed,
                        },
                        audit_actor=actor,
                    ),
                    session=sess,
                )
                run_values: dict[str, Any] = {
                    "elapsed_ms": AgentRun.elapsed_ms + attempt_elapsed,
                }
            else:
                run_values = {}

            if lease_token and run.lease_token == lease_token:
                # 仅当前执行者可以改变 run 状态；旧 attempt 不能覆盖新执行者的错误。
                run_values.update(
                    {
                        "lease_token": None,
                        "lease_expires_at": None,
                        "status": status,
                        "interrupt_requested_at": None,
                    }
                )
                if status == MANAGED_RUN_INTERRUPTED_STATUS:
                    run_values["interrupted_at"] = now
                elif status in MANAGED_RUN_TERMINAL_STATUSES:
                    run_values["ended_at"] = now
                if error_code is not None:
                    run_values["error_code"] = error_code
                if error_message is not None:
                    run_values["error_message"] = error_message[:2000]
            if run_values:
                await self._run_dao.execute_update(
                    self._run_dao.update_stmt(
                        AgentRun.id == run.id,
                        values=run_values,
                        audit_actor=actor,
                    ),
                    session=sess,
                )

        await _execute_storage_transaction(self._run_dao, _tx)

    # ==========================================================================
    # 受管理 run：原子 checkpoint / step / terminal 提交
    # ==========================================================================

    async def commit_managed_checkpoint(
        self,
        *,
        user_id: UUID,
        run_id: str,
        lease_token: str,
        expected_revision: int,
        committed_checkpoint: AgentCheckpoint,
        pause_checkpoint: AgentCheckpoint,
        pause_when_interrupted: bool,
    ) -> AgentRunCommitDTO:
        """原子提交新的执行现场；存在未处理打断请求时停在暂停现场。"""
        return await self._commit_managed_state(
            user_id=user_id,
            run_id=run_id,
            lease_token=lease_token,
            expected_revision=expected_revision,
            committed_checkpoint=committed_checkpoint,
            pause_checkpoint=pause_checkpoint,
            pause_when_interrupted=pause_when_interrupted,
            terminal_status=None,
            steps=(),
        )

    async def commit_managed_step(
        self,
        *,
        user_id: UUID,
        run_id: str,
        lease_token: str,
        expected_revision: int,
        committed_checkpoint: AgentCheckpoint,
        pause_checkpoint: AgentCheckpoint,
        step: AgentStepDTO,
        pause_when_interrupted: bool = True,
    ) -> AgentRunCommitDTO:
        """在同一事务中提交已完成步骤与更新后的运行现场。"""
        return await self._commit_managed_state(
            user_id=user_id,
            run_id=run_id,
            lease_token=lease_token,
            expected_revision=expected_revision,
            committed_checkpoint=committed_checkpoint,
            pause_checkpoint=pause_checkpoint,
            pause_when_interrupted=pause_when_interrupted,
            terminal_status=None,
            steps=(step,),
        )

    async def commit_managed_terminal(
        self,
        *,
        user_id: UUID,
        run_id: str,
        lease_token: str,
        expected_revision: int,
        committed_checkpoint: AgentCheckpoint,
        pause_checkpoint: AgentCheckpoint,
        terminal_status: str,
        result: AgentRunResultDTO,
        pause_when_interrupted: bool = True,
    ) -> AgentRunCommitDTO:
        """提交终态、final step 与唯一 assistant 消息。

        若存在未处理的打断请求，则放弃终态提交，持久化 `final_ready` 现场并把 run
        转为 `interrupted`；恢复时不再调用模型，直接提交该 final。
        """
        return await self._commit_managed_state(
            user_id=user_id,
            run_id=run_id,
            lease_token=lease_token,
            expected_revision=expected_revision,
            committed_checkpoint=committed_checkpoint,
            pause_checkpoint=pause_checkpoint,
            pause_when_interrupted=pause_when_interrupted,
            terminal_status=terminal_status,
            steps=tuple(result.steps),
            answer=result.answer,
        )

    async def _commit_managed_state(
        self,
        *,
        user_id: UUID,
        run_id: str,
        lease_token: str,
        expected_revision: int,
        committed_checkpoint: AgentCheckpoint,
        pause_checkpoint: AgentCheckpoint,
        pause_when_interrupted: bool,
        terminal_status: str | None,
        steps: tuple[AgentStepDTO, ...],
        answer: str | None = None,
    ) -> AgentRunCommitDTO:
        """受管理写入的单一路径：校验 fence/revision 后原子提交状态。"""
        now = utc_now_naive()
        actor = AuditActor.user(user_id)
        # 暂停耗时只在事务提交成功后记录，避免为回滚掉的写入留下观测数据。
        paused_phase: str | None = None
        paused_reason: str | None = None
        paused_wait_ms: float | None = None

        async def _tx(sess: AsyncSession) -> AgentRunCommitDTO:
            nonlocal paused_phase, paused_reason, paused_wait_ms
            run = await _get_managed_run_for_update(
                sess=sess, run_id=run_id, user_id=user_id
            )
            if run is None:
                raise AppException(errors.NotFound, message="Agent run 不存在")
            if run.lease_token != lease_token:
                raise AppException(
                    errors.AgentRunStateConflict,
                    message="执行权已失效，停止提交",
                )
            if run.lease_expires_at is None or run.lease_expires_at <= now:
                raise AppException(
                    errors.AgentRunStateConflict,
                    message="lease 已过期，停止提交",
                )
            if expected_revision >= 0 and run.checkpoint_revision != expected_revision:
                raise AppException(
                    errors.AgentRunStateConflict,
                    message="checkpoint 版本冲突，停止提交",
                )

            paused = (
                pause_when_interrupted
                and run.status == MANAGED_RUN_INTERRUPT_REQUESTED_STATUS
            )
            chosen = pause_checkpoint if paused else committed_checkpoint
            payload = self._encode(chosen)

            checkpoint_row = await _get_checkpoint_for_update(sess=sess, run_id=run_id)
            if checkpoint_row is None:
                raise AppException(
                    errors.AgentRunStateConflict,
                    message="checkpoint 不存在，拒绝写入",
                )
            await self._checkpoint_dao.execute_update(
                self._checkpoint_dao.update_stmt(
                    AgentRunCheckpoint.id == checkpoint_row.id,
                    values={
                        "schema_version": chosen.schema_version,
                        "revision": chosen.revision,
                        "phase": chosen.phase.value,
                        "next_step_index": chosen.next_step_index,
                        "payload": payload,
                    },
                    audit_actor=actor,
                ),
                session=sess,
            )

            if steps:
                await self._insert_missing_steps(
                    sess=sess, actor=actor, now=now, run=run, steps=steps
                )

            values: dict[str, Any] = {"checkpoint_revision": chosen.revision}
            # route / agent_name 由统一入口路由后冻结，run 行与 checkpoint 保持同一事实。
            if chosen.route is not None:
                values["route"] = chosen.route
            if chosen.agent_name is not None:
                values["agent_name"] = chosen.agent_name
            if paused:
                paused_phase = chosen.phase.value
                paused_reason = run.interrupt_reason
                requested_at = run.interrupt_requested_at
                paused_wait_ms = (
                    round((now - requested_at).total_seconds() * 1000, 3)
                    if requested_at is not None
                    else None
                )
                values.update(
                    {
                        "status": MANAGED_RUN_INTERRUPTED_STATUS,
                        "interrupted_at": now,
                        "interrupt_requested_at": None,
                        "lease_token": None,
                        "lease_expires_at": None,
                    }
                )
            elif terminal_status is not None:
                values.update(
                    {
                        "status": terminal_status,
                        "ended_at": now,
                        "lease_token": None,
                        "lease_expires_at": None,
                        "error_code": (
                            terminal_status
                            if terminal_status
                            in (
                                MANAGED_RUN_RECOVERY_REQUIRED_STATUS,
                                "failed",
                            )
                            else None
                        ),
                    }
                )
            if not paused and terminal_status is not None and answer:
                await self._insert_assistant_message_once(
                    sess=sess,
                    actor=actor,
                    now=now,
                    run=run,
                    answer=answer,
                )
                await self._bump_session_message_count(
                    sess=sess, actor=actor, now=now, run=run
                )

            await self._run_dao.execute_update(
                self._run_dao.update_stmt(
                    AgentRun.id == run.id,
                    values=values,
                    audit_actor=actor,
                ),
                session=sess,
            )
            return AgentRunCommitDTO(
                status=(
                    MANAGED_RUN_INTERRUPTED_STATUS
                    if paused
                    else (terminal_status or MANAGED_RUN_ACTIVE_STATUS)
                ),
                revision=chosen.revision,
                checkpoint=chosen,
                paused=paused,
            )

        commit = await _execute_storage_transaction_value(self._run_dao, _tx)
        if commit.paused:
            _log_interrupt_pause(
                run_id=run_id,
                phase=paused_phase,
                reason=paused_reason,
                wait_ms=paused_wait_ms,
            )
        return commit

    # ==========================================================================
    # 内部辅助
    # ==========================================================================

    def _encode(self, checkpoint: AgentCheckpoint) -> dict[str, Any]:
        """编码 checkpoint 并统一把校验失败转换为业务异常。"""
        try:
            return encode_checkpoint(checkpoint, max_bytes=self._checkpoint_max_bytes)
        except CheckpointSerializationError as exc:
            raise AppException(
                errors.AgentResumeUnsafe,
                message=f"执行现场无法持久化: {exc}",
            ) from exc

    async def _build_run_state(
        self, *, run: AgentRun, attempt_no: int, sess: AsyncSession | None = None
    ) -> AgentRunStateDTO:
        """读取 checkpoint 行并解码为运行状态快照。

        传入 `sess` 时复用调用方事务，避免在打开的事务内嵌套新连接。
        """
        if sess is None:
            checkpoint_row = await self._checkpoint_dao.fetch_first(
                self._checkpoint_dao.select_stmt().where(
                    AgentRunCheckpoint.run_id == run.run_id
                )
            )
        else:
            checkpoint_row = await _get_checkpoint(sess=sess, run_id=run.run_id)
        checkpoint: AgentCheckpoint | None = None
        checkpoint_error: str | None = None
        if checkpoint_row is not None:
            try:
                checkpoint = decode_checkpoint_row(
                    checkpoint_row, max_bytes=self._checkpoint_max_bytes
                )
            except CheckpointError as exc:
                checkpoint_error = f"{type(exc).__name__}: {exc}"
        else:
            checkpoint_error = "checkpoint 不存在"

        return AgentRunStateDTO(
            run_id=run.run_id,
            session_id=run.session_id,
            user_id=run.user_id,
            entrypoint=run.entrypoint,
            agent_name=run.agent_name,
            status=run.status,
            max_steps=run.max_steps,
            revision=run.checkpoint_revision or 0,
            attempt_no=attempt_no,
            started_at=run.started_at,
            route=run.route,
            execution_version=run.execution_version,
            ended_at=run.ended_at,
            elapsed_ms=run.elapsed_ms or 0,
            error_code=run.error_code or checkpoint_error,
            error_message=run.error_message or checkpoint_error,
            interrupt_reason=run.interrupt_reason,
            create_request_key=run.create_request_key,
            create_request_digest=run.create_request_digest,
            checkpoint=checkpoint,
        )

    async def _insert_missing_steps(
        self,
        *,
        sess: AsyncSession,
        actor: AuditActor,
        now: datetime,
        run: AgentRun,
        steps: tuple[AgentStepDTO, ...],
    ) -> None:
        """只插入尚未落库的步骤，保证受管理增量提交与旧路径都不重复写入。"""
        if not steps:
            return
        existing = await self._run_step_dao.list_step_indexes(
            sess=sess, run_id=run.run_id
        )
        rows = [
            {
                "run_id": run.run_id,
                "session_id": run.session_id,
                "user_id": run.user_id,
                "step_index": step.index,
                "status": step.status,
                "action_type": step.action_type,
                "tool": step.tool,
                "args": step.args,
                "action_result": to_json_value(step.action_result),
                "artifact_id": None,
                "error": step.error,
                "elapsed_ms": step.elapsed_ms,
                "created_at": now,
                "updated_at": now,
            }
            for step in steps
            if step.index not in existing
        ]
        if not rows:
            return
        statement = self._run_step_dao.build_insert_rows_stmt(
            rows=rows, audit_actor=actor
        )
        if statement is not None:
            await sess.execute(statement)

    async def _insert_assistant_message_once(
        self,
        *,
        sess: AsyncSession,
        actor: AuditActor,
        now: datetime,
        run: AgentRun,
        answer: str,
    ) -> bool:
        """写入稳定的 assistant 消息；已存在时返回 False。"""
        message_id = _managed_assistant_message_id(run.run_id)
        existing = await _get_message_by_id(sess=sess, message_id=message_id)
        if existing is not None:
            return False
        sess.add(
            AgentMessage.create(
                audit_actor=actor,
                message_id=message_id,
                session_id=run.session_id,
                run_id=run.run_id,
                user_id=run.user_id,
                role="assistant",
                content=answer,
                content_summary=None,
                token_count=0,
                message_metadata=None,
                created_at=now,
                updated_at=now,
            )
        )
        return True

    async def _bump_session_message_count(
        self,
        *,
        sess: AsyncSession,
        actor: AuditActor,
        now: datetime,
        run: AgentRun,
    ) -> None:
        """统一递增会话消息计数与最后消息时间。"""
        await self._session_dao.execute_update(
            self._session_dao.update_stmt(
                AgentSession.session_id == run.session_id,
                AgentSession.user_id == run.user_id,
                values={
                    "message_count": AgentSession.message_count + 1,
                    "last_message_at": now,
                },
                audit_actor=actor,
            ),
            session=sess,
        )


@cache
def new_agent_conversation_service() -> AgentConversationService:
    """依赖注入：获取 AgentConversationService 单例。"""
    return AgentConversationService(
        storage_backend=new_database_agent_storage_backend(),
    )


@cache
def new_database_agent_storage_backend() -> DatabaseAgentStorageBackend:
    """依赖注入：获取受管理运行使用的存储后端单例。"""
    return DatabaseAgentStorageBackend(
        session_dao=new_agent_session_dao(),
        message_dao=new_agent_message_dao(),
        run_dao=new_agent_run_dao(),
        run_step_dao=new_agent_run_step_dao(),
        checkpoint_dao=new_agent_run_checkpoint_dao(),
        attempt_dao=new_agent_run_attempt_dao(),
        checkpoint_max_bytes=settings.AGENT_CHECKPOINT_MAX_BYTES,
    )


def decode_checkpoint_row(
    row: AgentRunCheckpoint, *, max_bytes: int = DEFAULT_CHECKPOINT_MAX_BYTES
) -> AgentCheckpoint:
    """把 checkpoint 行解码为已校验的 checkpoint，保证列与载荷版本一致。"""
    checkpoint = decode_checkpoint(row.payload, max_bytes=max_bytes)
    if checkpoint.revision != row.revision:
        raise CheckpointError("checkpoint revision mismatch between column and payload")
    if checkpoint.run_id != row.run_id:
        raise CheckpointError("checkpoint run_id mismatch between column and payload")
    return checkpoint


async def _execute_storage_transaction(
    dao: BaseDao,
    callback: Callable[[AsyncSession], Awaitable[None]],
) -> None:
    async with dao.transaction() as session:
        await callback(session)


async def _execute_storage_transaction_value[T](
    dao: BaseDao,
    callback: Callable[[AsyncSession], Awaitable[T]],
) -> T:
    async with dao.transaction() as session:
        return await callback(session)


async def _get_session_for_update(
    *, sess, session_id: str, user_id: UUID
) -> AgentSession | None:
    result = await sess.execute(
        select(AgentSession).where(
            AgentSession.session_id == session_id,
            AgentSession.user_id == user_id,
            AgentSession.deleted_at.is_(None),
        )
    )
    return result.scalars().first()


async def _get_run_for_update(
    *,
    sess,
    run_id: str,
    session_id: str,
    user_id: UUID,
) -> AgentRun | None:
    result = await sess.execute(
        select(AgentRun).where(
            AgentRun.run_id == run_id,
            AgentRun.session_id == session_id,
            AgentRun.user_id == user_id,
            AgentRun.deleted_at.is_(None),
        )
    )
    return result.scalars().first()


async def _get_managed_run_for_update(
    *, sess, run_id: str, user_id: UUID
) -> AgentRun | None:
    result = await sess.execute(
        select(AgentRun)
        .where(
            AgentRun.run_id == run_id,
            AgentRun.user_id == user_id,
            AgentRun.deleted_at.is_(None),
        )
        .with_for_update()
    )
    return result.scalars().first()


async def _get_run_by_create_key_for_update(
    *, sess, user_id: UUID, request_key: str
) -> AgentRun | None:
    result = await sess.execute(
        select(AgentRun)
        .where(
            AgentRun.user_id == user_id,
            AgentRun.create_request_key == request_key,
            AgentRun.deleted_at.is_(None),
        )
        .with_for_update()
    )
    return result.scalars().first()


async def _get_attempt_by_request_key(
    *, sess, run_id: str, request_key: str
) -> AgentRunAttempt | None:
    result = await sess.execute(
        select(AgentRunAttempt).where(
            AgentRunAttempt.run_id == run_id,
            AgentRunAttempt.request_key == request_key,
        )
    )
    return result.scalars().first()


async def _get_latest_attempt(
    *, sess, run_id: str, user_id: UUID
) -> AgentRunAttempt | None:
    result = await sess.execute(
        select(AgentRunAttempt)
        .where(
            AgentRunAttempt.run_id == run_id,
            AgentRunAttempt.user_id == user_id,
        )
        .order_by(AgentRunAttempt.attempt_no.desc())
        .limit(1)
    )
    return result.scalars().first()


async def _get_attempt(*, sess, run_id: str, attempt_no: int) -> AgentRunAttempt | None:
    result = await sess.execute(
        select(AgentRunAttempt).where(
            AgentRunAttempt.run_id == run_id,
            AgentRunAttempt.attempt_no == attempt_no,
        )
    )
    return result.scalars().first()


async def _get_checkpoint(*, sess, run_id: str) -> AgentRunCheckpoint | None:
    result = await sess.execute(
        select(AgentRunCheckpoint).where(AgentRunCheckpoint.run_id == run_id)
    )
    return result.scalars().first()


async def _get_checkpoint_for_update(*, sess, run_id: str) -> AgentRunCheckpoint | None:
    result = await sess.execute(
        select(AgentRunCheckpoint)
        .where(AgentRunCheckpoint.run_id == run_id)
        .with_for_update()
    )
    return result.scalars().first()


async def _get_message_by_id(*, sess, message_id: str) -> AgentMessage | None:
    result = await sess.execute(
        select(AgentMessage).where(AgentMessage.message_id == message_id)
    )
    return result.scalars().first()


def _log_interrupt_pause(
    *,
    run_id: str,
    phase: str | None,
    reason: str | None,
    wait_ms: float | None,
) -> None:
    """记录一次「打断受理 → 实际暂停」的耗时，供后续判断是否需要加速通知。

    `interrupt_requested_at` / `interrupted_at` 会在下一次 claim 时被清空，因此这里在暂停
    发生的那一刻留痕。只记录归类后的来源标签，不写客户端提供的自由文本。
    """
    source = (
        reason
        if reason in _SYSTEM_INTERRUPT_REASONS
        else ("client_requested" if reason else "unknown")
    )
    try:
        logger.info(
            f"managed run paused by interrupt: run_id={run_id} phase={phase} "
            f"source={source} wait_ms={wait_ms}"
        )
    except Exception:  # noqa: BLE001 - 观测失败不得把已提交的暂停变成执行失败
        return


def _managed_assistant_message_id(run_id: str) -> str:
    """受管理 run 的稳定 assistant 消息 ID，用于终态提交幂等兜底。"""
    return f"{run_id}-assistant"


def _build_session_title(question: str) -> str:
    return question.strip()[:128] or "新会话"


def _elapsed_ms(started_at, ended_at) -> float:
    return round((ended_at - started_at).total_seconds() * 1000, 3)


def _is_create_key_conflict(exc: IntegrityError) -> bool:
    """只识别创建幂等唯一键，其他完整性错误仍原样抛出。"""
    original = exc.orig
    for error in (original, getattr(original, "__cause__", None)):
        if getattr(error, "constraint_name", None) == "uq_agent_run_user_create_key":
            return True
        if (
            getattr(getattr(error, "diag", None), "constraint_name", None)
            == "uq_agent_run_user_create_key"
        ):
            return True
    return (
        "UNIQUE constraint failed: agent_run.user_id, agent_run.create_request_key"
        in str(original)
    )
