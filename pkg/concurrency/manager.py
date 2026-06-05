import random
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from functools import partial
from typing import Any, Literal

import anyio
from anyio import (
    CancelScope,
    CapacityLimiter,
    create_task_group,
    fail_after,
    get_cancelled_exc_class,
    move_on_after,
)
from anyio.abc import TaskGroup

from pkg.concurrency._names import get_callable_name
from pkg.concurrency.limits import (
    ANYIO_TASK_MANAGER_MAX_QUEUE,
    DEFAULT_TIMEOUT,
    GLOBAL_MAX_DEFAULT,
    PROCESS_MAX_DEFAULT,
    THREAD_MAX_DEFAULT,
)
from pkg.concurrency.offload import anyio_run_in_process, anyio_run_in_thread
from pkg.logger import logger


@dataclass
class TaskInfo:
    """进程内 AnyIO 后台任务的运行态信息。"""

    task_id: str
    name: str
    scope: CancelScope
    status: str = "running"  # running | completed | failed | cancelled | timeout
    result: Any = None
    exception: BaseException | None = None


class AnyioTaskHandler:
    """管理进程内 AnyIO 后台任务、并发限制和同步函数 offload。"""

    def __init__(self) -> None:
        self._global_limiter = CapacityLimiter(GLOBAL_MAX_DEFAULT)
        self._thread_limiter = CapacityLimiter(THREAD_MAX_DEFAULT)
        self._process_limiter = CapacityLimiter(PROCESS_MAX_DEFAULT)

        self._tg: TaskGroup | None = None
        self._tg_started = False
        self._accepting = False
        self._lock = anyio.Lock()
        self.tasks: dict[str, TaskInfo] = {}
        self.max_queue = ANYIO_TASK_MANAGER_MAX_QUEUE
        self.default_timeout = DEFAULT_TIMEOUT

    async def start(self) -> None:
        """启动持久运行的 AnyIO TaskGroup。"""
        if self._tg_started:
            return
        self._tg = await create_task_group().__aenter__()
        self._tg_started = True
        self._accepting = True
        logger.info("AsyncTaskManagerAnyIO started.")

    async def shutdown(self) -> None:
        """停止接收新任务，取消并等待当前后台任务退出。"""
        logger.warning("Shutting down AsyncTaskManagerAnyIO...")
        self._accepting = False

        async with self._lock:
            active_tasks = list(self.tasks.values())

        for info in active_tasks:
            try:
                info.scope.cancel()
            except Exception as e:
                logger.warning(f"Error canceling task {info.task_id}: {e}")

        if self._tg_started and self._tg is not None:
            try:
                await self._tg.__aexit__(None, None, None)
            except Exception as e:
                logger.error(f"Error closing TaskGroup: {e}")
            finally:
                self._tg = None
                self._tg_started = False
        logger.info("AsyncTaskManagerAnyIO stopped.")

    @staticmethod
    def get_coro_func_name(func: Callable[..., Any]) -> str:
        """返回协程或同步函数的可读名称。"""
        return get_callable_name(func)

    async def _run_task_inner(
        self,
        info: TaskInfo,
        coro_func: Callable[..., Awaitable[Any]],
        args_tuple: tuple[Any, ...],
        kwargs_dict: dict[str, Any],
        timeout: float | None,
    ) -> None:
        coro_name = info.name
        task_id = info.task_id

        try:
            with info.scope:
                async with self._global_limiter:
                    logger.info(f"Task {coro_name} [{task_id}] started.")

                    if timeout and timeout > 0:
                        with fail_after(timeout):
                            result = await coro_func(*args_tuple, **kwargs_dict)
                    else:
                        result = await coro_func(*args_tuple, **kwargs_dict)

                    info.status = "completed"
                    info.result = result
                    logger.info(f"Task {coro_name} [{task_id}] completed.")

        except get_cancelled_exc_class():
            info.status = "cancelled"
            logger.info(f"Task {coro_name} [{task_id}] cancelled.")
        except TimeoutError as te:
            info.status = "timeout"
            info.exception = te
            logger.error(f"Task {coro_name} [{task_id}] timed out after {timeout}s.")
        except BaseException as e:
            info.status = "failed"
            info.exception = e
            logger.error(f"Task {coro_name} [{task_id}] failed, err={e}", exc_info=True)
        finally:
            with anyio.CancelScope(shield=True):
                async with self._lock:
                    self.tasks.pop(task_id, None)

    async def _execute_sync(
        self,
        sync_func: Callable[..., Any],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        timeout: float | None,
        cancellable: bool,
        backend: Literal["thread", "process"],
    ) -> Any:
        func_name = self.get_coro_func_name(sync_func)

        logger.info(f"Task {func_name} started in {backend}.")
        bound = partial(sync_func, *args, **kwargs)

        async def _run() -> Any:
            if backend == "thread":
                return await anyio_run_in_thread(
                    bound, abandon_on_cancel=cancellable, limiter=self._thread_limiter
                )
            return await anyio_run_in_process(
                bound, cancellable=cancellable, limiter=self._process_limiter
            )

        if timeout and timeout > 0:
            with fail_after(timeout):
                return await _run()
        return await _run()

    async def add_task(
        self,
        task_id: str | int,
        *,
        coro_func: Callable[..., Awaitable[Any]],
        args_tuple: tuple[Any, ...] = (),
        kwargs_dict: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> bool:
        """添加后台协程任务。"""
        if not self._accepting:
            raise RuntimeError(
                "AsyncTaskManagerAnyIO is shutting down, not accepting new tasks."
            )
        if not self._tg_started or self._tg is None:
            raise RuntimeError(
                "AsyncTaskManagerAnyIO is not started. Call await start() first."
            )

        task_id = str(task_id)
        kwargs_dict = kwargs_dict or {}
        coro_name = self.get_coro_func_name(coro_func)

        async with self._lock:
            if len(self.tasks) >= self.max_queue:
                logger.error(f"Queue overflow: {len(self.tasks)}/{self.max_queue}")
                raise RuntimeError(f"Queue overflow: {self.max_queue}")

            if task_id in self.tasks:
                logger.warning(f"Task {task_id} already exists.")
                return False

            scope = CancelScope()
            info = TaskInfo(task_id=task_id, name=coro_name, scope=scope)
            self.tasks[task_id] = info

            self._tg.start_soon(
                self._run_task_inner, info, coro_func, args_tuple, kwargs_dict, timeout
            )
        return True

    async def cancel_task(self, task_id: str) -> bool:
        """取消指定后台任务。"""
        async with self._lock:
            info = self.tasks.get(task_id)
            if info:
                info.scope.cancel()
                logger.info(f"Triggered cancellation for Task {task_id}.")
                return True
            logger.warning(f"Task {task_id} not found for cancellation.")
            return False

    async def get_task_status(self) -> dict[str, bool]:
        """返回当前仍在任务表内的任务运行状态。"""
        async with self._lock:
            return {tid: (ti.status == "running") for tid, ti in self.tasks.items()}

    async def run_gather_with_concurrency(
        self,
        coro_func: Callable[..., Awaitable[Any]],
        args_tuple_list: list[tuple[Any, ...]],
        task_timeout: float | None = None,
        global_timeout: float | None = None,
        jitter: float | None = 3.0,
    ) -> list[Any]:
        """按全局并发限制批量执行协程函数。"""
        coro_name = self.get_coro_func_name(coro_func)
        results: list[Any] = [None] * len(args_tuple_list)

        async def _worker(index: int, args: tuple[Any, ...]) -> None:
            if jitter and jitter > 0:
                await anyio.sleep(random.uniform(0, jitter))

            async with self._global_limiter:
                try:
                    logger.debug(f"Task-{index} ({coro_name}) started.")
                    if task_timeout and task_timeout > 0:
                        with fail_after(task_timeout):
                            res = await coro_func(*args)
                    else:
                        res = await coro_func(*args)
                    results[index] = res
                except TimeoutError:
                    logger.error(
                        f"Task-{index} ({coro_name}) timed out (single task limit)."
                    )
                    results[index] = None
                except get_cancelled_exc_class():
                    logger.debug(f"Task-{index} ({coro_name}) cancelled.")
                except Exception as inner_exc:
                    logger.error(f"Task-{index} ({coro_name}) failed: {inner_exc}")
                    results[index] = None

        try:
            with move_on_after(global_timeout) as scope:
                async with create_task_group() as tg:
                    for i, args_tuple in enumerate(args_tuple_list):
                        tg.start_soon(_worker, i, args_tuple)

            if scope.cancelled_caught:
                logger.warning(
                    f"Batch task ({coro_name}) hit global timeout {global_timeout}s."
                )
        except Exception as e:
            logger.error(f"Batch task ({coro_name}) unexpected error: {e}")

        return results

    async def run_in_thread(
        self,
        sync_func: Callable[..., Any],
        *,
        args_tuple: tuple[Any, ...] | None = None,
        kwargs_dict: dict[str, Any] | None = None,
        timeout: float | None = None,
        cancellable: bool = False,
    ) -> Any:
        """在线程中执行同步函数。"""
        return await self._execute_sync(
            sync_func,
            args_tuple or (),
            kwargs_dict or {},
            timeout,
            cancellable,
            "thread",
        )

    async def run_in_process(
        self,
        sync_func: Callable[..., Any],
        *,
        args_tuple: tuple[Any, ...] | None = None,
        kwargs_dict: dict[str, Any] | None = None,
        timeout: float | None = None,
        cancellable: bool = False,
    ) -> Any:
        """在子进程中执行同步函数。"""
        return await self._execute_sync(
            sync_func,
            args_tuple or (),
            kwargs_dict or {},
            timeout,
            cancellable,
            "process",
        )

    async def run_in_threads(
        self,
        sync_func: Callable[..., Any],
        *,
        args_tuple_list: list[tuple[Any, ...]] | None = None,
        kwargs_dict_list: list[dict[str, Any]] | None = None,
        timeout: float | None = None,
        cancellable: bool = False,
    ) -> list[Any]:
        """在线程中批量执行同步函数。"""
        return await self._run_batch_sync(
            sync_func, args_tuple_list, kwargs_dict_list, timeout, cancellable, "thread"
        )

    async def run_in_processes(
        self,
        sync_func: Callable[..., Any],
        *,
        args_tuple_list: list[tuple[Any, ...]] | None = None,
        kwargs_dict_list: list[dict[str, Any]] | None = None,
        timeout: float | None = None,
        cancellable: bool = False,
    ) -> list[Any]:
        """在子进程中批量执行同步函数。"""
        return await self._run_batch_sync(
            sync_func,
            args_tuple_list,
            kwargs_dict_list,
            timeout,
            cancellable,
            "process",
        )

    async def _run_batch_sync(
        self,
        sync_func: Callable[..., Any],
        args_list: list[tuple[Any, ...]] | None,
        kwargs_list: list[dict[str, Any]] | None,
        timeout: float | None,
        cancellable: bool,
        backend: Literal["thread", "process"],
    ) -> list[Any]:
        resolved_args: list[tuple[Any, ...]] = (
            args_list if args_list is not None else []
        )
        resolved_kwargs: Sequence[dict[str, Any] | None]
        if kwargs_list is None:
            resolved_kwargs = [None] * len(resolved_args)
        else:
            resolved_kwargs = kwargs_list

        if len(resolved_kwargs) != len(resolved_args):
            raise ValueError("args and kwargs lists must be same length")

        results: list[Any] = [None] * len(resolved_args)
        func_name = self.get_coro_func_name(sync_func)

        async def _worker(
            idx: int, a: tuple[Any, ...], k: dict[str, Any] | None
        ) -> None:
            bound = partial(sync_func, *(a or ()), **(k or {}))

            async with self._global_limiter:
                try:

                    async def _run() -> Any:
                        if backend == "thread":
                            return await anyio_run_in_thread(
                                bound,
                                abandon_on_cancel=cancellable,
                                limiter=self._thread_limiter,
                            )
                        return await anyio_run_in_process(
                            bound,
                            cancellable=cancellable,
                            limiter=self._process_limiter,
                        )

                    if timeout and timeout > 0:
                        with fail_after(timeout):
                            res = await _run()
                    else:
                        res = await _run()

                    results[idx] = res
                except TimeoutError:
                    logger.error(f"{backend}-{idx} ({func_name}) timed out.")
                except get_cancelled_exc_class():
                    logger.debug(f"{backend}-{idx} ({func_name}) cancelled.")
                except Exception as e:
                    logger.error(f"{backend}-{idx} ({func_name}) failed: {e}")

        async with create_task_group() as tg:
            for i, (args, kwargs) in enumerate(
                zip(resolved_args, resolved_kwargs, strict=False)
            ):
                tg.start_soon(_worker, i, args, kwargs)

        return results
