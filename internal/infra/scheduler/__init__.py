"""APScheduler 定时任务管理

任务定义在 internal/tasks/ 目录，此处仅负责调度注册
"""

from pkg.logger import logger
from pkg.scheduler import ApsSchedulerManager
from pkg.lazy_proxy import lazy_proxy

_apscheduler_manager: ApsSchedulerManager | None = None


def init_apscheduler():
    global _apscheduler_manager
    logger.info("Initializing APScheduler...")
    if _apscheduler_manager is not None:
        logger.warning("APScheduler has already been initialized.")
        return

    _apscheduler_manager = ApsSchedulerManager(timezone="UTC", max_instances=50)
    _register_tasks(_apscheduler_manager)
    logger.info("APScheduler initialized successfully.")


def _register_tasks(manager: ApsSchedulerManager):
    """注册定时任务

    从 internal/tasks 导入任务函数并注册到 APScheduler
    """
    from internal.tasks.celery import number_sum

    # 示例：每 15 分钟执行一次
    manager.register_cron(number_sum, minute="*/15", second=0)

    # 其他任务示例（按需启用）
    # from internal.tasks.celery import clean_expired_tokens, heartbeat, warmup_cache
    # manager.register_interval(heartbeat, seconds=30)
    # manager.register_cron(clean_expired_tokens, hour=3, minute=0)


def _get_apscheduler_manager() -> ApsSchedulerManager:
    if _apscheduler_manager is None:
        raise RuntimeError("APScheduler not initialized. Call init_apscheduler() first.")
    return _apscheduler_manager


apscheduler_manager = lazy_proxy(_get_apscheduler_manager)

__all__ = ["apscheduler_manager", "init_apscheduler"]
