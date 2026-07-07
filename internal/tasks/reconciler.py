from uuid import uuid4

from internal.config import settings
from internal.dao.celery_task import new_celery_task_dao
from internal.infra.celery import celery_client, run_in_async
from internal.tasks.constants import (
    EXECUTION_RECONCILER_TASK_NAME,
    QUEUED_RECONCILER_TASK_NAME,
    SUBMITTING_RECONCILER_TASK_NAME,
)


@celery_client.app.task(
    name=SUBMITTING_RECONCILER_TASK_NAME,
    max_retries=0,
    soft_time_limit=50,
    time_limit=60,
)
def fail_stale_submitting() -> dict[str, int]:
    """将发布确认超时的 SUBMITTING 任务快速失败。"""

    async def _execute() -> dict[str, int]:
        dao = new_celery_task_dao()
        record_ids = await dao.fail_stale_submitting(
            batch_size=settings.CELERY_RECONCILER_BATCH_SIZE,
            stale_seconds=settings.CELERY_PUBLISH_CONFIRM_TIMEOUT_SECONDS,
        )
        return {"submitting_failed_count": len(record_ids)}

    return run_in_async(
        _execute, trace_id=f"celery-submitting-reconciler-{uuid4().hex}"
    )


@celery_client.app.task(
    name=QUEUED_RECONCILER_TASK_NAME,
    max_retries=0,
    soft_time_limit=50,
    time_limit=60,
)
def fail_expired_queued() -> dict[str, int]:
    """将超过启动 deadline 的 QUEUED 任务快速失败。"""

    async def _execute() -> dict[str, int]:
        dao = new_celery_task_dao()
        record_ids = await dao.fail_expired_queued(
            batch_size=settings.CELERY_RECONCILER_BATCH_SIZE
        )
        return {"queued_failed_count": len(record_ids)}

    return run_in_async(_execute, trace_id=f"celery-queued-reconciler-{uuid4().hex}")


@celery_client.app.task(
    name=EXECUTION_RECONCILER_TASK_NAME,
    max_retries=0,
    soft_time_limit=50,
    time_limit=60,
)
def reconcile_expired_execution() -> dict[str, int]:
    """收敛超过 hard deadline 的 RUNNING/CANCELLING 任务。"""

    async def _execute() -> dict[str, int]:
        dao = new_celery_task_dao()
        record_ids = await dao.reconcile_expired_execution(
            batch_size=settings.CELERY_RECONCILER_BATCH_SIZE
        )
        return {"execution_reconciled_count": len(record_ids)}

    return run_in_async(_execute, trace_id=f"celery-execution-reconciler-{uuid4().hex}")
