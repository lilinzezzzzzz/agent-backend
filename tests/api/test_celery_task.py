from datetime import UTC, datetime

import pytest

from internal.controllers.api import celery_task as celery_task_controller
from internal.schemas.celery_task import (
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
            status=CeleryTaskStatus.PUBLISHED,
            created=True,
        )

    async def get_task(self, *, record_id: int, scope: str) -> CeleryTaskDetailDTO:
        assert record_id == 123
        assert scope == "user:999"
        now = datetime.now(UTC)
        return CeleryTaskDetailDTO(
            record_id=record_id,
            task_name="internal.tasks.celery_idempotency_demo.sum_numbers",
            status=CeleryTaskStatus.SUCCEEDED,
            trace_id="trace-123",
            error_type=None,
            error_message=None,
            created_at=now,
            updated_at=now,
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
        "status": "PUBLISHED",
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
