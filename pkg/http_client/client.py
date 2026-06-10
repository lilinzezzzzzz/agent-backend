import time
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import anyio
import httpx

from pkg.logger import logger


@dataclass
class RequestResult:
    status_code: int | None = None
    response: httpx.Response | None = None
    error: str | None = None
    _json: Any = field(init=False, default=None)

    @property
    def success(self) -> bool:
        return self.error is None and self.status_code is not None and 200 <= self.status_code < 300

    def json(self) -> Any:
        if self._json is not None:
            return self._json

        if not self.response:
            return {}

        try:
            self._json = self.response.json()
            return self._json
        except Exception as e:
            raise RuntimeError(f"Failed to parse JSON: {e}") from e


class AsyncHttpClient:
    """
    基于 httpx 封装的单例/长连接客户端。
    """

    def __init__(
        self,
        base_url: str = "",
        timeout: int = 60,
        headers: dict[str, str] | None = None,
        verify: bool = True,
    ):
        self.timeout = timeout
        self.default_headers = headers or {"Content-Type": "application/json"}
        self.client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            headers=self.default_headers,
            verify=verify,
        )

    @staticmethod
    def _get_error_message(response: httpx.Response) -> str:
        """统一获取错误响应的消息"""
        try:
            return response.text
        except Exception as e:
            return f"Failed to get response.text, status_code={response.status_code}, error={e}"

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def close(self):
        await self.client.aclose()

    def _stream_context(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: int | None = None,
    ):
        req_timeout = timeout or self.timeout
        method = method.upper()

        # 在内部定义上下文管理器
        @asynccontextmanager
        async def inner():
            logger.info(f"Stream Start: {method} {url}")
            try:
                # 这里依然可以访问外层的 self, method, url 等变量（闭包特性）
                async with self.client.stream(
                    method,
                    url,
                    params=params,
                    headers=headers,
                    timeout=req_timeout,
                ) as response:
                    response.raise_for_status()
                    yield response

            except httpx.HTTPStatusError as exc:
                raise RuntimeError(
                    f"Stream HTTPStatusError, status_code={exc.response.status_code}, error={exc}"
                ) from exc
            except Exception as exc:
                raise RuntimeError(f"Stream Unexpected Error, error={exc}") from exc

        # 调用内部函数并返回
        return inner()

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | str | None = None,
        json: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> RequestResult:
        url = url.strip()
        req_headers = headers or {}
        logger.info(f"Req: {method} {url} | params={params}")

        try:
            request_method = method.upper()
            request_timeout = timeout or self.timeout
            if isinstance(data, str):
                response = await self.client.request(
                    method=request_method,
                    url=url,
                    params=params,
                    content=data,
                    json=json,
                    files=files,
                    headers=req_headers,
                    timeout=request_timeout,
                )
            else:
                response = await self.client.request(
                    method=request_method,
                    url=url,
                    params=params,
                    data=data,
                    json=json,
                    files=files,
                    headers=req_headers,
                    timeout=request_timeout,
                )

            err_msg = self._get_error_message(response) if response.is_error else None

            return RequestResult(status_code=response.status_code, response=response, error=err_msg)

        except httpx.HTTPStatusError as exc:
            return RequestResult(
                status_code=exc.response.status_code,
                response=exc.response,
                error=f"HTTPStatusError: error={exc}",
            )
        except httpx.RequestError as exc:
            return RequestResult(status_code=0, error=f"RequestError, error={exc}")
        except Exception as exc:
            return RequestResult(status_code=500, error=f"Unexpected Error, error={exc}")

    async def get(self, url: str, **kwargs) -> RequestResult:
        return await self._request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs) -> RequestResult:
        return await self._request("POST", url, **kwargs)

    async def put(self, url: str, **kwargs) -> RequestResult:
        return await self._request("PUT", url, **kwargs)

    async def delete(self, url: str, **kwargs) -> RequestResult:
        return await self._request("DELETE", url, **kwargs)

    async def download_file(
        self,
        url: str,
        *,
        save_path: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: int | None = None,
        chunk_size: int = 1024 * 64,
        on_progress: Callable[[int, int | None], None] | None = None,
    ) -> tuple[bool, str]:
        """
        使用封装后的流式上下文下载文件。
        """
        start_time = time.perf_counter()
        logger.info(f"Download file: {url} -> {save_path}")

        try:
            async with self._stream_context(
                method="GET", url=url, params=params, headers=headers, timeout=timeout
            ) as response:
                total_size = response.headers.get("Content-Length")
                total_size = int(total_size) if total_size else None

                # 使用 anyio.Path 创建父目录
                save_path_obj = anyio.Path(save_path)
                parent_dir = save_path_obj.parent
                if parent_dir:
                    await parent_dir.mkdir(parents=True, exist_ok=True)

                downloaded = 0
                async with await save_path_obj.open("wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size):
                        await f.write(chunk)
                        downloaded += len(chunk)
                        if on_progress:
                            on_progress(downloaded, total_size)

                cost = time.perf_counter() - start_time
                size_mb = downloaded / (1024 * 1024)
                logger.info(f"Download complete: {save_path} | size={size_mb:.2f}MB | cost={cost:.2f}s")
                return True, ""

        except Exception as exc:
            return False, f"Download File, error={exc}"

    async def stream_request(
        self,
        method: str,
        url: str,
        chunk_size: int = 1024,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> AsyncGenerator[bytes, None]:
        """
        通用流式请求
        """
        async with self._stream_context(
            method=method, url=url, params=params, headers=headers, timeout=timeout
        ) as response:
            async for chunk in response.aiter_bytes(chunk_size):
                yield chunk
