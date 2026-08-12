import hashlib
from contextlib import asynccontextmanager
from datetime import datetime
from uuid import UUID

import pytest

from internal.core import AppException, errors
from internal.models.celery_task import CeleryTaskRecord
from internal.schemas.celery_task import CeleryTaskStatus
from internal.services.celery_task import CeleryTaskService, canonical_task_payload
from pkg.database.base import ModelMixin

TEST_RECORD_ID = UUID("00000000-0000-7000-8000-000000000123")
EXISTING_RECORD_ID = UUID("00000000-0000-7000-8000-000000000042")


class FakeSession:
    @asynccontextmanager
    async def begin(self):
        yield


@asynccontextmanager
async def _session_provider():
    yield FakeSession()


def _record(
    *,
    record_id: UUID = TEST_RECORD_ID,
    payload_hash: str = "payload",
    status: CeleryTaskStatus = CeleryTaskStatus.SUBMITTING,
    cancel_allowed: bool = True,
) -> CeleryTaskRecord:
    now = datetime(2026, 7, 7, 12, 0, 0)
    return CeleryTaskRecord(
        id=record_id,
        task_name="task.name",
        trace_id="trace-1",
        scope="user:1",
        queue="celery_queue",
        idempotency_key_hash="a" * 64,
        payload_hash=payload_hash,
        status=status.value,
        cancel_allowed=cancel_allowed,
        attempt_count=0,
        created_at=now,
        updated_at=now,
    )


class FakeCeleryTaskDao:
    def __init__(
        self,
        *,
        existing_payload_hash: str | None = None,
        existing_status: CeleryTaskStatus = CeleryTaskStatus.QUEUED,
        queued_status: CeleryTaskStatus = CeleryTaskStatus.QUEUED,
        dispatch_failure_status: CeleryTaskStatus = CeleryTaskStatus.FAILED,
        dispatch_failure_marked: bool = True,
        cancellation_status: CeleryTaskStatus = CeleryTaskStatus.CANCELLED,
        cancellation_cancel_allowed: bool = True,
        claim_succeeds: bool = True,
        finish_succeeds: bool = True,
        acknowledge_succeeds: bool = False,
        disallow_succeeds: bool = True,
    ) -> None:
        self.existing_payload_hash = existing_payload_hash
        self.existing_status = existing_status
        self.queued_status = queued_status
        self.dispatch_failure_status = dispatch_failure_status
        self.dispatch_failure_marked = dispatch_failure_marked
        self.cancellation_status = cancellation_status
        self.cancellation_cancel_allowed = cancellation_cancel_allowed
        self.claim_succeeds = claim_succeeds
        self.finish_succeeds = finish_succeeds
        self.acknowledge_succeeds = acknowledge_succeeds
        self.disallow_succeeds = disallow_succeeds
        self.reserve_calls: list[dict] = []
        self.queued_calls: list[dict] = []
        self.dispatch_failed_calls: list[dict] = []
        self.finish_calls: list[dict] = []
        self.acknowledge_calls: list[dict] = []
        self.claim_calls: list[dict] = []
        self.disallow_calls: list[dict] = []

    async def reserve_task(self, **kwargs):
        self.reserve_calls.append(kwargs)
        created = self.existing_payload_hash is None
        return (
            _record(
                record_id=kwargs["record_id"] if created else EXISTING_RECORD_ID,
                payload_hash=kwargs["payload_hash"]
                if created
                else self.existing_payload_hash,
                status=CeleryTaskStatus.SUBMITTING if created else self.existing_status,
            ),
            created,
        )

    async def mark_queued(self, **kwargs):
        self.queued_calls.append(kwargs)
        return _record(record_id=kwargs["record_id"], status=self.queued_status)

    async def mark_dispatch_failed(self, **kwargs):
        self.dispatch_failed_calls.append(kwargs)
        return (
            _record(
                record_id=kwargs["record_id"],
                status=self.dispatch_failure_status,
            ),
            self.dispatch_failure_marked,
        )

    async def request_cancellation(self, **_kwargs):
        return _record(
            status=self.cancellation_status,
            cancel_allowed=self.cancellation_cancel_allowed,
        )

    async def claim_execution(self, **kwargs):
        self.claim_calls.append(kwargs)
        return _record(status=CeleryTaskStatus.RUNNING) if self.claim_succeeds else None

    async def finish_execution(self, **kwargs) -> bool:
        self.finish_calls.append(kwargs)
        return self.finish_succeeds

    async def acknowledge_cancellation(self, **kwargs) -> bool:
        self.acknowledge_calls.append(kwargs)
        return self.acknowledge_succeeds

    async def disallow_cancellation(self, **kwargs) -> bool:
        self.disallow_calls.append(kwargs)
        return self.disallow_succeeds


class FakeCeleryClient:
    def __init__(
        self,
        *,
        submit_error: Exception | None = None,
        revoke_error: Exception | None = None,
    ) -> None:
        self.submit_error = submit_error
        self.revoke_error = revoke_error
        self.calls: list[dict] = []
        self.revoke_calls: list[tuple[str, bool]] = []

    async def async_submit(self, **kwargs):
        self.calls.append(kwargs)
        if self.submit_error is not None:
            raise self.submit_error
        return object()

    async def async_revoke(self, task_id: str, terminate: bool = False) -> None:
        self.revoke_calls.append((task_id, terminate))
        if self.revoke_error is not None:
            raise self.revoke_error


def _new_service(dao: FakeCeleryTaskDao, client: FakeCeleryClient) -> CeleryTaskService:
    return CeleryTaskService(
        dao=dao,  # type: ignore[arg-type]
        session_provider=_session_provider,  # type: ignore[arg-type]
        celery_client=client,  # type: ignore[arg-type]
    )


def test_celery_task_record_uses_new_model_mixin_contract() -> None:
    columns = set(CeleryTaskRecord.get_column_names())

    assert issubclass(CeleryTaskRecord, ModelMixin)
    assert {
        "id",
        "scope",
        "queue",
        "execution_token",
        "attempt_count",
        "queued_deadline_at",
        "hard_deadline_at",
        "fence_expires_at",
        "cancel_allowed",
        "started_at",
        "finished_at",
        "error_code",
        "error_summary",
    }.issubset(columns)
    assert {
        "idempotency_expires_at",
        "execution_timeout_seconds",
        "lease_owner",
        "lease_expires_at",
        "error_type",
        "error_message",
    }.isdisjoint(columns)
    assert CeleryTaskRecord.creator_id.property.columns[0].nullable is True
    assert (
        CeleryTaskRecord.queued_deadline_at.property.columns[0].type.timezone is False
    )


def test_canonical_payload_is_stable_for_object_key_order() -> None:
    first = canonical_task_payload(
        task_name="task.name",
        args=(1,),
        kwargs={"b": 2, "a": 1},
        queue="queue",
        options={},
    )
    second = canonical_task_payload(
        task_name="task.name",
        args=(1,),
        kwargs={"a": 1, "b": 2},
        queue="queue",
        options={},
    )

    assert first == second


@pytest.mark.asyncio
async def test_submit_once_marks_task_queued_without_countdown() -> None:
    dao = FakeCeleryTaskDao()
    client = FakeCeleryClient()
    service = _new_service(dao, client)

    result = await service.submit_once(
        task_name="task.name",
        trace_id="trace-1",
        scope="user:1",
        idempotency_key="request-1",
        args=(1, 2),
        queue="celery_queue",
    )

    assert result.created is True
    assert result.status is CeleryTaskStatus.QUEUED
    assert dao.reserve_calls[0]["queue"] == "celery_queue"
    assert dao.queued_calls[0]["record_id"] == result.record_id
    assert client.calls == [
        {
            "task_name": "task.name",
            "trace_id": "trace-1",
            "task_id": result.task_id,
            "args": (result.record_id, "user:1", 1, 2),
            "kwargs": {},
            "queue": "celery_queue",
            "retry": False,
        }
    ]


@pytest.mark.asyncio
async def test_submit_once_preserves_worker_race_state() -> None:
    service = _new_service(
        FakeCeleryTaskDao(queued_status=CeleryTaskStatus.RUNNING),
        FakeCeleryClient(),
    )

    result = await service.submit_once(
        task_name="task.name",
        trace_id="trace-1",
        scope="user:1",
        idempotency_key="request-1",
        queue="celery_queue",
    )

    assert result.status is CeleryTaskStatus.RUNNING


@pytest.mark.asyncio
async def test_submit_once_returns_existing_record_without_republishing() -> None:
    payload = canonical_task_payload(
        task_name="task.name",
        args=(1, 2),
        kwargs={},
        queue="celery_queue",
        options={},
    )
    dao = FakeCeleryTaskDao(existing_payload_hash=hashlib.sha256(payload).hexdigest())
    client = FakeCeleryClient()
    service = _new_service(dao, client)

    result = await service.submit_once(
        task_name="task.name",
        trace_id="trace-1",
        scope="user:1",
        idempotency_key="request-1",
        args=(1, 2),
        queue="celery_queue",
    )

    assert result.record_id == EXISTING_RECORD_ID
    assert result.created is False
    assert client.calls == []


@pytest.mark.asyncio
async def test_submit_once_rejects_same_key_with_different_payload() -> None:
    service = _new_service(
        FakeCeleryTaskDao(existing_payload_hash="different"), FakeCeleryClient()
    )

    with pytest.raises(AppException) as exc_info:
        await service.submit_once(
            task_name="task.name",
            trace_id="trace-1",
            scope="user:1",
            idempotency_key="request-1",
            queue="celery_queue",
        )

    assert exc_info.value.error is errors.IdempotencyConflict


@pytest.mark.asyncio
async def test_submit_once_marks_unclaimed_publish_failure() -> None:
    dao = FakeCeleryTaskDao()
    service = _new_service(dao, FakeCeleryClient(submit_error=ConnectionError()))

    with pytest.raises(AppException) as exc_info:
        await service.submit_once(
            task_name="task.name",
            trace_id="trace-1",
            scope="user:1",
            idempotency_key="request-1",
            queue="celery_queue",
        )

    assert exc_info.value.error is errors.ServiceUnavailable
    assert len(dao.dispatch_failed_calls) == 1


@pytest.mark.asyncio
async def test_submit_once_preserves_advanced_state_on_publish_exception() -> None:
    dao = FakeCeleryTaskDao(
        dispatch_failure_status=CeleryTaskStatus.RUNNING,
        dispatch_failure_marked=False,
    )
    service = _new_service(dao, FakeCeleryClient(submit_error=ConnectionError()))

    result = await service.submit_once(
        task_name="task.name",
        trace_id="trace-1",
        scope="user:1",
        idempotency_key="request-1",
        queue="celery_queue",
    )

    assert result.status is CeleryTaskStatus.RUNNING


@pytest.mark.asyncio
async def test_cancel_task_persists_then_best_effort_revokes() -> None:
    dao = FakeCeleryTaskDao(cancellation_status=CeleryTaskStatus.CANCELLING)
    client = FakeCeleryClient(revoke_error=ConnectionError())
    service = _new_service(dao, client)

    result = await service.cancel_task(record_id=TEST_RECORD_ID, scope="user:1")

    assert result.status is CeleryTaskStatus.CANCELLING
    assert client.revoke_calls == [(f"task_{TEST_RECORD_ID}", False)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [
        CeleryTaskStatus.SUCCEEDED,
        CeleryTaskStatus.FAILED,
        CeleryTaskStatus.ORPHANED,
    ],
)
async def test_cancel_unsafe_task_state_returns_state_conflict(
    status: CeleryTaskStatus,
) -> None:
    client = FakeCeleryClient()
    service = _new_service(
        FakeCeleryTaskDao(cancellation_status=status),
        client,
    )

    with pytest.raises(AppException) as exc_info:
        await service.cancel_task(record_id=TEST_RECORD_ID, scope="user:1")

    assert exc_info.value.error is errors.TaskStateConflict
    assert client.revoke_calls == []


@pytest.mark.asyncio
async def test_cancel_running_task_when_cancel_disallowed_returns_state_conflict() -> (
    None
):
    client = FakeCeleryClient()
    service = _new_service(
        FakeCeleryTaskDao(
            cancellation_status=CeleryTaskStatus.RUNNING,
            cancellation_cancel_allowed=False,
        ),
        client,
    )

    with pytest.raises(AppException) as exc_info:
        await service.cancel_task(record_id=TEST_RECORD_ID, scope="user:1")

    assert exc_info.value.error is errors.TaskStateConflict
    assert client.revoke_calls == []


@pytest.mark.asyncio
async def test_claim_uses_execution_token_and_combined_deadline() -> None:
    dao = FakeCeleryTaskDao()
    service = _new_service(dao, FakeCeleryClient())

    claimed = await service.claim(
        record_id=TEST_RECORD_ID,
        task_name="task.name",
        scope="user:1",
        execution_token="delivery-token",
        execution_timeout_seconds=25,
        deadline_grace_seconds=30,
    )

    assert claimed is True
    assert dao.claim_calls == [
        {
            "record_id": TEST_RECORD_ID,
            "task_name": "task.name",
            "scope": "user:1",
            "execution_token": "delivery-token",
            "hard_deadline_seconds": 55,
        }
    ]


@pytest.mark.asyncio
async def test_disallow_cancellation_uses_running_token_fence() -> None:
    dao = FakeCeleryTaskDao()
    service = _new_service(dao, FakeCeleryClient())

    updated = await service.disallow_cancellation(
        record_id=TEST_RECORD_ID,
        execution_token="delivery-token",
    )

    assert updated is True
    assert dao.disallow_calls == [
        {"record_id": TEST_RECORD_ID, "execution_token": "delivery-token"}
    ]


@pytest.mark.asyncio
async def test_success_completion_uses_running_token_fence() -> None:
    dao = FakeCeleryTaskDao()
    service = _new_service(dao, FakeCeleryClient())

    completed = await service.succeed(
        record_id=TEST_RECORD_ID,
        execution_token="delivery-token",
    )

    assert completed is True
    assert dao.finish_calls[0]["status"] is CeleryTaskStatus.SUCCEEDED
    assert dao.finish_calls[0]["execution_token"] == "delivery-token"


@pytest.mark.asyncio
async def test_completion_losing_race_to_cancellation_returns_false() -> None:
    dao = FakeCeleryTaskDao(
        finish_succeeds=False,
        acknowledge_succeeds=True,
    )
    service = _new_service(dao, FakeCeleryClient())

    completed = await service.succeed(
        record_id=TEST_RECORD_ID,
        execution_token="delivery-token",
    )

    assert completed is False
    assert dao.acknowledge_calls == [
        {"record_id": TEST_RECORD_ID, "execution_token": "delivery-token"}
    ]
