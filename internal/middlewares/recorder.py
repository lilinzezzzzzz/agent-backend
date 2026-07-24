import time

from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response
from opentelemetry.trace import SpanKind, StatusCode
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from internal.core import AppException, errors
from pkg import request_context as context
from pkg.api_response import error_response
from pkg.ids import normalize_uuid7_trace_id, uuid6_unique_str_id
from pkg.logger import logger
from pkg.toolkit.exc import format_exception_traceback
from pkg.toolkit.middleware import BaseMiddlewareContext
from pkg.tracing import span_context

_REQUEST_SPAN_NAME = "middleware.request"


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

    def __init__(
        self,
        scope: Scope,
        *,
        client_host: str,
        query_string: str,
        receive: Receive | None = None,
    ) -> None:
        super().__init__(scope)
        self._client_host = client_host
        self._query_string = query_string
        self._start_time = time.perf_counter()
        self._trace_id = uuid6_unique_str_id()
        self._receive = receive
        self._response_started = False
        self._process_time: float | None = None

        # 只接受无连字符 UUIDv7 hex；非法外部输入不会进入请求上下文。
        header_trace_id = normalize_uuid7_trace_id(self.headers.get("X-Trace-ID"))
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
            logger.opt(depth=1).warning(
                f"Business exception, exc={format_exception_traceback(exc, max_entries=3)}"
            )
        elif isinstance(exc, RequestValidationError):
            logger.opt(depth=1).warning(f"Validation Error: {exc}")
        else:
            logger.opt(depth=1).error(
                f"Unexpected exception, exc={format_exception_traceback(exc, max_entries=10)}"
            )

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
            return error_response(
                error=errors.BadRequest, message=f"Validation Error: {exc}"
            )
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

        # 全局异常捕获,覆盖整个请求处理流程
        try:
            # 1. 初始化上下文
            context.init(**{context.ContextKey.TRACE_ID.value: req_ctx.trace_id})
            send_wrapper = req_ctx.create_send_wrapper(send, scope)
            async with span_context(
                _REQUEST_SPAN_NAME,
                span_kind=SpanKind.SERVER,
            ) as request_span:
                # 2. 记录访问日志
                logger.info(
                    f"access log, ip={req_ctx.client_host}, method={req_ctx.method}, "
                    f"path={req_ctx.path}, query_string={req_ctx.query_string}"
                )

                try:
                    # 3. 创建 send 包装器并执行应用逻辑
                    await self.app(scope, receive, send_wrapper)

                    # 4. 记录响应日志
                    logger.info(
                        f"response log, processing time={req_ctx.process_time:.4f}s"
                    )
                except Exception as exc:
                    # 5. 统一异常处理
                    request_span.record_exception(exc)
                    request_span.set_status(StatusCode.ERROR, type(exc).__name__)
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
