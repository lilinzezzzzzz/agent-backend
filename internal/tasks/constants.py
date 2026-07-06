IDEMPOTENT_SUM_TASK_NAME = "internal.tasks.celery_idempotency_demo.sum_numbers"
IDEMPOTENT_SUM_QUEUE = "celery_queue"
IDEMPOTENT_SUM_TIMEOUT_SECONDS = 30

RUNNING_RECONCILER_TASK_NAME = "internal.tasks.celery_task_running_reconciler"
ORPHAN_DETECTOR_TASK_NAME = "internal.tasks.celery_task_orphan_detector"
PUBLISH_RECONCILER_TASK_NAME = "internal.tasks.celery_task_publish_reconciler"
