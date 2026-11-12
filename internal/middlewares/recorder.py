import time

from fastapi.telemetry import get_telemetry_data
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.context import Context
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from internal.middlewares.exceptions import exception_response
from pkg import request_context as context
from pkg.logger import logger
from pkg.tracing.propagation import (
    extract_or_create_trace_context,
    trace_context_headers,
)


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

    def _resolve_otel_context(self, headers: Headers) -> Context:
        """复用原生请求 Span；未采集时独立建立请求传播身份。"""
        current_context = otel_context.get_current()
        if get_telemetry_data() is None:
            # 不继承无关调用方的活跃 Span，只使用本次请求头中的身份。
            return extract_or_create_trace_context(headers)

        current_span = trace.get_current_span(current_context)
        if self._tracing_enabled and current_span.get_span_context().is_valid:
            return current_context

        # 仅启用 Metrics/Logs 时保留框架元数据，同时替换传播身份。
        return extract_or_create_trace_context(headers, base_context=current_context)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start_time = time.perf_counter()
        request_context = self._resolve_otel_context(Headers(scope=scope))
        span_context = trace.get_current_span(request_context).get_span_context()
        trace_id = f"{span_context.trace_id:032x}"
        traceparent = trace_context_headers(request_context)["traceparent"]
        response_started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
                headers = MutableHeaders(scope=message)
                headers["X-Content-Type-Options"] = "nosniff"
                # 接口可显式声明缓存策略，其他响应默认禁止 HTTP 缓存存储。
                headers.setdefault("Cache-Control", "no-store")
                headers["X-Process-Time"] = str(time.perf_counter() - start_time)
                headers["traceparent"] = traceparent
            await send(message)

        otel_token = otel_context.attach(request_context)
        try:
            context.init(trace_id=trace_id)
            client = scope.get("client")
            client_host = client[0] if client else "unknown"
            query_string = scope.get("query_string", b"").decode()
            logger.info(
                f"access log, ip={client_host}, method={scope.get('method', 'GET')}, "
                f"path={scope['path']}, query_string={query_string}"
            )
            await self.app(scope, receive, send_wrapper)
            logger.info(
                f"response log, processing time={time.perf_counter() - start_time:.4f}s"
            )
        except Exception as exc:
            # 路由内的业务/校验错误由 FastAPI handler 处理；这里只兜底其他异常。
            error_resp = exception_response(exc, logs_enabled=self._logs_enabled)
            if not response_started:
                await error_resp(scope, receive, send_wrapper)
            else:
                logger.critical(
                    f"Response already started, cannot send error response. trace_id={trace_id}"
                )
        finally:
            try:
                context.clear()
            finally:
                otel_context.detach(otel_token)
