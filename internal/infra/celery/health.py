"""FastAPI 进程使用的 Celery broker 健康检查。"""

from internal.config import settings
from internal.infra.celery.application import celery_app
from internal.tasks.scheduler import CELERY_INCLUDE_MODULES, CELERY_TASK_ROUTES
from pkg.logger import logger


def check_celery_health() -> None:
    """检查 broker 连通性；失败只记录日志，不阻断 API 启动。"""
    logger.info("Initializing Celery integration...")
    logger.info(f"Celery modules included: {CELERY_INCLUDE_MODULES}")
    logger.info(f"Celery routes: {CELERY_TASK_ROUTES}")

    try:
        with celery_app.connection_or_acquire() as connection:
            connection.ensure_connection(max_retries=1)
        logger.info(f"Celery broker ({settings.redis_url}) connected successfully.")
    except Exception as exc:
        logger.error(f"Celery broker connection failed: {exc}")
