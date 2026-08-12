import asyncio
import inspect
from collections.abc import Callable, Coroutine
from typing import Any

from celery import signals

from pkg.logger import logger

LifecycleHook = Callable[[], Any] | Callable[[], Coroutine[Any, Any, Any]]


def register_worker_hooks(
    on_startup: LifecycleHook | None = None,
    on_shutdown: LifecycleHook | None = None,
) -> None:
    """注册 Worker 子进程生命周期钩子。"""
    if on_startup:

        @signals.worker_process_init.connect(
            weak=False, dispatch_uid="pkg_celery_worker_startup"
        )
        def _wrapper_startup(**kwargs: Any) -> None:
            try:
                if inspect.iscoroutinefunction(on_startup):
                    asyncio.run(on_startup())
                else:
                    on_startup()
                logger.success("Worker startup hook executed successfully.")
            except Exception as exc:
                logger.critical(f"Worker startup hook failed: {exc}")
                raise

    if on_shutdown:

        @signals.worker_process_shutdown.connect(
            weak=False, dispatch_uid="pkg_celery_worker_shutdown"
        )
        def _wrapper_shutdown(**kwargs: Any) -> None:
            logger.info("Executing registered worker shutdown hook...")
            try:
                if inspect.iscoroutinefunction(on_shutdown):
                    # 必须同步等待，避免 Worker 退出时中断资源清理。
                    asyncio.run(on_shutdown())
                else:
                    on_shutdown()
                logger.info("Worker shutdown hook executed successfully.")
            except Exception as exc:
                logger.warning(f"Worker shutdown hook error: {exc}")
