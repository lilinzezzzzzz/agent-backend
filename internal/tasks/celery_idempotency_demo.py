from collections.abc import Mapping
from uuid import uuid4

from celery.exceptions import Ignore

from internal.config import settings
from internal.infra.celery import celery_client, run_in_async
from internal.services.celery_task import new_celery_task_service
from internal.tasks.constants import (
    IDEMPOTENT_SUM_TASK_NAME,
    IDEMPOTENT_SUM_TIMEOUT_SECONDS,
)


def _resolve_trace_id(task) -> str:
    headers = getattr(task.request, "headers", None)
    if isinstance(headers, Mapping):
        trace_id = headers.get("trace_id")
        if isinstance(trace_id, str) and trace_id:
            return trace_id

    raise ValueError("Celery task missing required trace_id header")


@celery_client.app.task(
    bind=True,
    name=IDEMPOTENT_SUM_TASK_NAME,
    max_retries=0,
    soft_time_limit=25,
    time_limit=IDEMPOTENT_SUM_TIMEOUT_SECONDS,
)
def sum_numbers(self, record_id: int, scope: str, x: int, y: int) -> dict[str, int]:
    """执行一次经 PostgreSQL claim 门禁的加法 demo。"""

    async def _execute() -> dict[str, int]:
        execution_token = uuid4().hex
        service = new_celery_task_service()
        claimed = await service.claim(
            record_id=record_id,
            task_name=IDEMPOTENT_SUM_TASK_NAME,
            scope=scope,
            execution_token=execution_token,
            execution_timeout_seconds=IDEMPOTENT_SUM_TIMEOUT_SECONDS,
            deadline_grace_seconds=settings.CELERY_EXECUTION_DEADLINE_GRACE_SECONDS,
        )
        if not claimed:
            raise Ignore()

        try:
            if await service.acknowledge_cancellation(
                record_id=record_id, execution_token=execution_token
            ):
                raise Ignore()
            result = {"x": x, "y": y, "result": x + y}
            if await service.acknowledge_cancellation(
                record_id=record_id, execution_token=execution_token
            ):
                raise Ignore()
        except Ignore:
            raise
        except Exception as exc:
            failed = await service.fail(
                record_id=record_id,
                execution_token=execution_token,
                exc=exc,
            )
            if not failed:
                raise Ignore() from exc
            raise
        if not await service.succeed(
            record_id=record_id, execution_token=execution_token
        ):
            raise Ignore()
        return result

    return run_in_async(_execute, trace_id=_resolve_trace_id(self))
