"""异步 span 上下文与装饰器(参考OTEL)。"""

import re
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar, Token
from dataclasses import dataclass
from functools import wraps
from inspect import iscoroutinefunction
from itertools import count
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class SpanFrame:
    """当前活跃 span 的快照。"""

    span_seq: int
    parent_span_seq: int | None
    span_name: str
    span_path: str


class _SpanRuntime:
    """同一异步请求链内共享的 span 运行时。"""

    __slots__ = ("_seq_counter",)

    def __init__(self) -> None:
        self._seq_counter = count(1)

    def next_seq(self) -> int:
        return next(self._seq_counter)


_span_stack_var: ContextVar[tuple[SpanFrame, ...]] = ContextVar("logger_span_stack", default=())
_span_runtime_var: ContextVar[_SpanRuntime | None] = ContextVar("logger_span_runtime", default=None)
_SPAN_NAME_PATTERN = re.compile(r"^[A-Za-z0-9]+([._-][A-Za-z0-9]+)*$")
_SPAN_PATH_SEPARATOR = "|"


class _SpanLogger(Protocol):
    """span 日志记录器协议。"""

    def bind(self, **kwargs: Any) -> "_SpanLogger": ...

    def info(self, message: str, *args: Any, **kwargs: Any) -> None: ...

    def error(self, message: str, *args: Any, **kwargs: Any) -> None: ...


_span_logger: _SpanLogger | None = None


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


def configure_span_logger(logger: _SpanLogger | None) -> None:
    """配置 span 日志记录器。"""

    global _span_logger
    _span_logger = logger


def _require_span_logger() -> _SpanLogger:
    logger = _span_logger
    if logger is None:
        raise RuntimeError("Span logger not initialized. Call init_logger() or LoggerHandler.setup() first.")
    return logger


def get_current_span() -> SpanFrame | None:
    """返回当前活跃 span；如果不存在则返回 None。"""

    stack = _span_stack_var.get()
    if not stack:
        return None
    return stack[-1]


def get_span_record_extra() -> dict[str, Any]:
    """返回当前日志需要注入的 span 字段。"""

    frame = get_current_span()
    if frame is None:
        return {
            "span_seq": None,
            "parent_span_seq": None,
            "span_name": None,
            "span_path": None,
        }

    return {
        "span_seq": frame.span_seq,
        "parent_span_seq": frame.parent_span_seq,
        "span_name": frame.span_name,
        "span_path": frame.span_path,
    }


def _build_span_log_extra(
    frame: SpanFrame,
    *,
    span_event: str,
    span_status: str | None = None,
) -> dict[str, Any]:
    return {
        "span_seq": frame.span_seq,
        "parent_span_seq": frame.parent_span_seq,
        "span_name": frame.span_name,
        "span_path": frame.span_path,
        "span_event": span_event,
        "span_status": span_status,
    }


class _AsyncSpanContext:
    """仅支持 async with 的 span 上下文管理器。"""

    __slots__ = ("_span_name", "_frame", "_logger", "_start_at", "_stack_token", "_runtime_token")

    def __init__(self, *, span_name: str) -> None:
        self._span_name = span_name
        self._frame: SpanFrame | None = None
        self._logger = _require_span_logger()
        self._start_at: float | None = None
        self._stack_token: Token[tuple[SpanFrame, ...]] | None = None
        self._runtime_token: Token[_SpanRuntime | None] | None = None

    def _reset_state(self) -> None:
        if self._stack_token is not None:
            _span_stack_var.reset(self._stack_token)
        if self._runtime_token is not None:
            _span_runtime_var.reset(self._runtime_token)

        self._frame = None
        self._start_at = None
        self._stack_token = None
        self._runtime_token = None

    def __enter__(self) -> SpanFrame:
        raise TypeError("span_context() supports only 'async with' usage")

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: Any) -> bool:
        raise TypeError("span_context() supports only 'async with' usage")

    async def __aenter__(self) -> SpanFrame:
        runtime = _span_runtime_var.get()
        if runtime is None:
            runtime = _SpanRuntime()
            self._runtime_token = _span_runtime_var.set(runtime)

        current_stack = _span_stack_var.get()
        span_seq = runtime.next_seq()
        parent_span_seq = current_stack[-1].span_seq if current_stack else None
        path_segment = f"{span_seq}:{self._span_name}"
        span_path = f"{current_stack[-1].span_path}{_SPAN_PATH_SEPARATOR}{path_segment}" if current_stack else path_segment

        self._frame = SpanFrame(
            span_seq=span_seq,
            parent_span_seq=parent_span_seq,
            span_name=self._span_name,
            span_path=span_path,
        )
        self._stack_token = _span_stack_var.set((*current_stack, self._frame))
        self._start_at = time.perf_counter()

        try:
            self._logger.bind(**_build_span_log_extra(self._frame, span_event="start")).info("span.start")
        except Exception:
            self._reset_state()
            raise
        return self._frame

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: Any,
    ) -> bool:
        if self._frame is None:
            self._reset_state()
            return False

        elapsed_ms = 0.0
        if self._start_at is not None:
            elapsed_ms = round((time.perf_counter() - self._start_at) * 1000, 3)

        try:
            if exc is None:
                self._logger.bind(
                    **_build_span_log_extra(self._frame, span_event="end", span_status="ok"),
                    json_content={"elapsed_ms": elapsed_ms},
                ).info("span.end")
            else:
                error_type = exc_type.__name__ if exc_type is not None else type(exc).__name__
                self._logger.bind(
                    **_build_span_log_extra(self._frame, span_event="end", span_status="error"),
                    json_content={
                        "elapsed_ms": elapsed_ms,
                        "error_type": error_type,
                    }
                ).error("span.error")
        finally:
            self._reset_state()

        return False


def span_context(span_name: str) -> _AsyncSpanContext:
    """创建异步 span 上下文。"""

    return _AsyncSpanContext(span_name=_validate_span_name(span_name))


def with_span[**P, T](
    *,
    span_name: str,
) -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Awaitable[T]]]:
    """为 async def 注入 span 上下文。"""
    validated_span_name = _validate_span_name(span_name)

    def decorator(func: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
        if not iscoroutinefunction(func):
            raise TypeError("with_span() supports async def only")

        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            async with _AsyncSpanContext(span_name=validated_span_name):
                return await func(*args, **kwargs)
            raise RuntimeError("unreachable")

        return wrapper

    return decorator


__all__ = [
    "SpanFrame",
    "get_current_span",
    "get_span_record_extra",
    "span_context",
    "with_span",
]
