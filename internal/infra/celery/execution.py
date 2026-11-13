"""Celery 同步任务调用应用异步服务的执行适配。"""

from collections.abc import Callable, Coroutine
from contextlib import AsyncExitStack

import anyio

from internal.config import settings
from internal.infra.database import (
    check_database_revision,
    close_async_db,
    init_async_db,
    reset_async_db,
)
from internal.infra.lifecycle import close_resources
from internal.infra.redis import close_async_redis, init_async_redis, reset_async_redis
from pkg import request_context as context


def run_in_async[T](
    coro_func: Callable[[], Coroutine[None, None, T]], trace_id: str
) -> T:
    """在独立事件循环中执行异步调用，并管理任务级应用资源。"""
    reset_async_db()
    reset_async_redis()

    async def _wrapper() -> T:
        cleanup = AsyncExitStack()
        try:
            cleanup.push_async_callback(close_async_db)
            init_async_db(echo=settings.db.echo)
            await check_database_revision()
            cleanup.push_async_callback(close_async_redis)
            init_async_redis()
            context.init(trace_id=trace_id)
            return await coro_func()
        finally:
            await close_resources(cleanup)

    return anyio.run(_wrapper)
