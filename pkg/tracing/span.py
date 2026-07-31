"""仅支持 ``async with`` 的 OpenTelemetry Span 门面。"""

import re
from collections.abc import Mapping
from contextlib import AbstractContextManager
from types import TracebackType
from typing import Literal

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


class _AsyncSpanContext:
    """根据 tracing runtime 状态创建真实 Span 或 non-recording Span。"""

    __slots__ = ("_attributes", "_entered", "_scope", "_span_kind", "_span_name")

    def __init__(
        self,
        *,
        span_name: str,
        span_kind: SpanKind,
        attributes: Mapping[str, AttributeValue] | None,
    ) -> None:
        self._span_name = span_name
        self._span_kind = span_kind
        self._attributes = attributes
        self._scope: AbstractContextManager[Span] | None = None
        self._entered = False

    def __enter__(self) -> Span:
        raise TypeError("span_context() supports only 'async with' usage")

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        raise TypeError("span_context() supports only 'async with' usage")

    async def __aenter__(self) -> Span:
        if self._entered:
            raise RuntimeError("span_context() cannot be entered more than once")
        self._entered = True

        if not is_tracing_enabled():
            return _NOOP_SPAN

        scope = get_tracer().start_as_current_span(
            self._span_name,
            kind=self._span_kind,
            attributes=self._attributes,
            record_exception=True,
            set_status_on_exception=True,
            end_on_exit=True,
        )
        self._scope = scope
        try:
            return scope.__enter__()
        except BaseException:
            self._scope = None
            self._entered = False
            raise

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> Literal[False]:
        scope = self._scope
        self._scope = None
        self._entered = False
        if scope is not None:
            scope.__exit__(exc_type, exc, tb)
        return False


def span_context(
    span_name: str,
    *,
    span_kind: SpanKind = SpanKind.INTERNAL,
    attributes: Mapping[str, AttributeValue] | None = None,
) -> _AsyncSpanContext:
    """创建异步 OpenTelemetry Span 上下文。"""

    if not isinstance(span_kind, SpanKind):
        raise TypeError(f"span_kind must be a SpanKind, got {type(span_kind).__name__}")
    if attributes is not None and not isinstance(attributes, Mapping):
        raise TypeError(
            f"attributes must be a mapping or None, got {type(attributes).__name__}"
        )
    return _AsyncSpanContext(
        span_name=_validate_span_name(span_name),
        span_kind=span_kind,
        attributes=attributes,
    )


__all__ = ["span_context"]
