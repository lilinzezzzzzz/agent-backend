import hashlib
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest

from internal.core import AppException, errors
from internal.models.celery_task import CeleryTaskRecord
from internal.schemas.celery_task import CeleryTaskStatus
from internal.services.celery_task import (
    CeleryTaskService,
    canonical_task_payload,
)
from pkg.database.base import ModelMixin


class FakeSession:
    @asynccontextmanager
    async def begin(self):
        yield


@asynccontextmanager
async def _session_provider():
    yield FakeSession()


class FakeCeleryTaskDao:
    def __init__(
        self,
        *,
        existing_payload_hash: str | None = None,
        existing_status: CeleryTaskStatus = CeleryTaskStatus.PUBLISHED,
    ) -> None:
        self.existing_payload_hash = existing_payload_hash
        self.existing_status = existing_status
        self.reserve_calls: list[dict] = []
        self.published_ids: list[int] = []
        self.failed_ids: list[int] = []

    async def reserve_task(self, **kwargs):
        self.reserve_calls.append(kwargs)
        created = self.existing_payload_hash is None
        return (
            CeleryTaskRecord(
                id=kwargs["record_id"] if created else 42,
                payload_hash=kwargs["payload_hash"]
                if created
                else self.existing_payload_hash,
                status=CeleryTaskStatus.PENDING_PUBLISH.value
                if created
                else self.existing_status.value,
            ),
            created,
        )

    async def mark_published(self, *, record_id: int) -> bool:
        self.published_ids.append(record_id)
        return True

    async def mark_publish_failed(self, *, record_id: int) -> bool:
        self.failed_ids.append(record_id)
        return True


class FakeCeleryClient:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict] = []

    async def async_submit(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return object()


def _new_service(dao: FakeCeleryTaskDao, client: FakeCeleryClient) -> CeleryTaskService:
    return CeleryTaskService(
        dao=dao,  # type: ignore[arg-type]
        session_provider=_session_provider,  # type: ignore[arg-type]
        celery_client=client,  # type: ignore[arg-type]
    )


def test_celery_task_record_task_id_uses_record_id() -> None:
    record = CeleryTaskRecord(id=123)

    assert record.task_id == "task_123"


def test_celery_task_record_integrates_model_mixin_columns() -> None:
    assert issubclass(CeleryTaskRecord, ModelMixin)
    assert {
        "id",
        "creator_id",
        "created_at",
        "updater_id",
        "updated_at",
        "deleted_at",
    }.issubset(CeleryTaskRecord.get_column_names())
    assert CeleryTaskRecord.creator_id.property.columns[0].nullable is True
    assert CeleryTaskRecord.created_at.property.columns[0].type.timezone is False
    assert CeleryTaskRecord.created_at.property.columns[0].nullable is False
    assert CeleryTaskRecord.updated_at.property.columns[0].type.timezone is False
    assert CeleryTaskRecord.updated_at.property.columns[0].nullable is True
    assert CeleryTaskRecord.lease_expires_at.property.columns[0].type.timezone is False
    assert (
        CeleryTaskRecord.idempotency_expires_at.property.columns[0].type.timezone
        is False
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


def test_canonical_payload_preserves_array_order() -> None:
    first = canonical_task_payload(
        task_name="task.name", args=(1, 2), kwargs={}, queue=None, options={}
    )
    second = canonical_task_payload(
        task_name="task.name", args=(2, 1), kwargs={}, queue=None, options={}
    )

    assert first != second


@pytest.mark.asyncio
async def test_submit_once_publishes_with_stable_task_id() -> None:
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
        execution_timeout_seconds=30,
        idempotency_expires_in=timedelta(days=30),
    )

    assert result.created is True
    assert result.status is CeleryTaskStatus.PUBLISHED
    assert dao.reserve_calls[0]["idempotency_expires_at"].tzinfo is None
    assert dao.published_ids == [result.record_id]
    assert client.calls == [
        {
            "task_name": "task.name",
            "trace_id": "trace-1",
            "task_id": result.task_id,
            "args": (result.record_id, "user:1", 1, 2),
            "kwargs": {},
            "queue": "celery_queue",
            "countdown": 2,
            "retry": False,
        }
    ]


@pytest.mark.asyncio
async def test_submit_once_returns_existing_record_without_republishing() -> None:
    payload = canonical_task_payload(
        task_name="task.name", args=(1, 2), kwargs={}, queue=None, options={}
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
        execution_timeout_seconds=30,
        idempotency_expires_in=timedelta(days=30),
    )

    assert result.record_id == 42
    assert result.created is False
    assert client.calls == []


@pytest.mark.asyncio
async def test_submit_once_rejects_same_key_with_different_payload() -> None:
    dao = FakeCeleryTaskDao(existing_payload_hash="different")
    service = _new_service(dao, FakeCeleryClient())

    with pytest.raises(AppException) as exc_info:
        await service.submit_once(
            task_name="task.name",
            trace_id="trace-1",
            scope="user:1",
            idempotency_key="request-1",
            args=(1, 2),
            execution_timeout_seconds=30,
            idempotency_expires_in=timedelta(days=30),
        )

    assert exc_info.value.error is errors.IdempotencyConflict


@pytest.mark.asyncio
async def test_submit_once_marks_publish_failure_without_retry() -> None:
    dao = FakeCeleryTaskDao()
    service = _new_service(dao, FakeCeleryClient(error=ConnectionError()))

    with pytest.raises(AppException) as exc_info:
        await service.submit_once(
            task_name="task.name",
            trace_id="trace-1",
            scope="user:1",
            idempotency_key="request-1",
            args=(1, 2),
            execution_timeout_seconds=30,
            idempotency_expires_in=timedelta(days=30),
        )

    assert exc_info.value.error is errors.ServiceUnavailable
    assert len(dao.failed_ids) == 1
    assert dao.published_ids == []
