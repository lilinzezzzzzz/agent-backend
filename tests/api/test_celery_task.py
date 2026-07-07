from datetime import UTC, datetime

import pytest

from internal.controllers.api import celery_task as celery_task_controller
from internal.schemas.celery_task import (
    CeleryTaskCancelDTO,
    CeleryTaskDetailDTO,
    CeleryTaskDispatchDTO,
    CeleryTaskStatus,
    IdempotentSumCreateReqSchema,
)


class FakeCeleryTaskService:
    async def submit_once(self, **kwargs) -> CeleryTaskDispatchDTO:
        assert kwargs["scope"] == "user:999"
        assert kwargs["trace_id"] == "test-trace-id"
        assert kwargs["args"] == (7, 11)
        return CeleryTaskDispatchDTO(
            record_id=123,
            status=CeleryTaskStatus.QUEUED,
            created=True,
        )

    async def get_task(self, *, record_id: int, scope: str) -> CeleryTaskDetailDTO:
        assert record_id == 123
        assert scope == "user:999"
        now = datetime.now(UTC)
        return CeleryTaskDetailDTO(
            record_id=record_id,
            task_name="internal.tasks.celery_idempotency_demo.sum_numbers",
            queue="celery_demo_queue",
            status=CeleryTaskStatus.SUCCEEDED,
            trace_id="trace-123",
            attempt_count=1,
            queued_deadline_at=None,
            hard_deadline_at=None,
            started_at=now,
            finished_at=now,
            error_code=None,
            error_summary=None,
            created_at=now,
            updated_at=now,
        )

    async def cancel_task(self, *, record_id: int, scope: str) -> CeleryTaskCancelDTO:
        assert record_id == 123
        assert scope == "user:999"
        return CeleryTaskCancelDTO(
            record_id=record_id,
            status=CeleryTaskStatus.CANCELLING,
        )


@pytest.mark.asyncio
async def test_create_idempotent_sum_uses_authenticated_scope() -> None:
    response = await celery_task_controller.create_idempotent_sum_task(
        IdempotentSumCreateReqSchema(idempotency_key="request-1", x=7, y=11),
        FakeCeleryTaskService(),  # type: ignore[arg-type]
    )

    assert response.data == {
        "record_id": 123,
        "task_id": "task_123",
        "status": "QUEUED",
        "created": True,
    }


@pytest.mark.asyncio
async def test_get_celery_task_uses_authenticated_scope() -> None:
    response = await celery_task_controller.get_celery_task(
        123,
        FakeCeleryTaskService(),  # type: ignore[arg-type]
    )

    assert response.data["record_id"] == 123
    assert response.data["status"] == "SUCCEEDED"
    assert response.data["queue"] == "celery_demo_queue"
    assert response.data["attempt_count"] == 1


@pytest.mark.asyncio
async def test_cancel_celery_task_uses_authenticated_scope() -> None:
    response = await celery_task_controller.cancel_celery_task(
        123,
        FakeCeleryTaskService(),  # type: ignore[arg-type]
    )

    assert response.data == {
        "record_id": 123,
        "task_id": "task_123",
        "status": "CANCELLING",
    }
