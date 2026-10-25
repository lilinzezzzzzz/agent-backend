import time

from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response
from fastapi.telemetry import get_telemetry_data
from opentelemetry import _logs
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.trace import StatusCode
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from internal.core import AppException, errors
from pkg import request_context as context
from pkg.api_response import error_response
from pkg.logger import logger
from pkg.toolkit.exc import format_exception_traceback
from pkg.toolkit.middleware import BaseMiddlewareContext
from pkg.tracing.propagation import (
    extract_or_create_trace_context,
    trace_context_headers,
)

_OTEL_LOGGER_SCOPE = "internal.middlewares.recorder"


class _RequestContext(BaseMiddlewareContext):
    """请求上下文，封装中间件处理过程中的状态变量"""

    __slots__ = (
        "_client_host",
        "_query_string",
        "_start_time",
        "_trace_id",
        "_otel_context",
        "_traceparent",
        "_receive",
        "_response_started",
        "_process_time",
        "_tracing_enabled",
    )

    def __init__(
        self,
        scope: Scope,
        *,
        client_host: str,
        query_string: str,
        receive: Receive | None = None,
        tracing_enabled: bool = True,
    ) -> None:
        super().__init__(scope)
        self._client_host = client_host
        self._query_string = query_string
        self._start_time = time.perf_counter()
        self._tracing_enabled = tracing_enabled
        self._otel_context = self._resolve_otel_context()
        span_context = trace.get_current_span(self._otel_context).get_span_context()
        self._trace_id = f"{span_context.trace_id:032x}"
        self._traceparent = trace_context_headers(self._otel_context)["traceparent"]
        self._receive = receive
        self._response_started = False
        self._process_time: float | None = None

    def _resolve_otel_context(self) -> Context:
        """复用请求 Span，或独立解析、生成传播上下文并保留框架采集元数据。"""
        current_context = otel_context.get_current()
        current_span_context = trace.get_current_span(current_context).get_span_context()
        has_fastapi_request_telemetry = get_telemetry_data() is not None
        has_valid_span_context = current_span_context.is_valid

        if has_fastapi_request_telemetry:
            if self._tracing_enabled and has_valid_span_context:
                # 复用请求 Span，未采样时也保持日志和响应头的 Trace ID 一致。
                return current_context

            # 仅开启 Metrics/Logs 时，保留 FastAPI 元数据以支持路由归属和错误日志。
            # 传播器会替换已有 Span，避免继承无关调用方的追踪身份。
            return extract_or_create_trace_context(
                self.headers,
                base_context=current_context,
            )

        # 没有框架遥测上下文时，合法请求头沿用，缺失或非法则生成不采集的上下文。
        return extract_or_create_trace_context(self.headers)

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
    def otel_context(self) -> Context:
        return self._otel_context

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
                headers_list["traceparent"] = self._traceparent
            await send(message)

        return send_wrapper


class ASGIRecordMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        tracing_enabled: bool = True,
        logs_enabled: bool = False,
    ) -> None:
        self.app = app
        self._tracing_enabled = tracing_enabled
        self._logs_enabled = logs_enabled

    @staticmethod
    def _log_otel_exception(exc: Exception) -> None:
        """将未处理异常写入 OTel Logs 管道，不改变业务异常处理流程。"""
        otel_logger = _logs.get_logger(
            _OTEL_LOGGER_SCOPE,
            logger_provider=_logs.get_logger_provider(),
        )
        otel_logger.emit(
            exception=exc,
            event_name="http.server.request.exception",
            timestamp=time.time_ns(),
            severity_number=_logs.SeverityNumber.ERROR,
            severity_text="ERROR",
            body="Unhandled exception in request",
        )

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
            tracing_enabled=self._tracing_enabled,
        )
        send_wrapper: Send = send
        request_span = trace.get_current_span(req_ctx.otel_context)
        otel_token = otel_context.attach(req_ctx.otel_context)

        # 全局异常捕获,覆盖整个请求处理流程
        try:
            # 1. 初始化上下文
            context.init(**{context.ContextKey.TRACE_ID.value: req_ctx.trace_id})
            send_wrapper = req_ctx.create_send_wrapper(send, scope)
            # 2. 记录访问日志；HTTP SERVER Span 由 FastAPI 原生 telemetry 创建。
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
                # 业务错误仍可能返回 HTTP 200 信封，需要显式标记请求 Span。
                if request_span.is_recording():
                    request_span.record_exception(exc)
                    request_span.set_status(StatusCode.ERROR, type(exc).__name__)
                self._log_exception(exc)
                if self._logs_enabled and not isinstance(
                    exc, (AppException, RequestValidationError)
                ):
                    try:
                        self._log_otel_exception(exc)
                    except Exception:
                        # 观测链路失败不得改变业务错误响应。
                        logger.opt(exception=True).warning(
                            "Failed to emit OTel exception log"
                        )

                if not req_ctx.response_started:
                    error_resp = self._build_error_response(exc)
                    await error_resp(scope, receive, send=send_wrapper)
                else:
                    logger.critical(
                        f"Response already started, cannot send error response. trace_id={req_ctx.trace_id}"
                    )
        finally:
            try:
                context.clear()
            finally:
                otel_context.detach(otel_token)
