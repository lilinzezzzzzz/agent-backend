"""任务调度层。

职责：
- 定时任务配置（Beat Schedule）

注意：业务逻辑应放在 services 层，此处只做调度和协调
"""

from internal.tasks.scheduler import (
    CELERY_INCLUDE_MODULES,
    CELERY_TASK_ROUTES,
    STATIC_BEAT_SCHEDULE,
)

__all__ = [
    "CELERY_INCLUDE_MODULES",
    "CELERY_TASK_ROUTES",
    "STATIC_BEAT_SCHEDULE",
]
