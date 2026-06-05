from collections.abc import Callable
from functools import partial
from typing import Any

from anyio import CapacityLimiter, to_process, to_thread


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
    return await to_thread.run_sync(bound, abandon_on_cancel=abandon_on_cancel, limiter=limiter)  # type: ignore


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
