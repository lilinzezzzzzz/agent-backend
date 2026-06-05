import asyncio
import multiprocessing
import random
from collections.abc import Awaitable, Callable, Coroutine, Sequence
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
    to_process,
    to_thread,
)
from anyio.abc import TaskGroup

from pkg.logger import logger

CPU = max(1, multiprocessing.cpu_count())
GLOBAL_MAX_DEFAULT = min(max(32, 4 * CPU), 256)
THREAD_MAX_DEFAULT = min(max(16, (2 * GLOBAL_MAX_DEFAULT) // 3), 128)
PROCESS_MAX_DEFAULT = max(1, min(CPU, 8))

DEFAULT_TIMEOUT = 180
ANYIO_TM_MAX_QUEUE = 10_000


# ---------- anyio wrapper functions ----------
def _format_callable_name(
    func_name: str | None, *, bound_owner: Any = None, fallback: str = "background-task"
) -> str:
    if not isinstance(func_name, str):
        return fallback
    if bound_owner is not None:
        owner_name = (
            bound_owner.__name__
            if isinstance(bound_owner, type)
            else bound_owner.__class__.__name__
        )
        return f"{owner_name}.{func_name}"
    return "lambda_func" if func_name == "<lambda>" else func_name


def _get_coroutine_name(coro: Coroutine[Any, Any, Any]) -> str:
    code = getattr(coro, "cr_code", None)
    func_name = getattr(code, "co_name", None)

    frame = getattr(coro, "cr_frame", None)
    frame_locals = getattr(frame, "f_locals", None)
    if isinstance(frame_locals, dict):
        bound_self = frame_locals.get("self")
        if bound_self is not None:
            return _format_callable_name(func_name, bound_owner=bound_self)

        bound_cls = frame_locals.get("cls")
        if isinstance(bound_cls, type):
            return _format_callable_name(func_name, bound_owner=bound_cls)

    return _format_callable_name(func_name)


def asyncio_run_background[T](
    coro: Coroutine[Any, Any, T],
    *,
    on_error: Callable[[Exception], None] | None = None,
) -> asyncio.Task[T]:
    """
    在当前 asyncio event loop 中后台执行协程，并消费任务结果。

    Args:
        coro: 需要后台执行的协程对象
        on_error: 后台任务异常回调；未提供时交给事件循环异常处理器

    Returns:
        asyncio.Task 对象，可用于取消或检查状态
    """
    task_name = _get_coroutine_name(coro)
    try:
        task = asyncio.create_task(coro, name=task_name)
    except RuntimeError:
        coro.close()
        raise

    def _consume_result(done_task: asyncio.Task[T]) -> None:
        done_task_name = done_task.get_name()
        try:
            done_task.result()
        except asyncio.CancelledError:
            return
        except Exception as exc:
            if on_error is not None:
                try:
                    on_error(exc)
                except Exception as handler_exc:
                    done_task.get_loop().call_exception_handler(
                        {
                            "message": f"background task {done_task_name} error handler failed",
                            "exception": handler_exc,
                            "source_exception": exc,
                            "task": done_task,
                        }
                    )
                return

            done_task.get_loop().call_exception_handler(
                {
                    "message": f"background task {done_task_name} failed",
                    "exception": exc,
                    "task": done_task,
                }
            )

    task.add_done_callback(_consume_result)
    return task


async def anyio_run_in_thread(
    func: Callable[..., Any],
    *args: Any,
    abandon_on_cancel: bool = False,
    limiter: CapacityLimiter | None = None,
    **kwargs: Any,
) -> Any:
    """
    封装 anyio.to_thread.run_sync，消除类型警告。

    Args:
        func: 同步函数
        *args: 位置参数
        abandon_on_cancel: 取消时是否放弃任务
        limiter: 容量限制器
        **kwargs: 关键字参数

    Returns:
        函数执行结果
    """
    bound = partial(func, *args, **kwargs)
    return await to_thread.run_sync(
        bound, abandon_on_cancel=abandon_on_cancel, limiter=limiter
    )  # type: ignore


async def anyio_run_in_process(
    func: Callable[..., Any],
    *args: Any,
    cancellable: bool = False,
    limiter: CapacityLimiter | None = None,
    **kwargs: Any,
) -> Any:
    """
    封装 anyio.to_process.run_sync，消除类型警告。

    Args:
        func: 同步函数
        *args: 位置参数
        cancellable: 是否可取消
        limiter: 容量限制器
        **kwargs: 关键字参数

    Returns:
        函数执行结果
    """
    bound = partial(func, *args, **kwargs)
    return await to_process.run_sync(bound, cancellable=cancellable, limiter=limiter)  # type: ignore


@dataclass
class TaskInfo:
    task_id: str
    name: str
    scope: CancelScope
    status: str = "running"  # running | completed | failed | canceled | timeout
    result: Any = None
    exception: BaseException | None = None


class AnyioTaskHandler:
    def __init__(self):
        self._global_limiter = CapacityLimiter(GLOBAL_MAX_DEFAULT)
        self._thread_limiter = CapacityLimiter(THREAD_MAX_DEFAULT)
        self._process_limiter = CapacityLimiter(PROCESS_MAX_DEFAULT)

        self._tg: TaskGroup | None = None
        self._tg_started = False
        self._accepting = False  # 是否接受新任务
        self._lock = anyio.Lock()
        self.tasks: dict[str, TaskInfo] = {}
        self.max_queue = ANYIO_TM_MAX_QUEUE
        self.default_timeout = DEFAULT_TIMEOUT

    # ---------- lifecycle ----------
    async def start(self):
        if self._tg_started:
            return
        # 创建持久运行的 TaskGroup
        self._tg = await create_task_group().__aenter__()
        self._tg_started = True
        self._accepting = True
        logger.info("AsyncTaskManagerAnyIO started.")

    async def shutdown(self):
        logger.warning("Shutting down AsyncTaskManagerAnyIO...")
        # 停止接受新任务
        self._accepting = False

        # 只在创建快照时持有锁，减少锁粒度
        async with self._lock:
            active_tasks = list(self.tasks.values())

        # cancel 操作只是设置标志，无需在锁内执行
        for info in active_tasks:
            try:
                # 这会触发 _run_task_inner 中的 CancelledError
                info.scope.cancel()
            except Exception as e:
                logger.warning(f"Error canceling task {info.task_id}: {e}")

        if self._tg_started and self._tg is not None:
            try:
                # 退出 TaskGroup 会等待所有任务取消完成
                await self._tg.__aexit__(None, None, None)
            except Exception as e:
                logger.error(f"Error closing TaskGroup: {e}")
            finally:
                self._tg = None
                self._tg_started = False
        logger.info("AsyncTaskManagerAnyIO stopped.")

    # ---------- helpers ----------
    @staticmethod
    def get_coro_func_name(func: Callable[..., Any]) -> str:
        while isinstance(func, partial):
            func = func.func

        bound_self = getattr(func, "__self__", None)
        func_name = getattr(func, "__name__", None)
        return _format_callable_name(
            func_name, bound_owner=bound_self, fallback=str(func)
        )

    async def _run_task_inner(
        self,
        info: TaskInfo,
        coro_func: Callable[..., Awaitable[Any]],
        args_tuple: tuple,
        kwargs_dict: dict,
        timeout: float | None,
    ):
        coro_name = info.name
        task_id = info.task_id

        try:
            # [Fix]: Scope 上下文包裹执行逻辑，确保异常抛出后上下文退出，不影响 finally
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
            # 此时 info.scope 已退出，在这里处理取消逻辑
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
            # [Safe Cleanup]: 此时不在 info.scope 中，使用 shield 保护清理过程
            # 即使父级调用 shutdown 导致所有任务被取消，清理字典的操作也能完成
            with anyio.CancelScope(shield=True):
                async with self._lock:
                    self.tasks.pop(task_id, None)

    async def _execute_sync(
        self,
        sync_func: Callable,
        args: tuple,
        kwargs: dict,
        timeout: float | None,
        cancellable: bool,
        backend: Literal["thread", "process"],
    ) -> Any:
        """内部通用方法：执行单个同步任务"""
        func_name = self.get_coro_func_name(sync_func)

        logger.info(f"Task {func_name} started in {backend}.")
        bound = partial(sync_func, *args, **kwargs)

        async def _run():
            if backend == "thread":
                # AnyIO 4.1.0+: thread 使用 abandon_on_cancel
                return await anyio_run_in_thread(
                    bound, abandon_on_cancel=cancellable, limiter=self._thread_limiter
                )
            else:
                return await anyio_run_in_process(
                    bound, cancellable=cancellable, limiter=self._process_limiter
                )

        if timeout and timeout > 0:
            with fail_after(timeout):
                return await _run()
        return await _run()

    # ---------- public APIs ----------
    async def add_task(
        self,
        task_id: str | int,
        *,
        coro_func: Callable[..., Awaitable[Any]],
        args_tuple: tuple = (),
        kwargs_dict: dict | None = None,
        timeout: float | None = None,
    ) -> bool:
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
                raise Exception(f"Queue overflow: {self.max_queue}")

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
        async with self._lock:
            info = self.tasks.get(task_id)
            if info:
                # 触发任务内部的 CancelledError
                info.scope.cancel()
                logger.info(f"Triggered cancellation for Task {task_id}.")
                return True
            logger.warning(f"Task {task_id} not found for cancellation.")
            return False

    async def get_task_status(self) -> dict[str, bool]:
        async with self._lock:
            return {tid: (ti.status == "running") for tid, ti in self.tasks.items()}

    async def run_gather_with_concurrency(
        self,
        coro_func: Callable[..., Awaitable[Any]],
        args_tuple_list: list[tuple],
        task_timeout: float | None = None,
        global_timeout: float | None = None,
        jitter: float | None = 3.0,
    ) -> list[Any]:
        coro_name = self.get_coro_func_name(coro_func)
        results: list[Any] = [None] * len(args_tuple_list)

        async def _worker(index: int, args: tuple):
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
                    pass
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
        args_tuple: tuple | None = None,
        kwargs_dict: dict | None = None,
        timeout: float | None = None,
        cancellable: bool = False,
    ) -> Any:
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
        args_tuple: tuple | None = None,
        kwargs_dict: dict | None = None,
        timeout: float | None = None,
        cancellable: bool = False,
    ) -> Any:
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
        sync_func: Callable,
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

        async def _worker(idx: int, a: tuple[Any, ...], k: dict[str, Any] | None):
            bound = partial(sync_func, *(a or ()), **(k or {}))

            async with self._global_limiter:
                try:

                    async def _run():
                        if backend == "thread":
                            return await anyio_run_in_thread(
                                bound,
                                abandon_on_cancel=cancellable,
                                limiter=self._thread_limiter,
                            )
                        else:
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
