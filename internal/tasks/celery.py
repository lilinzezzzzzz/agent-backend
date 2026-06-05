"""Celery 任务定义

任务分类：
1. 独立业务逻辑：调用 services 层，任务本身只是调度包装
2. 协调多个 services：组合调用多个已有的 services
3. 纯技术运维：心跳检测、缓存预热等，无业务逻辑

注意：所有业务逻辑应放在 services 层，此处只做调度和协调
"""


from internal.utils.celery import celery_client, run_in_async
from pkg.logger import logger

# =========================================================
# 1. 独立业务逻辑任务示例
# =========================================================


@celery_client.app.task(bind=True, name="internal.tasks.celery_tasks.clean_expired_tokens")
def clean_expired_tokens(self):
    """
    清理过期 token（独立业务逻辑）

    业务逻辑在 services/token.py 中，此处只做调度包装
    """

    async def _clean_tokens():
        # TODO: 调用 TokenService.clean_expired_tokens()
        # from internal.services.token import TokenService
        # service = TokenService()
        # return await service.clean_expired_tokens()
        logger.info("Cleaning expired tokens...")
        return {"deleted_count": 0}

    return run_in_async(_clean_tokens, trace_id=f"clean_tokens_{self.request.id}")


@celery_client.app.task(bind=True, name="internal.tasks.celery_tasks.generate_daily_report")
def generate_daily_report(self, report_type: str):
    """
    生成日报表（独立业务逻辑）
    """
    from datetime import UTC, datetime

    async def _generate():
        # TODO: 调用 ReportService.generate_report()
        # from internal.services.report import ReportService
        # service = ReportService()
        # return await service.generate_report(report_type, datetime.now(UTC))
        logger.info(f"Generating {report_type} report for {datetime.now(UTC).date()}")
        return {"status": "generated", "report_type": report_type}

    return run_in_async(_generate, trace_id=f"report_{self.request.id}")


# =========================================================
# 2. 协调多个 services 任务示例
# =========================================================


@celery_client.app.task(bind=True, name="internal.tasks.celery_tasks.send_welcome_email")
def send_welcome_email(self, user_id: int):
    """
    发送欢迎邮件（协调多个 services）

    组合调用 UserService + EmailService
    """

    async def _send():
        # TODO: 协调多个 services
        # from internal.services.user import UserService
        # from internal.services.email import EmailService
        #
        # user_service = UserService()
        # email_service = EmailService()
        #
        # user = await user_service.get_user(user_id)
        # if user:
        #     await email_service.send_welcome(user.email, user.name)
        #     await user_service.mark_welcome_sent(user_id)
        logger.info(f"Sending welcome email to user {user_id}")
        return {"user_id": user_id, "email_sent": True}

    return run_in_async(_send, trace_id=f"welcome_email_{user_id}")


@celery_client.app.task(bind=True, name="internal.tasks.celery_tasks.sync_user_data")
def sync_user_data(self, user_id: int):
    """
    同步用户数据到第三方系统（协调多个 services）
    """

    async def _sync():
        # TODO: 协调 UserService + ThirdPartySyncService + NotificationService
        logger.info(f"Syncing user {user_id} data to external systems")
        return {"user_id": user_id, "synced": True}

    return run_in_async(_sync, trace_id=f"sync_user_{user_id}")


# =========================================================
# 3. 纯技术运维任务示例
# =========================================================


@celery_client.app.task(name="internal.tasks.celery_tasks.heartbeat")
def heartbeat():
    """
    心跳检测（纯技术运维）
    无业务逻辑，不需要 services
    """
    logger.info("Celery worker heartbeat - alive")
    return {"status": "alive"}


@celery_client.app.task(bind=True, name="internal.tasks.celery_tasks.warmup_cache")
def warmup_cache(self):
    """
    缓存预热（纯技术运维）
    """

    async def _warmup():
        # TODO: 直接操作业务缓存预热
        # from internal.cache.auth import new_auth_cache
        # _auth_cache = new_auth_cache()
        # await _auth_cache.set_user_metadata("warmup_time", {"time": datetime.now(UTC).isoformat()})
        logger.info("Cache warmup completed")
        return {"status": "warmed"}

    return run_in_async(_warmup, trace_id=f"warmup_{self.request.id}")


# =========================================================
# 4. 示例任务（兼容旧代码）
# =========================================================


@celery_client.app.task(bind=True, name="internal.tasks.celery_tasks.number_sum")
def number_sum(self, x: int | list[int], y: int):
    """
    示例异步任务
    支持处理单个数字加法，或 Chord 回调的列表求和
    """
    try:
        # 兼容 Chord 回调逻辑
        if isinstance(x, list):
            logger.info(f"Received list input from chord: {x}, aggregating...")
            x = sum(x)

        result = x + y
        logger.info(f"计算两个数字的和: {x} + {y} = {result}")
        return result
    except Exception as e:
        logger.error(f"Task failed: {e}")
        raise self.retry(exc=e, countdown=5, max_retries=3) from e
