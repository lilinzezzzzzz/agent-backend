IDEMPOTENT_SUM_TASK_NAME = "internal.tasks.celery_idempotency_demo.sum_numbers"
IDEMPOTENT_SUM_QUEUE = "celery_queue"
IDEMPOTENT_SUM_TIMEOUT_SECONDS = 30

SUBMITTING_RECONCILER_TASK_NAME = "internal.tasks.celery_task_submitting_reconciler"
QUEUED_RECONCILER_TASK_NAME = "internal.tasks.celery_task_queued_reconciler"
EXECUTION_RECONCILER_TASK_NAME = "internal.tasks.celery_task_execution_reconciler"
