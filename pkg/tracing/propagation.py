"""W3C Trace Context 的轻量提取、生成和序列化，不初始化采集资源。"""

from collections.abc import Mapping
from secrets import randbits

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

_PROPAGATOR = TraceContextTextMapPropagator()


def _random_nonzero_id(bits: int) -> int:
    value = 0
    while value == 0:
        value = randbits(bits)
    return value


def extract_or_create_trace_context(
    headers: Mapping[str, str],
    *,
    base_context: Context | None = None,
) -> Context:
    """提取合法父上下文；缺失或非法时创建不采集数据的本地请求上下文。"""
    # 保留框架 Metrics/Logs 元数据，清除已有 Span 后再解析本次请求的身份。
    propagation_context = trace.set_span_in_context(
        trace.INVALID_SPAN,
        base_context if base_context is not None else Context(),
    )
    extracted = _PROPAGATOR.extract(headers, context=propagation_context)
    if trace.get_current_span(extracted).get_span_context().is_valid:
        return extracted

    span_context = SpanContext(
        trace_id=_random_nonzero_id(128),
        span_id=_random_nonzero_id(64),
        is_remote=False,
        trace_flags=TraceFlags(TraceFlags.DEFAULT),
    )
    return trace.set_span_in_context(NonRecordingSpan(span_context), propagation_context)


def trace_context_headers(context: Context) -> dict[str, str]:
    """将上下文序列化为标准 traceparent 和可选的 tracestate。"""
    headers: dict[str, str] = {}
    _PROPAGATOR.inject(headers, context=context)
    return headers
