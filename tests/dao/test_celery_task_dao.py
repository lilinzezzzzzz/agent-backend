from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from internal.dao import celery_task as celery_task_dao_module
from internal.dao.celery_task import (
    _FAIL_EXPIRED_QUEUED_SQL,
    _FAIL_STALE_SUBMITTING_SQL,
    _RECONCILE_EXPIRED_EXECUTION_SQL,
    CeleryTaskDao,
)
from internal.models.celery_task import CeleryTaskRecord
from internal.schemas.celery_task import CeleryTaskStatus


def _record(status: CeleryTaskStatus) -> CeleryTaskRecord:
    now = datetime(2026, 7, 7, 8, 0, 0)
    return CeleryTaskRecord(
        id=123,
        task_name="task.name",
        trace_id="trace-1",
        scope="user:1",
        queue="celery_queue",
        idempotency_key_hash="a" * 64,
        payload_hash="b" * 64,
        status=status.value,
        cancel_allowed=True,
        attempt_count=0,
        created_at=now,
        updated_at=now,
    )


class _Result:
    def __init__(
        self, *, record: CeleryTaskRecord | None = None, ids: list[int] | None = None
    ) -> None:
        self.record = record
        self.ids = ids or []

    def scalar_one_or_none(self) -> CeleryTaskRecord | None:
        return self.record

    def scalars(self) -> "_Result":
        return self

    def all(self) -> list[int]:
        return self.ids


class _Dialect:
    name = "postgresql"


class _Bind:
    dialect = _Dialect()


class _Session:
    def __init__(
        self, *, record: CeleryTaskRecord | None = None, ids: list[int] | None = None
    ) -> None:
        self.record = record
        self.ids = ids
        self.execute_calls: list[tuple[object, dict[str, object] | None]] = []

    @asynccontextmanager
    async def begin(self):
        yield

    def get_bind(self) -> _Bind:
        return _Bind()

    async def execute(
        self, statement: object, params: dict[str, object] | None = None
    ) -> _Result:
        self.execute_calls.append((statement, params))
        return _Result(record=self.record, ids=self.ids)


def test_reconciliation_is_deadline_predicate_not_persisted_status() -> None:
    assert {status.value for status in CeleryTaskStatus} == {
        "SUBMITTING",
        "QUEUED",
        "RUNNING",
        "CANCELLING",
        "ORPHANED",
        "SUCCEEDED",
        "FAILED",
        "CANCELLED",
    }


def _provider(session: _Session):
    @asynccontextmanager
    async def provide():
        yield cast(AsyncSession, session)

    return provide


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("initial", "expected", "finished"),
    [
        (CeleryTaskStatus.SUBMITTING, CeleryTaskStatus.CANCELLED, True),
        (CeleryTaskStatus.QUEUED, CeleryTaskStatus.CANCELLED, True),
        (CeleryTaskStatus.RUNNING, CeleryTaskStatus.CANCELLING, False),
    ],
)
async def test_request_cancellation_transitions_with_one_utc_time(
    monkeypatch: pytest.MonkeyPatch,
    initial: CeleryTaskStatus,
    expected: CeleryTaskStatus,
    finished: bool,
) -> None:
    now = datetime(2026, 7, 7, 9, 0, 0)
    clock_calls = 0

    def utc_now() -> datetime:
        nonlocal clock_calls
        clock_calls += 1
        return now

    monkeypatch.setattr(celery_task_dao_module, "utc_now_naive", utc_now)
    record = _record(initial)
    session = _Session(record=record)
    dao = CeleryTaskDao(session_provider=_provider(session))  # type: ignore[arg-type]

    actual = await dao.request_cancellation(record_id=123, scope="user:1")

    assert actual is record
    assert record.status == expected.value
    assert record.updated_at == now
    assert (record.finished_at == now) is finished
    assert clock_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [
        CeleryTaskStatus.CANCELLING,
        CeleryTaskStatus.CANCELLED,
        CeleryTaskStatus.ORPHANED,
        CeleryTaskStatus.SUCCEEDED,
        CeleryTaskStatus.FAILED,
    ],
)
async def test_request_cancellation_does_not_retime_idempotent_or_terminal_state(
    monkeypatch: pytest.MonkeyPatch,
    status: CeleryTaskStatus,
) -> None:
    def unexpected_utc_now() -> datetime:
        raise AssertionError("unchanged cancellation state must not read current time")

    monkeypatch.setattr(
        celery_task_dao_module,
        "utc_now_naive",
        unexpected_utc_now,
    )
    record = _record(status)
    session = _Session(record=record)
    dao = CeleryTaskDao(session_provider=_provider(session))  # type: ignore[arg-type]

    actual = await dao.request_cancellation(record_id=123, scope="user:1")

    assert actual is record
    assert record.status == status.value


@pytest.mark.asyncio
async def test_request_cancellation_rejects_running_when_cancel_disallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_utc_now() -> datetime:
        raise AssertionError("disallowed cancellation must not read current time")

    monkeypatch.setattr(
        celery_task_dao_module,
        "utc_now_naive",
        unexpected_utc_now,
    )
    record = _record(CeleryTaskStatus.RUNNING)
    record.cancel_allowed = False
    session = _Session(record=record)
    dao = CeleryTaskDao(session_provider=_provider(session))  # type: ignore[arg-type]

    actual = await dao.request_cancellation(record_id=123, scope="user:1")

    assert actual is record
    assert record.status == CeleryTaskStatus.RUNNING.value


@pytest.mark.asyncio
async def test_reconciler_reuses_utc_now_for_cutoff_and_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 7, 7, 9, 0, 0)
    clock_calls = 0

    def utc_now() -> datetime:
        nonlocal clock_calls
        clock_calls += 1
        return now

    monkeypatch.setattr(celery_task_dao_module, "utc_now_naive", utc_now)
    session = _Session(ids=[1, 2])
    dao = CeleryTaskDao(session_provider=_provider(session))  # type: ignore[arg-type]

    actual = await dao.fail_stale_submitting(batch_size=25, stale_seconds=30)

    assert actual == [1, 2]
    assert clock_calls == 1
    assert session.execute_calls[0] == (
        _FAIL_STALE_SUBMITTING_SQL,
        {
            "batch_size": 25,
            "cutoff": now - timedelta(seconds=30),
            "now": now,
        },
    )


@pytest.mark.asyncio
async def test_execution_reconciler_sets_orphan_fence_from_utc_now(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 7, 7, 9, 0, 0)

    def utc_now() -> datetime:
        return now

    monkeypatch.setattr(celery_task_dao_module, "utc_now_naive", utc_now)
    session = _Session(ids=[1])
    dao = CeleryTaskDao(session_provider=_provider(session))  # type: ignore[arg-type]

    actual = await dao.reconcile_expired_execution(
        batch_size=25,
        orphan_fence_seconds=300,
    )

    assert actual == [1]
    assert session.execute_calls[0] == (
        _RECONCILE_EXPIRED_EXECUTION_SQL,
        {
            "batch_size": 25,
            "fence_expires_at": now + timedelta(seconds=300),
            "now": now,
        },
    )


def test_reconciler_sql_uses_skip_locked_deadline_cas() -> None:
    submitting_sql = _FAIL_STALE_SUBMITTING_SQL.text
    queued_sql = _FAIL_EXPIRED_QUEUED_SQL.text
    execution_sql = _RECONCILE_EXPIRED_EXECUTION_SQL.text

    all_sql = "\n".join((submitting_sql, queued_sql, execution_sql))
    assert "NEEDS_RECONCILIATION" not in all_sql
    assert "FOR UPDATE SKIP LOCKED" in submitting_sql
    assert "r.status = 'SUBMITTING'" in submitting_sql
    assert "r.updated_at < :cutoff" in submitting_sql
    assert "FOR UPDATE SKIP LOCKED" in queued_sql
    assert "r.status = 'QUEUED'" in queued_sql
    assert "r.queued_deadline_at <= :now" in queued_sql
    assert "FOR UPDATE SKIP LOCKED" in execution_sql
    assert "r.status IN ('RUNNING', 'CANCELLING')" in execution_sql
    assert "r.hard_deadline_at <= :now" in execution_sql
    assert "SET status = 'ORPHANED'" in execution_sql
    assert "fence_expires_at = :fence_expires_at" in execution_sql


def test_postgresql_init_ddl_matches_state_machine_contract() -> None:
    ddl = Path("ddl/postgresql/init.sql").read_text(encoding="utf-8")
    celery_table_ddl = ddl[
        ddl.index("CREATE TABLE celery_task_record") : ddl.index(
            "CREATE INDEX idx_celery_task_record_execution_deadline"
        )
    ]

    assert "TIMESTAMP WITHOUT TIME ZONE" in celery_table_ddl
    assert "UNIQUE (scope, task_name, idempotency_key_hash)" in celery_table_ddl
    assert "WHERE status = 'SUBMITTING'" in ddl
    assert "WHERE status = 'QUEUED'" in ddl
    assert "WHERE status IN ('RUNNING', 'CANCELLING')" in ddl
    assert "WHERE status = 'ORPHANED'" in ddl
    assert "fence_expires_at" in ddl
    assert "cancel_allowed BOOLEAN DEFAULT true NOT NULL" in ddl
    assert "NEEDS_RECONCILIATION" not in ddl
    assert "DROP TABLE" not in ddl.upper()
    for removed_column in (
        "idempotency_expires_at",
        "execution_timeout_seconds",
        "lease_owner",
        "lease_expires_at",
        "error_type",
        "error_message",
    ):
        assert removed_column not in celery_table_ddl


def test_audit_actor_type_baseline_matches_model_contract() -> None:
    ddl = Path("ddl/postgresql/init.sql").read_text(encoding="utf-8")

    assert "creator_type VARCHAR(32) NOT NULL" in ddl
    assert "updater_type VARCHAR(32)" in ddl
    assert CeleryTaskRecord.creator_id.property.columns[0].nullable is True
    assert CeleryTaskRecord.creator_type.property.columns[0].nullable is False
    assert CeleryTaskRecord.updater_type.property.columns[0].nullable is True
