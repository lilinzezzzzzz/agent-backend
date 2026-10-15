"""仅支持 ``async with`` 的 OpenTelemetry Span 门面。"""

import re
from collections.abc import AsyncGenerator, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from opentelemetry.trace import (
    INVALID_SPAN_CONTEXT,
    NonRecordingSpan,
    Span,
    SpanKind,
)
from opentelemetry.util.types import AttributeValue

from pkg.tracing.otel import get_tracer, is_tracing_enabled

_SPAN_NAME_PATTERN = re.compile(r"^[A-Za-z0-9]+([._-][A-Za-z0-9]+)*$")
_NOOP_SPAN = NonRecordingSpan(INVALID_SPAN_CONTEXT)


def _validate_span_name(span_name: str) -> str:
    if not isinstance(span_name, str):
        raise TypeError(f"span_name must be a string, got {type(span_name).__name__}")

    normalized = span_name.strip()
    if not normalized:
        raise ValueError("span_name cannot be empty")
    if _SPAN_NAME_PATTERN.fullmatch(normalized) is None:
        raise ValueError(
            "span_name must contain only letters, digits, '.', '_' or '-', "
            "and separators cannot appear at the beginning, end, or consecutively."
        )
    return normalized


@asynccontextmanager
async def _span_scope(
    span_name: str,
    *,
    span_kind: SpanKind,
    attributes: Mapping[str, AttributeValue] | None,
) -> AsyncGenerator[Span, None]:
    if not is_tracing_enabled():
        yield _NOOP_SPAN
        return

    with get_tracer().start_as_current_span(
        span_name,
        kind=span_kind,
        attributes=attributes,
        record_exception=True,
        set_status_on_exception=True,
        end_on_exit=True,
    ) as span:
        yield span


def span_context(
    span_name: str,
    *,
    span_kind: SpanKind = SpanKind.INTERNAL,
    attributes: Mapping[str, AttributeValue] | None = None,
) -> AbstractAsyncContextManager[Span]:
    """校验参数并创建异步 Span 上下文；每次使用时需创建新的管理器。"""

    if not isinstance(span_kind, SpanKind):
        raise TypeError(f"span_kind must be a SpanKind, got {type(span_kind).__name__}")
    if attributes is not None and not isinstance(attributes, Mapping):
        raise TypeError(
            f"attributes must be a mapping or None, got {type(attributes).__name__}"
        )
    return _span_scope(
        span_name=_validate_span_name(span_name),
        span_kind=span_kind,
        attributes=attributes,
    )


__all__ = ["span_context"]
