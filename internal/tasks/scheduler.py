"""定时任务调度配置

包含：
- CELERY_INCLUDE_MODULES: 需要加载的任务模块
- CELERY_TASK_ROUTES: 任务路由配置
- STATIC_BEAT_SCHEDULE: 静态定时任务表
"""

from celery.schedules import crontab

# =========================================================
# 任务模块配置
# =========================================================

# 需要加载的任务模块 (Python 模块路径)
CELERY_INCLUDE_MODULES = [
    "internal.tasks.celery",
]

# 任务路由配置 (决定任务去哪个队列)
CELERY_TASK_ROUTES = {
    # Celery 任务统一走 celery_queue
    "internal.tasks.celery_tasks.*": {"queue": "celery_queue"},
    # 定时任务统一走 cron_queue
    "task_sum_every_15_min": {"queue": "cron_queue"},
}


# =========================================================
# 静态定时任务表 (Beat Schedule)
# =========================================================

# 注意：Key 是任务的唯一标识，Value 中的 'task' 必须与 @task(name=...) 一致
STATIC_BEAT_SCHEDULE = {
    # 案例 1：Cron 风格 - 每隔 15 分钟执行一次
    "task_sum_every_15_min": {
        "task": "internal.tasks.celery_tasks.number_sum",
        "schedule": crontab(minute="*/15"),
        "args": (10, 20),
    },
    # 案例 2：Interval 风格 - 每 30 秒执行一次
    "task_heartbeat_30s": {
        "task": "internal.tasks.celery_tasks.number_sum",
        "schedule": 30.0,
        "args": (1, 1),
    },
}
