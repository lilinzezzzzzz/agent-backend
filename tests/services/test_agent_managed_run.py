"""受管理 run 存储后端的事务、lease 与幂等测试（SQLite）。

SQLite 只能验证状态机与事务语义；PostgreSQL 上的并发 claim / fence 行为需要在隔离
测试库中单独验证（标记 integration）。
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from internal.core import AppException, errors
from internal.dao.agent_conversation import (
    AgentMessageDao,
    AgentRunAttemptDao,
    AgentRunCheckpointDao,
    AgentRunDao,
    AgentRunStepDao,
    AgentSessionDao,
)
from internal.models.agent_conversation import AgentRun, AgentRunCheckpoint
from internal.schemas.agent import AgentStepDTO
from internal.services.agents import conversation as conversation_module
from internal.services.agents.conversation import (
    MANAGED_RUN_INTERRUPTED_STATUS,
    MANAGED_RUN_RECOVERY_REQUIRED_STATUS,
    DatabaseAgentStorageBackend,
)
from pkg.agents import (
    AgentCheckpoint,
    AgentRunPhase,
    AgentStepRecord,
    AgentStepStatus,
    AgentToolCall,
    decode_checkpoint,
    stable_tool_call_id,
    step_record_to_checkpoint_step,
)
from pkg.database.audit import AuditActor
from pkg.toolkit.timer import utc_now_naive

TEST_USER_ID = UUID("00000000-0000-7000-8000-000000000999")
OTHER_USER_ID = UUID("00000000-0000-7000-8000-000000001000")

RUN_ID = "run_managed_1"
SESSION_ID = "session_managed_1"
REQUEST_KEY = "create_key_0001"
EXECUTION_VERSION = "v1"


def build_backend(db_session) -> DatabaseAgentStorageBackend:
    """构造使用测试 session 的存储后端。"""
    return DatabaseAgentStorageBackend(
        session_dao=AgentSessionDao(session_provider=db_session),
        message_dao=AgentMessageDao(session_provider=db_session),
        run_dao=AgentRunDao(session_provider=db_session),
        run_step_dao=AgentRunStepDao(session_provider=db_session),
        checkpoint_dao=AgentRunCheckpointDao(session_provider=db_session),
        attempt_dao=AgentRunAttemptDao(session_provider=db_session),
    )


def initial_checkpoint(*, phase: AgentRunPhase = AgentRunPhase.BEFORE_ACTION):
    return AgentCheckpoint(
        run_id=RUN_ID,
        phase=phase,
        max_steps=4,
        user_input="订单 1001 到哪了？",
        definition_version=EXECUTION_VERSION,
        session_context={"session_id": SESSION_ID},
        route="order",
        agent_name="order_support",
        model_config={"provider": "test", "model": "test-model"},
        revision=0,
    )


async def create_ready_run(backend, *, request_key: str = REQUEST_KEY):
    return await backend.create_managed_run(
        user_id=TEST_USER_ID,
        run_id=RUN_ID,
        session_id=SESSION_ID,
        user_message_id="msg_user_1",
        entrypoint="order_support",
        agent_name="order_support",
        question="订单 1001 到哪了？",
        max_steps=4,
        trace_id="trace-1",
        request_key=request_key,
        request_digest="digest-1",
        execution_version=EXECUTION_VERSION,
        checkpoint=initial_checkpoint(),
    )


async def claim(backend, *, request_key: str = "resume_key_0001", **kwargs):
    return await backend.claim_managed_run(
        user_id=TEST_USER_ID,
        run_id=RUN_ID,
        request_key=request_key,
        request_digest=kwargs.pop("request_digest", "resume-digest"),
        trace_id="trace-claim",
        lease_seconds=kwargs.pop("lease_seconds", 30),
        resolve_stale_status=kwargs.pop(
            "resolve_stale_status",
            lambda state: (MANAGED_RUN_INTERRUPTED_STATUS, "stale_executor"),
        ),
        **kwargs,
    )


def with_step(checkpoint: AgentCheckpoint, step: AgentStepRecord) -> AgentCheckpoint:
    """按存储层语义把新步骤追加进 checkpoint，用于构造提交载荷。"""
    from dataclasses import replace

    steps = checkpoint.steps + (
        step_record_to_checkpoint_step(
            step,
            tool_call_id=stable_tool_call_id(run_id=RUN_ID, step_index=step.index),
        ),
    )
    return replace(
        checkpoint.next_revision(
            phase=AgentRunPhase.BEFORE_ACTION, next_step_index=len(steps)
        ),
        steps=steps,
    )


async def expire_lease(backend, db_session) -> None:
    """把当前 lease 置为已过期，用于模拟进程退出。"""
    run_dao = AgentRunDao(session_provider=db_session)
    await run_dao.execute_update(
        run_dao.update_stmt(
            AgentRun.run_id == RUN_ID,
            values={"lease_expires_at": utc_now_naive() - timedelta(seconds=60)},
            audit_actor=AuditActor.user(TEST_USER_ID),
        )
    )


@pytest.mark.asyncio
async def test_create_managed_run_freezes_checkpoint_and_dedupes(db_session) -> None:
    backend = build_backend(db_session)

    started = await create_ready_run(backend)
    assert started.run_id == RUN_ID
    assert started.session_id == SESSION_ID

    state = await backend.load_managed_run(user_id=TEST_USER_ID, run_id=RUN_ID)
    assert state is not None
    assert state.status == "ready"
    assert state.execution_version == EXECUTION_VERSION
    assert state.checkpoint is not None
    assert state.checkpoint.revision == 0
    assert state.checkpoint.phase is AgentRunPhase.BEFORE_ACTION

    # 同键同输入复用已有 run，不重复创建用户消息。
    again = await create_ready_run(backend)
    assert again.run_id == RUN_ID

    messages = await AgentMessageDao(session_provider=db_session).fetch_all(
        AgentMessageDao(session_provider=db_session).select_stmt()
    )
    assert len([m for m in messages if m.role == "user"]) == 1

    # 同键不同输入返回冲突。
    with pytest.raises(AppException) as excinfo:
        await backend.create_managed_run(
            user_id=TEST_USER_ID,
            run_id="run_other",
            session_id=SESSION_ID,
            user_message_id="msg_user_2",
            entrypoint="order_support",
            agent_name="order_support",
            question="换一个问题",
            max_steps=4,
            trace_id=None,
            request_key=REQUEST_KEY,
            request_digest="digest-other",
            execution_version=EXECUTION_VERSION,
            checkpoint=initial_checkpoint(),
        )
    assert excinfo.value.error is errors.IdempotencyConflict


@pytest.mark.asyncio
async def test_claim_grants_single_executor_and_replays_same_key(db_session) -> None:
    backend = build_backend(db_session)
    await create_ready_run(backend)

    first = await claim(backend)
    assert first.replayed is False
    assert first.attempt_no == 1
    assert first.lease_token
    assert first.status == "running"

    # 同 key 重试返回原 attempt，不创建第二个执行者。
    replay = await claim(backend)
    assert replay.replayed is True
    assert replay.attempt_no == 1
    assert replay.lease_token is None

    # 不同 key 对活动 run 返回冲突。
    with pytest.raises(AppException) as excinfo:
        await claim(backend, request_key="resume_key_0002")
    assert excinfo.value.error is errors.AgentRunStateConflict


@pytest.mark.asyncio
async def test_stale_lease_is_taken_over_by_next_resume(db_session) -> None:
    backend = build_backend(db_session)
    await create_ready_run(backend)
    await claim(backend)
    await expire_lease(backend, db_session)

    second = await claim(backend, request_key="resume_key_0002")

    assert second.attempt_no == 2
    assert second.lease_token
    state = await backend.load_managed_run(user_id=TEST_USER_ID, run_id=RUN_ID)
    assert state.status == "running"


@pytest.mark.asyncio
async def test_stale_non_replayable_tool_in_flight_requires_recovery(
    db_session,
) -> None:
    backend = build_backend(db_session)
    await create_ready_run(backend)
    await claim(backend)
    await expire_lease(backend, db_session)

    def resolve_recovery(state):
        return MANAGED_RUN_RECOVERY_REQUIRED_STATUS, "tool is non_replayable"

    result = await backend.claim_managed_run(
        user_id=TEST_USER_ID,
        run_id=RUN_ID,
        request_key="resume_key_0003",
        request_digest="resume-digest",
        trace_id=None,
        lease_seconds=30,
        resolve_stale_status=resolve_recovery,
    )

    assert result.lease_token is None
    assert result.status == MANAGED_RUN_RECOVERY_REQUIRED_STATUS
    state = await backend.load_managed_run(user_id=TEST_USER_ID, run_id=RUN_ID)
    assert state.status == MANAGED_RUN_RECOVERY_REQUIRED_STATUS
    assert state.error_message == "tool is non_replayable"


@pytest.mark.asyncio
async def test_commit_requires_current_fence_and_revision(db_session) -> None:
    backend = build_backend(db_session)
    await create_ready_run(backend)
    claimed = await claim(backend)
    checkpoint = claimed.checkpoint.next_revision(phase=AgentRunPhase.BEFORE_TOOL)

    with pytest.raises(AppException) as stale_token:
        await backend.commit_managed_checkpoint(
            user_id=TEST_USER_ID,
            run_id=RUN_ID,
            lease_token="not-the-token",
            expected_revision=claimed.checkpoint.revision,
            committed_checkpoint=checkpoint,
            pause_checkpoint=claimed.checkpoint.next_revision(),
            pause_when_interrupted=True,
        )
    assert stale_token.value.error is errors.AgentRunStateConflict

    with pytest.raises(AppException) as stale_revision:
        await backend.commit_managed_checkpoint(
            user_id=TEST_USER_ID,
            run_id=RUN_ID,
            lease_token=claimed.lease_token,
            expected_revision=claimed.checkpoint.revision + 5,
            committed_checkpoint=checkpoint,
            pause_checkpoint=claimed.checkpoint.next_revision(),
            pause_when_interrupted=True,
        )
    assert stale_revision.value.error is errors.AgentRunStateConflict


@pytest.mark.asyncio
async def test_interrupt_pauses_at_previous_safe_checkpoint(db_session) -> None:
    backend = build_backend(db_session)
    await create_ready_run(backend)
    claimed = await claim(backend)

    status = await backend.request_run_interrupt(
        user_id=TEST_USER_ID, run_id=RUN_ID, reason="user_cancel"
    )
    assert status == "interrupt_requested"
    assert (
        await backend.request_run_interrupt(
            user_id=TEST_USER_ID, run_id=RUN_ID, reason="user_cancel"
        )
        == "interrupt_requested"
    )

    commit = await backend.commit_managed_checkpoint(
        user_id=TEST_USER_ID,
        run_id=RUN_ID,
        lease_token=claimed.lease_token,
        expected_revision=claimed.checkpoint.revision,
        committed_checkpoint=claimed.checkpoint.next_revision(
            phase=AgentRunPhase.BEFORE_TOOL
        ),
        pause_checkpoint=claimed.checkpoint.next_revision(),
        pause_when_interrupted=True,
    )

    assert commit.paused is True
    assert commit.checkpoint.phase is AgentRunPhase.BEFORE_ACTION
    state = await backend.load_managed_run(user_id=TEST_USER_ID, run_id=RUN_ID)
    assert state.status == MANAGED_RUN_INTERRUPTED_STATUS
    assert state.interrupt_reason == "user_cancel"

    # lease 已释放：再次打断返回当前状态而不是冲突。
    assert (
        await backend.request_run_interrupt(
            user_id=TEST_USER_ID, run_id=RUN_ID, reason="again"
        )
        == MANAGED_RUN_INTERRUPTED_STATUS
    )


@pytest.mark.asyncio
async def test_step_commit_is_atomic_and_survives_interrupt(db_session) -> None:
    backend = build_backend(db_session)
    await create_ready_run(backend)
    claimed = await claim(backend)
    await backend.request_run_interrupt(
        user_id=TEST_USER_ID, run_id=RUN_ID, reason="user_cancel"
    )

    step = AgentStepDTO(
        index=0,
        status=AgentStepStatus.COMPLETED.value,
        action_type="tool_call",
        tool="get_order_status",
        args={"order_id": "1001"},
        action_result={"ok": True},
        elapsed_ms=3.0,
    )
    step_record = AgentStepRecord(
        index=0,
        action=AgentToolCall(tool="get_order_status", args={"order_id": "1001"}),
        status=AgentStepStatus.COMPLETED,
        action_result={"ok": True},
        elapsed_ms=3.0,
    )
    advance = with_step(claimed.checkpoint, step_record)

    commit = await backend.commit_managed_step(
        user_id=TEST_USER_ID,
        run_id=RUN_ID,
        lease_token=claimed.lease_token,
        expected_revision=claimed.checkpoint.revision,
        committed_checkpoint=advance,
        pause_checkpoint=advance,
        step=step,
        pause_when_interrupted=True,
    )

    # 步骤与现场同事务提交，暂停不会丢失已完成的工具结果。
    assert commit.paused is True
    assert commit.checkpoint.next_step_index == 1
    assert len(commit.checkpoint.steps) == 1

    state = await backend.load_managed_run(user_id=TEST_USER_ID, run_id=RUN_ID)
    assert state.status == MANAGED_RUN_INTERRUPTED_STATUS

    step_dao = AgentRunStepDao(session_provider=db_session)
    rows = await step_dao.fetch_all(
        step_dao.select_stmt().where(step_dao.model_cls.run_id == RUN_ID)
    )
    assert [row.step_index for row in rows] == [0]


@pytest.mark.asyncio
async def test_terminal_commit_writes_one_assistant_message(db_session) -> None:
    backend = build_backend(db_session)
    await create_ready_run(backend)
    claimed = await claim(backend)

    from dataclasses import replace

    from internal.schemas.agent import AgentRunResultDTO
    from pkg.agents import AgentCheckpointStep, AgentRunStatus

    final_step = AgentCheckpointStep(
        index=0,
        status="completed",
        action={"type": "final", "answer": "订单 1001 已发货。"},
        elapsed_ms=1.0,
    )
    committed = replace(
        claimed.checkpoint.next_revision(
            phase=AgentRunPhase.FINAL_READY, final_answer="订单 1001 已发货。"
        ),
        steps=(final_step,),
        next_step_index=1,
    )
    paused = replace(
        claimed.checkpoint.next_revision(
            phase=AgentRunPhase.FINAL_READY, final_answer="订单 1001 已发货。"
        ),
    )

    commit = await backend.commit_managed_terminal(
        user_id=TEST_USER_ID,
        run_id=RUN_ID,
        lease_token=claimed.lease_token,
        expected_revision=claimed.checkpoint.revision,
        committed_checkpoint=committed,
        pause_checkpoint=paused,
        terminal_status=AgentRunStatus.COMPLETED.value,
        result=AgentRunResultDTO(
            run_id=RUN_ID,
            status=AgentRunStatus.COMPLETED.value,
            answer="订单 1001 已发货。",
            steps=[AgentStepDTO(index=0, status="completed", action_type="final")],
            session_id=SESSION_ID,
        ),
        pause_when_interrupted=True,
    )

    assert commit.paused is False
    assert commit.status == AgentRunStatus.COMPLETED.value

    state = await backend.load_managed_run(user_id=TEST_USER_ID, run_id=RUN_ID)
    assert state.status == "completed"
    assert state.ended_at is not None
    assert state.checkpoint.final_answer == "订单 1001 已发货。"

    message_dao = AgentMessageDao(session_provider=db_session)
    messages = await message_dao.fetch_all(
        message_dao.select_stmt().where(message_dao.model_cls.run_id == RUN_ID)
    )
    assistant = [m for m in messages if m.role == "assistant"]
    assert len(assistant) == 1
    assert assistant[0].message_id == f"{RUN_ID}-assistant"

    # 终态 run 不允许再次打断或恢复。
    with pytest.raises(AppException) as interrupt_error:
        await backend.request_run_interrupt(
            user_id=TEST_USER_ID, run_id=RUN_ID, reason="too_late"
        )
    assert interrupt_error.value.error is errors.AgentRunStateConflict

    with pytest.raises(AppException) as resume_error:
        await claim(backend, request_key="resume_key_after_terminal")
    assert resume_error.value.error is errors.AgentRunStateConflict


@pytest.mark.asyncio
async def test_heartbeat_renews_and_reports_interrupt(db_session) -> None:
    backend = build_backend(db_session)
    await create_ready_run(backend)
    claimed = await claim(backend)

    heartbeat = await backend.heartbeat_managed_run(
        user_id=TEST_USER_ID,
        run_id=RUN_ID,
        lease_token=claimed.lease_token,
        lease_seconds=30,
    )
    assert heartbeat.lease_owned is True
    assert heartbeat.interrupt_requested is False

    await backend.request_run_interrupt(
        user_id=TEST_USER_ID, run_id=RUN_ID, reason="user_cancel"
    )
    heartbeat = await backend.heartbeat_managed_run(
        user_id=TEST_USER_ID,
        run_id=RUN_ID,
        lease_token=claimed.lease_token,
        lease_seconds=30,
    )
    # 打断请求期间仍允许续租，保证当前工具的有界结果可以保存。
    assert heartbeat.lease_owned is True
    assert heartbeat.interrupt_requested is True

    lost = await backend.heartbeat_managed_run(
        user_id=TEST_USER_ID,
        run_id=RUN_ID,
        lease_token="other-token",
        lease_seconds=30,
    )
    assert lost.lease_owned is False


@pytest.mark.asyncio
async def test_attempt_finish_accumulates_elapsed_and_releases_lease(
    db_session,
) -> None:
    backend = build_backend(db_session)
    await create_ready_run(backend)
    claimed = await claim(backend)

    await backend.finish_managed_attempt(
        user_id=TEST_USER_ID,
        run_id=RUN_ID,
        lease_token=claimed.lease_token,
        attempt_no=claimed.attempt_no,
        status=MANAGED_RUN_INTERRUPTED_STATUS,
    )

    run_dao = AgentRunDao(session_provider=db_session)
    run = await run_dao.get_by_run_id_for_user(run_id=RUN_ID, user_id=TEST_USER_ID)
    assert run.lease_token is None
    assert run.elapsed_ms >= 0

    attempt_dao = AgentRunAttemptDao(session_provider=db_session)
    attempt = await attempt_dao.latest_attempt(run_id=RUN_ID, user_id=TEST_USER_ID)
    assert attempt.status == MANAGED_RUN_INTERRUPTED_STATUS
    assert attempt.ended_at is not None


@pytest.mark.asyncio
async def test_load_managed_run_is_user_scoped(db_session) -> None:
    backend = build_backend(db_session)
    await create_ready_run(backend)

    assert await backend.load_managed_run(user_id=OTHER_USER_ID, run_id=RUN_ID) is None

    with pytest.raises(AppException) as excinfo:
        await backend.claim_managed_run(
            user_id=OTHER_USER_ID,
            run_id=RUN_ID,
            request_key="resume_key_0001",
            request_digest="resume-digest",
            trace_id=None,
            lease_seconds=30,
            resolve_stale_status=lambda state: (MANAGED_RUN_INTERRUPTED_STATUS, None),
        )
    assert excinfo.value.error is errors.NotFound


@pytest.mark.asyncio
async def test_complete_run_does_not_duplicate_committed_steps(db_session) -> None:
    backend = build_backend(db_session)
    await create_ready_run(backend)
    claimed = await claim(backend)

    step = AgentStepDTO(
        index=0,
        status="completed",
        action_type="tool_call",
        tool="get_order_status",
        args={"order_id": "1001"},
        action_result={"ok": True},
        elapsed_ms=1.0,
    )
    step_record = AgentStepRecord(
        index=0,
        action=AgentToolCall(tool="get_order_status", args={"order_id": "1001"}),
        status=AgentStepStatus.COMPLETED,
        action_result={"ok": True},
        elapsed_ms=1.0,
    )
    advance = with_step(claimed.checkpoint, step_record)
    committed = await backend.commit_managed_step(
        user_id=TEST_USER_ID,
        run_id=RUN_ID,
        lease_token=claimed.lease_token,
        expected_revision=claimed.checkpoint.revision,
        committed_checkpoint=advance,
        pause_checkpoint=advance,
        step=step,
        pause_when_interrupted=False,
    )
    assert committed.paused is False

    # 旧路径的 complete_run 只补齐缺失步骤，不重复插入同一 (run_id, step_index)。
    from internal.schemas.agent import AgentRunResultDTO

    await backend.complete_run(
        user_id=TEST_USER_ID,
        session_id=SESSION_ID,
        run_id=RUN_ID,
        route="order",
        result=AgentRunResultDTO(
            run_id=RUN_ID,
            status="completed",
            answer="订单 1001 已发货。",
            steps=[step],
            session_id=SESSION_ID,
        ),
    )

    step_dao = AgentRunStepDao(session_provider=db_session)
    rows = await step_dao.fetch_all(
        step_dao.select_stmt().where(step_dao.model_cls.run_id == RUN_ID)
    )
    assert [row.step_index for row in rows] == [0]


@pytest.mark.asyncio
async def test_checkpoint_column_and_payload_revision_stay_in_sync(db_session) -> None:
    backend = build_backend(db_session)
    await create_ready_run(backend)
    await claim(backend)

    checkpoint_dao = AgentRunCheckpointDao(session_provider=db_session)
    row = await checkpoint_dao.fetch_first(
        checkpoint_dao.select_stmt().where(AgentRunCheckpoint.run_id == RUN_ID)
    )
    decoded = decode_checkpoint(row.payload)
    assert decoded.revision == row.revision
    assert decoded.run_id == row.run_id


@pytest.mark.asyncio
async def test_old_run_without_execution_version_cannot_resume(db_session) -> None:
    backend = build_backend(db_session)
    started = await backend.start_run(
        user_id=TEST_USER_ID,
        session_id=None,
        entrypoint="order_support",
        agent_name="order_support",
        question="旧接口问题",
        max_steps=4,
        trace_id=None,
    )

    with pytest.raises(AppException) as excinfo:
        await backend.claim_managed_run(
            user_id=TEST_USER_ID,
            run_id=started.run_id,
            request_key="resume_key_legacy",
            request_digest="resume-digest",
            trace_id=None,
            lease_seconds=30,
            resolve_stale_status=lambda state: (MANAGED_RUN_INTERRUPTED_STATUS, None),
        )
    assert excinfo.value.error is errors.AgentResumeUnsafe


@pytest_asyncio.fixture
async def split_primary_replica():
    """主库与只读副本分离的 session 工厂，用于验证权威状态读主库。"""
    from pkg.database.base import Base

    primary_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    replica_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with primary_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with replica_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield (
        async_sessionmaker(
            bind=primary_engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        ),
        async_sessionmaker(
            bind=replica_engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        ),
    )

    await primary_engine.dispose()
    await replica_engine.dispose()


def build_split_backend(primary, replica) -> DatabaseAgentStorageBackend:
    """run / attempt 的展示读取走只读副本，其余写入走主库。"""
    return DatabaseAgentStorageBackend(
        session_dao=AgentSessionDao(session_provider=primary),
        message_dao=AgentMessageDao(session_provider=primary),
        run_dao=AgentRunDao(session_provider=primary, read_session_provider=replica),
        run_step_dao=AgentRunStepDao(session_provider=primary),
        checkpoint_dao=AgentRunCheckpointDao(session_provider=primary),
        attempt_dao=AgentRunAttemptDao(
            session_provider=primary, read_session_provider=replica
        ),
    )


async def seed_replica_run_row(replica, *, status: str, lease_token: str) -> None:
    """在副本中写入一份落后于主库的 run 行，模拟复制延迟。"""
    now = utc_now_naive()
    async with replica() as session, session.begin():
        session.add(
            AgentRun.create(
                audit_actor=AuditActor.user(TEST_USER_ID),
                run_id=RUN_ID,
                session_id=SESSION_ID,
                user_id=TEST_USER_ID,
                entrypoint="order_support",
                agent_name="order_support",
                route="order",
                status=status,
                max_steps=4,
                trace_id=None,
                started_at=now,
                ended_at=None,
                elapsed_ms=0,
                error_code=None,
                error_message=None,
                execution_version=EXECUTION_VERSION,
                checkpoint_revision=1,
                lease_token=lease_token,
                lease_expires_at=now + timedelta(seconds=30),
                interrupt_requested_at=None,
                interrupted_at=None,
                interrupt_reason=None,
                create_request_key=REQUEST_KEY,
                create_request_digest="digest-1",
                run_metadata=None,
                created_at=now,
                updated_at=now,
            )
        )


@pytest.mark.asyncio
async def test_control_state_reads_primary_not_read_replica(
    split_primary_replica,
) -> None:
    """打断状态检查以主库为权威：副本延迟不得让执行者看不到打断意图。

    查询与写后响应同样必须读取主库，避免把已提交进度显示为未完成。
    """
    primary, replica = split_primary_replica
    backend = build_split_backend(primary, replica)

    await create_ready_run(backend)
    claimed = await claim(backend)

    # 副本落后于主库：仍显示 running。
    await seed_replica_run_row(
        replica, status="running", lease_token="replica-lag-token"
    )

    status = await backend.request_run_interrupt(
        user_id=TEST_USER_ID, run_id=RUN_ID, reason="user_cancel"
    )
    assert status == "interrupt_requested"

    control = await backend.read_managed_control_state(
        user_id=TEST_USER_ID,
        run_id=RUN_ID,
        lease_token=claimed.lease_token,
    )
    assert control.status == "interrupt_requested"
    assert control.interrupt_requested is True
    assert control.attempt_no == 1
    assert control.lease_owned is True

    view = await backend.load_managed_run(user_id=TEST_USER_ID, run_id=RUN_ID)
    assert view.status == "interrupt_requested"
    assert view.attempt_no == 1
    # 单独读取副本确认它确实落后。
    replica_view = await AgentRunDao(session_provider=replica).get_by_run_id_for_user(
        run_id=RUN_ID, user_id=TEST_USER_ID
    )
    assert replica_view.status == "running"


@pytest.mark.asyncio
async def test_control_state_reports_lease_loss_from_primary(
    split_primary_replica,
) -> None:
    """lease 归属判定同样读主库：旧执行者不会因副本内容误判自己仍持有执行权。"""
    primary, replica = split_primary_replica
    backend = build_split_backend(primary, replica)

    await create_ready_run(backend)
    claimed = await claim(backend)

    # 主库上本次 attempt 已收尾并释放 lease；副本仍显示旧 token 有效。
    await backend.finish_managed_attempt(
        user_id=TEST_USER_ID,
        run_id=RUN_ID,
        lease_token=claimed.lease_token,
        attempt_no=claimed.attempt_no,
        status=MANAGED_RUN_INTERRUPTED_STATUS,
    )
    await seed_replica_run_row(
        replica, status="running", lease_token=claimed.lease_token
    )

    control = await backend.read_managed_control_state(
        user_id=TEST_USER_ID,
        run_id=RUN_ID,
        lease_token=claimed.lease_token,
    )

    # 旧执行者必须以主库为准判定自己已失去执行权。
    assert control.lease_owned is False
    assert control.status == "interrupted"


@pytest.mark.asyncio
async def test_interrupt_pause_logs_wait_time_without_client_text(db_session) -> None:
    """暂停时记录可统计的等待耗时，且不把客户端提供的打断原因原文写进日志。"""
    backend = build_backend(db_session)
    await create_ready_run(backend)
    claimed = await claim(backend)
    await backend.request_run_interrupt(
        user_id=TEST_USER_ID, run_id=RUN_ID, reason="用户输入的原因原文"
    )

    # 直接 patch 模块级 logger：整套测试同时运行时 conftest 的 logger mock 会被替换。
    with patch.object(conversation_module, "logger") as mocked_logger:
        await backend.commit_managed_checkpoint(
            user_id=TEST_USER_ID,
            run_id=RUN_ID,
            lease_token=claimed.lease_token,
            expected_revision=claimed.checkpoint.revision,
            committed_checkpoint=claimed.checkpoint.next_revision(
                phase=AgentRunPhase.BEFORE_TOOL
            ),
            pause_checkpoint=claimed.checkpoint.next_revision(),
            pause_when_interrupted=True,
        )

    entries = [
        call
        for call in mocked_logger.info.call_args_list
        if "paused by interrupt" in str(call)
    ]
    assert len(entries) == 1
    rendered = str(entries[0])
    assert RUN_ID in rendered
    # pause_checkpoint 是上一个安全现场，因此阶段仍是 before_action。
    assert "phase=before_action" in rendered
    assert "source=client_requested" in rendered
    assert "wait_ms=" in rendered
    assert "用户输入的原因原文" not in rendered


@pytest.mark.asyncio
async def test_interrupt_pause_logs_system_reason_verbatim(db_session) -> None:
    """服务端生成的打断原因按原值记录，便于区分用户主动与断连/慢消费。"""
    backend = build_backend(db_session)
    await create_ready_run(backend)
    claimed = await claim(backend)
    await backend.request_run_interrupt(
        user_id=TEST_USER_ID, run_id=RUN_ID, reason="slow_client"
    )

    with patch.object(conversation_module, "logger") as mocked_logger:
        await backend.commit_managed_checkpoint(
            user_id=TEST_USER_ID,
            run_id=RUN_ID,
            lease_token=claimed.lease_token,
            expected_revision=claimed.checkpoint.revision,
            committed_checkpoint=claimed.checkpoint.next_revision(
                phase=AgentRunPhase.BEFORE_TOOL
            ),
            pause_checkpoint=claimed.checkpoint.next_revision(),
            pause_when_interrupted=True,
        )

    rendered = str(
        [
            call
            for call in mocked_logger.info.call_args_list
            if "paused by interrupt" in str(call)
        ]
    )
    assert "source=slow_client" in rendered


@pytest.mark.asyncio
async def test_normal_commit_does_not_log_interrupt_pause(db_session) -> None:
    """未被打断的提交不写暂停日志，避免每个安全点都刷一条。"""
    backend = build_backend(db_session)
    await create_ready_run(backend)
    claimed = await claim(backend)

    with patch.object(conversation_module, "logger") as mocked_logger:
        await backend.commit_managed_checkpoint(
            user_id=TEST_USER_ID,
            run_id=RUN_ID,
            lease_token=claimed.lease_token,
            expected_revision=claimed.checkpoint.revision,
            committed_checkpoint=claimed.checkpoint.next_revision(
                phase=AgentRunPhase.BEFORE_ACTION
            ),
            pause_checkpoint=claimed.checkpoint.next_revision(),
            pause_when_interrupted=True,
        )

    state = await backend.load_managed_run(user_id=TEST_USER_ID, run_id=RUN_ID)
    assert state.status == "running"
    assert [
        call
        for call in mocked_logger.info.call_args_list
        if "paused by interrupt" in str(call)
    ] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("different_payload", [False, True])
async def test_create_unique_race_returns_winner_or_payload_conflict(
    db_session, monkeypatch, different_payload
):
    """模拟预检未看到竞争者、随后 insert 遭遇真实唯一约束冲突。"""
    from dataclasses import replace

    backend = build_backend(db_session)
    await create_ready_run(backend)
    original = conversation_module._get_run_by_create_key_for_update
    lookups = 0

    async def miss_first_lookup(**kwargs):
        nonlocal lookups
        lookups += 1
        if lookups == 1:
            return None
        return await original(**kwargs)

    monkeypatch.setattr(
        conversation_module, "_get_run_by_create_key_for_update", miss_first_lookup
    )

    async def create_loser():
        return await backend.create_managed_run(
            user_id=TEST_USER_ID,
            run_id="losing_run",
            session_id="losing_session",
            user_message_id="losing_message",
            entrypoint="order_support",
            agent_name="order_support",
            question="订单 1001 到哪了？",
            max_steps=4,
            trace_id=None,
            request_key=REQUEST_KEY,
            request_digest="different" if different_payload else "digest-1",
            execution_version=EXECUTION_VERSION,
            checkpoint=replace(initial_checkpoint(), run_id="losing_run"),
        )

    if different_payload:
        with pytest.raises(AppException) as error:
            await create_loser()
        assert error.value.error is errors.IdempotencyConflict
    else:
        winner = await create_loser()
        assert winner.run_id == RUN_ID
    assert lookups == 2
    # 失败方的事务必须完全回滚：不得留下孤儿 session。
    session_dao = AgentSessionDao(session_provider=db_session)
    sessions = await session_dao.fetch_all(session_dao.select_stmt())
    assert [session.session_id for session in sessions] == [SESSION_ID]


@pytest.mark.asyncio
async def test_get_reconciles_expired_executor_and_fences_old_token(db_session):
    backend = build_backend(db_session)
    await create_ready_run(backend)
    claimed = await claim(backend)
    await expire_lease(backend, db_session)
    view = await backend.load_managed_run(
        user_id=TEST_USER_ID,
        run_id=RUN_ID,
        resolve_stale_status=lambda state: ("interrupted", "stale_executor"),
    )
    assert view.status == "interrupted"
    assert not (
        await backend.read_managed_control_state(
            user_id=TEST_USER_ID, run_id=RUN_ID, lease_token=claimed.lease_token
        )
    ).lease_owned


@pytest.mark.asyncio
async def test_primary_load_works_before_any_replication(split_primary_replica):
    primary, replica = split_primary_replica
    backend = build_split_backend(primary, replica)
    await create_ready_run(backend)
    view = await backend.load_managed_run(user_id=TEST_USER_ID, run_id=RUN_ID)
    assert view is not None
    assert view.status == "ready"
    assert view.checkpoint.run_id == RUN_ID


def test_only_create_unique_constraint_is_recoverable():
    from sqlalchemy.exc import IntegrityError

    error = IntegrityError("insert", {}, Exception("other constraint"))
    assert not conversation_module._is_create_key_conflict(error)
    original = Exception("unique violation")
    cause = Exception("asyncpg unique violation")
    cause.constraint_name = "uq_agent_run_user_create_key"
    original.__cause__ = cause
    assert conversation_module._is_create_key_conflict(
        IntegrityError("insert", {}, original)
    )
