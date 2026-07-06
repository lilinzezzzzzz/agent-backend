from uuid import uuid4

from internal.config import settings
from internal.dao.celery_task import new_celery_task_dao
from internal.infra.celery import celery_client, run_in_async
from internal.tasks.constants import (
    ORPHAN_DETECTOR_TASK_NAME,
    PUBLISH_RECONCILER_TASK_NAME,
    RUNNING_RECONCILER_TASK_NAME,
)


@celery_client.app.task(
    name=PUBLISH_RECONCILER_TASK_NAME,
    max_retries=0,
    soft_time_limit=50,
    time_limit=60,
)
def fail_stale_pending_publish() -> dict[str, int]:
    """将超时未确认发布的任务快速失败，不自动重新提交。"""

    async def _execute() -> dict[str, int]:
        dao = new_celery_task_dao()
        record_ids = await dao.fail_stale_pending_publish(
            batch_size=settings.CELERY_RECONCILER_BATCH_SIZE,
            stale_seconds=settings.CELERY_PUBLISH_CONFIRM_TIMEOUT_SECONDS,
        )
        return {"publish_failed_count": len(record_ids)}

    return run_in_async(_execute, trace_id=f"celery-publish-reconciler-{uuid4().hex}")


@celery_client.app.task(
    name=RUNNING_RECONCILER_TASK_NAME, max_retries=0, soft_time_limit=50, time_limit=60
)
def reconcile_expired_running() -> dict[str, int]:
    """收敛过期 RUNNING 任务，不重试或执行业务逻辑。"""

    async def _execute() -> dict[str, int]:
        dao = new_celery_task_dao()
        record_ids = await dao.reconcile_expired_running(
            batch_size=settings.CELERY_RECONCILER_BATCH_SIZE
        )
        return {"reconciled_count": len(record_ids)}

    return run_in_async(_execute, trace_id=f"celery-running-reconciler-{uuid4().hex}")


@celery_client.app.task(
    name=ORPHAN_DETECTOR_TASK_NAME, max_retries=0, soft_time_limit=50, time_limit=60
)
def detect_orphaned_tasks() -> dict[str, int]:
    """标记超时未 claim 的 PUBLISHED 任务，不自动重投。"""

    async def _execute() -> dict[str, int]:
        dao = new_celery_task_dao()
        record_ids = await dao.detect_orphaned(
            batch_size=settings.CELERY_RECONCILER_BATCH_SIZE,
            orphan_seconds=settings.CELERY_PUBLISHED_ORPHAN_SECONDS,
        )
        return {"orphaned_count": len(record_ids)}

    return run_in_async(_execute, trace_id=f"celery-orphan-detector-{uuid4().hex}")
