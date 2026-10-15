"""Celery application composition root。"""

from celery import Celery
from kombu import Exchange, Queue

from internal.config import settings
from internal.infra.celery.lifecycle import worker_shutdown, worker_startup
from internal.tasks.scheduler import (
    CELERY_DEFAULT_QUEUE,
    CELERY_INCLUDE_MODULES,
    CELERY_TASK_ROUTES,
    STATIC_BEAT_SCHEDULE,
    get_worker_queue_names,
)
from pkg.celery_queue.client import CeleryClient
from pkg.celery_queue.lifecycle import register_worker_hooks

celery_client = CeleryClient(
    app_name="my_fastapi_server",
    broker_url=settings.redis_url,
    backend_url=settings.redis_url,
    include=CELERY_INCLUDE_MODULES,
    task_routes=CELERY_TASK_ROUTES,
    beat_schedule=STATIC_BEAT_SCHEDULE,
    task_default_queue=CELERY_DEFAULT_QUEUE,
    # 与 Celery 自动创建命名队列时的 exchange/routing key 保持一致。
    task_queues=[
        Queue(name, exchange=Exchange(name, type="direct"), routing_key=name)
        for name in get_worker_queue_names()
    ],
)

register_worker_hooks(on_startup=worker_startup, on_shutdown=worker_shutdown)

# Celery CLI 通过该模块级对象启动 Worker 或 Beat。
celery_app: Celery = celery_client.app

__all__ = ["celery_app", "celery_client"]
