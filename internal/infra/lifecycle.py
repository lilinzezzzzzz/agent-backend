"""API 与任务共享的异步资源清理边界。"""

import sys
from contextlib import AsyncExitStack

import anyio


async def close_resources(cleanup: AsyncExitStack) -> None:
    """等待清理完成，再原样传播清理异常或调用方的取消。

    在调用方的 finally 中执行，以保留当前异常及其清理失败链。
    子任务执行受保护的清理，避免调用方反复 Task.cancel() 中断关闭操作。
    """
    caller_exc_info = sys.exc_info()
    caller_error = caller_exc_info[1]
    cleanup_error: BaseException | None = None

    async def close() -> None:
        nonlocal cleanup_error
        try:
            try:
                if caller_error is not None:
                    raise caller_error
            finally:
                # 保留调用方异常状态，让 ExitStack 正确连接多次清理失败的上下文。
                with anyio.CancelScope(shield=True):
                    await cleanup.__aexit__(*caller_exc_info)
        except BaseException as exc:
            # 交回调用方抛出，避免 TaskGroup 把清理异常包装为 ExceptionGroup。
            if exc is not caller_error:
                cleanup_error = exc

    try:
        with anyio.CancelScope(shield=True):
            async with anyio.create_task_group() as task_group:
                task_group.start_soon(close)
    finally:
        if cleanup_error is not None:
            fixed_context = cleanup_error.__context__
            try:
                raise cleanup_error
            except BaseException:
                if fixed_context is not None:
                    cleanup_error.__context__ = fixed_context
                raise
