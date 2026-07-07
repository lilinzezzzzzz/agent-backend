import asyncio
from collections.abc import Callable, Coroutine, Mapping, Sequence
from datetime import datetime
from functools import partial
from typing import Any, cast

from celery import Celery, signals
from celery.result import AsyncResult

from pkg.concurrency import anyio_run_in_thread
from pkg.logger import logger

LifecycleHook = Callable[[], Any] | Callable[[], Coroutine[Any, Any, Any]]


def _is_valid_trace_id(trace_id: str) -> bool:
    return bool(trace_id.strip()) and trace_id not in {"unknown", "-"}


def _validate_task_id(task_id: str | None) -> str | None:
    if task_id is None:
        return None
    if not isinstance(task_id, str):
        raise TypeError("task_id must be a string")
    if not task_id:
        raise ValueError("task_id cannot be empty")
    return task_id


class CeleryClient:
    """
    Celery 工具类：封装任务提交、状态查询及动态定时任务管理。
    """

    def __init__(
        self,
        app_name: str,
        broker_url: str,
        backend_url: str | None = None,
        include: Sequence[str] | None = None,
        task_routes: Mapping[str, Mapping[str, Any]] | None = None,
        task_default_queue: str = "default",
        beat_schedule: dict[str, Any] | None = None,
        timezone: str = "UTC",
        **extra_conf: Any,
    ) -> None:
        self.queue = task_default_queue
        include_modules: list[str] | None = (
            list(include) if include is not None else None
        )
        self.app = Celery(
            app_name, broker=broker_url, backend=backend_url, include=include_modules
        )

        # 基础配置
        conf = {
            "timezone": timezone,
            "enable_utc": timezone == "UTC",
            "task_default_queue": task_default_queue,
            "task_routes": task_routes or {},
            "beat_schedule": beat_schedule or {},
            "task_serializer": "json",
            "accept_content": ["json"],
            "result_serializer": "json",
            "worker_hijack_root_logger": False,
            "broker_connection_retry_on_startup": True,
            "result_extended": True,
        }

        conf.update(extra_conf or {})
        self.app.conf.update(conf)

    # ------------------------------
    # 内部辅助方法
    # ------------------------------
    def _get_exec_options(self, options: dict, queue: str | None = None) -> dict:
        """
        合并默认配置与传入配置。
        优先级: 显式参数 > options字典 > 实例默认值
        """
        exec_options = options.copy() if options else {}

        # 如果 explicit queue 有值，强制使用
        if queue:
            exec_options["queue"] = queue
        # 如果 options 里也没 queue，使用实例默认 queue
        elif "queue" not in exec_options and self.queue:
            exec_options["queue"] = self.queue

        return exec_options

    # ------------------------------
    # 1. 提交任务 (Submit)
    # ------------------------------
    def submit(
        self,
        *,
        task_name: str,
        trace_id: str,
        args: tuple | list | None = None,
        kwargs: dict | None = None,
        queue: str | None = None,
        priority: int | None = None,
        countdown: int | float | None = None,
        eta: datetime | None = None,
        task_id: str | None = None,
        **options: Any,
    ) -> AsyncResult:
        """提交异步任务，并关联当前 trace_id。

        Args:
            task_name: 完整 Celery task name。
            trace_id: 调用方提供的调用链标识。
        """
        if not isinstance(trace_id, str):
            raise TypeError("trace_id must be a string")
        if not trace_id:
            raise ValueError("trace_id is mandatory and cannot be empty")
        if not _is_valid_trace_id(trace_id):
            raise ValueError("trace_id is invalid")

        args = tuple(args) if args else ()
        kwargs = kwargs or {}

        # 合并参数
        exec_options = self._get_exec_options(options, queue=queue)

        headers = exec_options.get("headers")
        if headers is not None and not isinstance(headers, Mapping):
            raise TypeError("headers must be a mapping")
        exec_options["headers"] = {**dict(headers or {}), "trace_id": trace_id}
        if priority is not None:
            exec_options["priority"] = priority
        if countdown is not None:
            exec_options["countdown"] = countdown
        if eta is not None:
            exec_options["eta"] = eta
        validated_task_id = _validate_task_id(task_id)
        if validated_task_id is not None:
            exec_options["task_id"] = validated_task_id

        return self.app.send_task(
            name=task_name, args=args, kwargs=kwargs, **exec_options
        )

    async def async_submit(
        self,
        *,
        task_name: str,
        trace_id: str,
        args: tuple | list | None = None,
        kwargs: dict | None = None,
        queue: str | None = None,
        priority: int | None = None,
        countdown: int | float | None = None,
        eta: datetime | None = None,
        task_id: str | None = None,
        **options: Any,
    ) -> AsyncResult:
        """在线程中提交 Celery 任务，避免阻塞 async 调用方。"""
        submit = partial(
            self.submit,
            task_name=task_name,
            trace_id=trace_id,
            args=args,
            kwargs=kwargs,
            queue=queue,
            priority=priority,
            countdown=countdown,
            eta=eta,
            task_id=task_id,
            **options,
        )
        return cast(AsyncResult, await anyio_run_in_thread(submit))

    # ------------------------------
    # 2. 查询与检查
    # ------------------------------
    def get_result(self, task_id: str, timeout: float, propagate: bool = True) -> Any:
        """
        获取任务结果 (阻塞式)
        :param timeout: 等待超时时间(秒)，None 表示一直等待
        :param propagate: True 则任务报错时抛出异常，False 则返回异常对象
        :param task_id: str 任务ID
        """
        return AsyncResult(task_id, app=self.app).get(
            timeout=timeout, propagate=propagate
        )

    def get_status(self, task_id: str) -> str:
        return AsyncResult(task_id, app=self.app).state

    def revoke(self, task_id: str, terminate: bool = False):
        self.app.control.revoke(task_id, terminate=terminate)

    async def async_get_result(
        self, task_id: str, timeout: float, propagate: bool = True
    ) -> Any:
        """在线程中获取 Celery 任务结果，避免阻塞 async 调用方。"""
        return await anyio_run_in_thread(
            self.get_result,
            task_id,
            timeout,
            propagate=propagate,
        )

    async def async_get_status(self, task_id: str) -> str:
        """在线程中查询 Celery 任务状态，避免阻塞 async 调用方。"""
        return cast(str, await anyio_run_in_thread(self.get_status, task_id))

    async def async_revoke(self, task_id: str, terminate: bool = False) -> None:
        """在线程中撤销 Celery 任务，避免阻塞 async 调用方。"""
        await anyio_run_in_thread(self.revoke, task_id, terminate=terminate)

    # ------------------------------
    # 3. 生命周期管理
    # ------------------------------
    @staticmethod
    def register_worker_hooks(
        on_startup: LifecycleHook | None = None,
        on_shutdown: LifecycleHook | None = None,
    ):
        """
        注册 Worker 进程生命周期钩子
        注意：使用了 dispatch_uid 防止在多实例下重复注册
        """

        # --- Startup Handler ---
        if on_startup:

            @signals.worker_process_init.connect(
                weak=False, dispatch_uid="pkg_celery_worker_startup"
            )
            def _wrapper_startup(**kwargs):
                try:
                    if asyncio.iscoroutinefunction(on_startup):
                        asyncio.run(on_startup())
                    else:
                        on_startup()
                    logger.success("Worker startup hook executed successfully.")
                except Exception as e:
                    logger.critical(f"Worker startup hook failed: {e}")
                    raise e

        # --- Shutdown Handler ---
        if on_shutdown:

            @signals.worker_process_shutdown.connect(
                weak=False, dispatch_uid="pkg_celery_worker_shutdown"
            )
            def _wrapper_shutdown(**kwargs):
                logger.info("Executing registered worker shutdown hook...")
                try:
                    if asyncio.iscoroutinefunction(on_shutdown):
                        # 修复: 必须同步运行，确保在进程退出前完成清理
                        # 严禁在此处使用 loop.create_task，否则进程会立即退出导致清理中断
                        asyncio.run(on_shutdown())
                    else:
                        on_shutdown()
                    logger.info("Worker shutdown hook executed successfully.")
                except Exception as e:
                    logger.warning(f"Worker shutdown hook error: {e}")
