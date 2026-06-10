"""流处理工具模块

提供 SSE 流超时控制等功能。
"""

from collections.abc import AsyncIterable
from typing import cast

import anyio

from internal.core.errors import StreamError, StreamTimeoutError
from pkg.logger import logger
from pkg.api_response import wrap_sse_data


async def stream_with_chunk_control[T](
    generator: AsyncIterable[T],
    chunk_timeout: float,
    is_sse: bool = True,
) -> AsyncIterable[T]:
    """
    基于 AnyIO 的单 Chunk 超时控制。

    总超时由 Middleware 统一控制。

    Args:
        generator: 异步生成器
        chunk_timeout: 单个 chunk 超时时间（秒）
        is_sse: 是否为 SSE 格式输出
            - True: 超时/错误时 yield SSE 错误数据并记录日志
            - False: 超时/错误时抛出 StreamTimeoutError/StreamError

    Yields:
        生成器产生的数据项

    Raises:
        StreamTimeoutError: 当 is_sse=False 且 chunk 超时时抛出
        StreamError: 当 is_sse=False 且发生异常时抛出

    Examples:
        # SSE 模式（自动处理错误响应）
        async for chunk in stream_with_chunk_control(gen, timeout=30.0, is_sse=True):
            yield chunk

        # 非 SSE 模式（业务层自行处理异常）
        try:
            async for chunk in stream_with_chunk_control(gen, timeout=30.0, is_sse=False):
                yield chunk
        except StreamTimeoutError as e:
            logger.warning(f"Stream timeout: {e.timeout}s")
        except StreamError as e:
            logger.error(f"Stream error: {e.original_error}")
    """
    iterator = generator.__aiter__()

    while True:
        try:
            # 使用 anyio.fail_after 仅控制获取下一个 chunk 的等待时间
            with anyio.fail_after(chunk_timeout):
                item = await iterator.__anext__()

            yield item

        except StopAsyncIteration:
            break

        except TimeoutError:
            # Chunk 生成超时
            if is_sse:
                logger.warning(f"[Stream Timeout] Chunk generation timed out after {chunk_timeout}s")
                err_data = {
                    "code": 40800,
                    "message": f"Stream chunk timed out. No data received for {chunk_timeout}s",
                }
                yield cast(T, wrap_sse_data(err_data))
            else:
                raise StreamTimeoutError(
                    f"Chunk generation timed out after {chunk_timeout}s",
                    timeout=chunk_timeout,
                ) from None
            break

        except anyio.get_cancelled_exc_class() as e:
            # Middleware 触发的总超时取消 或 客户端断连
            # 取消是正常行为，直接向上抛出
            raise e

        except Exception as e:
            # 流处理异常
            if is_sse:
                logger.error(f"[Stream Error] {e}", exc_info=True)
                yield cast(T, wrap_sse_data({"code": 50001, "message": str(e)}))
            else:
                raise StreamError(
                    f"Stream generation error: {e}",
                    original_error=e,
                ) from e
            break
