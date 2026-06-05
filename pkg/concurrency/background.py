import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

from pkg.concurrency._names import get_coroutine_name


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
    task_name = get_coroutine_name(coro)
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
