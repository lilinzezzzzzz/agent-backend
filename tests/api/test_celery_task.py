from datetime import UTC, datetime
from uuid import UUID

import pytest

from internal.controllers.api import celery_task as celery_task_controller
from internal.schemas.celery_task import (
    CeleryTaskCancelDTO,
    CeleryTaskDetailDTO,
    CeleryTaskDispatchDTO,
    CeleryTaskStatus,
    IdempotentSumCreateReqSchema,
)

TEST_RECORD_ID = UUID("00000000-0000-7000-8000-000000000123")
TEST_USER_ID = UUID("00000000-0000-7000-8000-000000000999")
TEST_SCOPE = f"user:{TEST_USER_ID}"


class FakeCeleryTaskService:
    async def submit_once(self, **kwargs) -> CeleryTaskDispatchDTO:
        assert kwargs["scope"] == TEST_SCOPE
        assert kwargs["trace_id"] == "test-trace-id"
        assert kwargs["args"] == (7, 11)
        return CeleryTaskDispatchDTO(
            record_id=TEST_RECORD_ID,
            status=CeleryTaskStatus.QUEUED,
            created=True,
        )

    async def get_task(self, *, record_id: UUID, scope: str) -> CeleryTaskDetailDTO:
        assert record_id == TEST_RECORD_ID
        assert scope == TEST_SCOPE
        now = datetime.now(UTC)
        return CeleryTaskDetailDTO(
            record_id=record_id,
            task_name="internal.tasks.celery_idempotency_demo.sum_numbers",
            queue="celery_demo_queue",
            status=CeleryTaskStatus.SUCCEEDED,
            cancel_allowed=False,
            trace_id="trace-123",
            attempt_count=1,
            queued_deadline_at=None,
            hard_deadline_at=None,
            fence_expires_at=None,
            started_at=now,
            finished_at=now,
            error_code=None,
            error_summary=None,
            created_at=now,
            updated_at=now,
        )

    async def cancel_task(self, *, record_id: UUID, scope: str) -> CeleryTaskCancelDTO:
        assert record_id == TEST_RECORD_ID
        assert scope == TEST_SCOPE
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
        "record_id": TEST_RECORD_ID,
        "task_id": f"task_{TEST_RECORD_ID}",
        "status": "QUEUED",
        "created": True,
    }


@pytest.mark.asyncio
async def test_get_celery_task_uses_authenticated_scope() -> None:
    response = await celery_task_controller.get_celery_task(
        TEST_RECORD_ID,
        FakeCeleryTaskService(),  # type: ignore[arg-type]
    )

    assert response.data["record_id"] == TEST_RECORD_ID
    assert response.data["status"] == "SUCCEEDED"
    assert response.data["queue"] == "celery_demo_queue"
    assert response.data["cancel_allowed"] is False
    assert response.data["attempt_count"] == 1


@pytest.mark.asyncio
async def test_cancel_celery_task_uses_authenticated_scope() -> None:
    response = await celery_task_controller.cancel_celery_task(
        TEST_RECORD_ID,
        FakeCeleryTaskService(),  # type: ignore[arg-type]
    )

    assert response.data == {
        "record_id": TEST_RECORD_ID,
        "task_id": f"task_{TEST_RECORD_ID}",
        "status": "CANCELLING",
    }
