from collections.abc import Callable, Coroutine
from functools import partial
from typing import Any

from anyio import (
    CapacityLimiter,
    create_task_group,
    get_cancelled_exc_class,
    to_process,
    to_thread,
)


async def anyio_gather[T](
    *coros: Coroutine[Any, Any, T],
) -> list[T]:
    """
    使用 AnyIO task group 并发执行协程，并按传入顺序返回结果。

    与 asyncio.gather 不同，任一协程失败时会取消其余协程，
    等待 task group 完成清理后再抛出最先捕获的异常。
    取消异常始终继续传播，不会包装成普通结果。

    Args:
        *coros: 待并发执行的协程。

    Returns:
        与传入协程顺序一致的结果列表。
    """
    results: dict[int, T] = {}
    errors: list[Exception] = []
    cancellations: list[BaseException] = []

    async def run(index: int, coro: Coroutine[Any, Any, T]) -> None:
        try:
            results[index] = await coro
        except get_cancelled_exc_class() as exc:
            cancellations.append(exc)
            raise
        except Exception as exc:
            errors.append(exc)
            task_group.cancel_scope.cancel()

    async with create_task_group() as task_group:
        for index, coro in enumerate(coros):
            task_group.start_soon(run, index, coro)

    if errors:
        raise errors[0]
    if cancellations:
        raise cancellations[0]

    return [results[index] for index in range(len(coros))]


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
