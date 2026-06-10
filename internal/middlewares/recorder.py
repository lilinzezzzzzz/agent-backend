import time
from typing import Any

from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from internal.core import AppException, errors
from pkg.logger import logger, span_context
from pkg import request_context as context
from pkg.toolkit.exc import get_business_exec_tb, get_unexpected_exec_tb
from pkg.toolkit.middleware import BaseMiddlewareContext
from pkg.api_response import error_response
from pkg.ids import uuid6_unique_str_id

_REQUEST_SPAN_NAME = "middleware.request"


class _RecorderSpanScope:
    """为 recorder 封装可显式标记错误的 span scope。"""

    __slots__ = ("_captured_exc", "_span_ctx")

    def __init__(self, *, span_name: str) -> None:
        self._captured_exc: BaseException | None = None
        self._span_ctx = span_context(span_name)

    def mark_error(self, exc: BaseException) -> None:
        if not isinstance(exc, BaseException):
            raise TypeError(f"exc must be a BaseException, got {type(exc).__name__}")
        self._captured_exc = exc

    async def __aenter__(self) -> "_RecorderSpanScope":
        await self._span_ctx.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: Any,
    ) -> bool:
        effective_exc = exc if exc is not None else self._captured_exc
        effective_exc_type = (
            exc_type if exc_type is not None else (type(effective_exc) if effective_exc is not None else None)
        )
        effective_tb = tb if tb is not None else (effective_exc.__traceback__ if effective_exc is not None else None)
        return await self._span_ctx.__aexit__(effective_exc_type, effective_exc, effective_tb)


def _recorder_span_context(span_name: str) -> _RecorderSpanScope:
    return _RecorderSpanScope(span_name=span_name)


class _RequestContext(BaseMiddlewareContext):
    """请求上下文，封装中间件处理过程中的状态变量"""

    __slots__ = (
        "_client_host",
        "_query_string",
        "_start_time",
        "_trace_id",
        "_receive",
        "_response_started",
        "_process_time",
    )

    def __init__(self, scope: Scope, *, client_host: str, query_string: str, receive: Receive | None = None) -> None:
        super().__init__(scope)
        self._client_host = client_host
        self._query_string = query_string
        self._start_time = time.perf_counter()
        self._trace_id = uuid6_unique_str_id()
        self._receive = receive
        self._response_started = False
        self._process_time: float | None = None

        # 优先使用请求头中的 trace_id
        header_trace_id = self.headers.get("X-Trace-ID")
        if header_trace_id:
            self._trace_id = header_trace_id

    @property
    def client_host(self) -> str:
        return self._client_host

    @property
    def query_string(self) -> str:
        return self._query_string

    @property
    def trace_id(self) -> str:
        return self._trace_id

    @property
    def receive(self) -> Receive | None:
        return self._receive

    @property
    def response_started(self) -> bool:
        return self._response_started

    @response_started.setter
    def response_started(self, value: bool) -> None:
        self._response_started = value

    @property
    def process_time(self) -> float:
        """返回请求处理耗时。如果已记录则返回记录值，否则返回当前实时值。"""
        if self._process_time is not None:
            return self._process_time
        return time.perf_counter() - self._start_time

    def create_send_wrapper(self, send: Send, scope: Scope):
        """
        创建 send 包装器，用于在响应头中注入追踪信息

        Args:
            send: 原始 send 函数
            scope: ASGI scope

        Returns:
            包装后的 send 函数
        """

        async def send_wrapper(message: Message):
            if message["type"] == "http.response.start":
                self.response_started = True
                headers_list = MutableHeaders(scope=message)
                headers_list["X-Process-Time"] = str(self.process_time)
                headers_list["X-Trace-ID"] = self.trace_id
            await send(message)

        return send_wrapper


class ASGIRecordMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    @staticmethod
    def _log_exception(exc: Exception) -> None:
        """
        记录异常日志，根据异常类型使用不同的日志级别

        Args:
            exc: 捕获的异常
        """
        if isinstance(exc, AppException):
            logger.opt(depth=1).warning(f"Business exception, exc={get_business_exec_tb(exc)}")
        elif isinstance(exc, RequestValidationError):
            logger.opt(depth=1).warning(f"Validation Error: {exc}")
        else:
            logger.opt(depth=1).error(f"Unexpected exception, exc={get_unexpected_exec_tb(exc)}")

    @staticmethod
    def _build_error_response(exc: Exception) -> Response:
        """
        根据异常类型构造错误响应

        Args:
            exc: 捕获的异常

        Returns:
            FastAPI Response 对象
        """
        if isinstance(exc, AppException):
            return error_response(error=exc.error, message=exc.message)
        elif isinstance(exc, RequestValidationError):
            return error_response(error=errors.BadRequest, message=f"Validation Error: {exc}")
        else:
            return error_response(error=errors.InternalServerError, message=str(exc))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # 初始化请求上下文
        req_ctx = _RequestContext(
            scope,
            client_host=scope.get("client", ["unknown"])[0],
            query_string=scope.get("query_string", b"").decode(),
            receive=receive,
        )
        send_wrapper: Send = send
        request_span: _RecorderSpanScope | None = None

        # 全局异常捕获,覆盖整个请求处理流程
        try:
            # 1. 初始化上下文
            context.init(**{context.ContextKey.TRACE_ID: req_ctx.trace_id})
            send_wrapper = req_ctx.create_send_wrapper(send, scope)
            async with _recorder_span_context(_REQUEST_SPAN_NAME) as request_span:
                # 2. 记录访问日志
                logger.info(
                    f"access log, ip={req_ctx.client_host}, method={req_ctx.method}, "
                    f"path={req_ctx.path}, query_string={req_ctx.query_string}"
                )

                try:
                    # 3. 创建 send 包装器并执行应用逻辑
                    await self.app(scope, receive, send_wrapper)

                    # 4. 记录响应日志
                    logger.info(f"response log, processing time={req_ctx.process_time:.4f}s")
                except Exception as exc:
                    # 5. 统一异常处理
                    request_span.mark_error(exc)
                    self._log_exception(exc)

                    if not req_ctx.response_started:
                        error_resp = self._build_error_response(exc)
                        await error_resp(scope, receive, send=send_wrapper)
                    else:
                        logger.critical(
                            f"Response already started, cannot send error response. trace_id={req_ctx.trace_id}"
                        )
        finally:
            context.clear()
