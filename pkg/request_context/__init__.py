import contextlib
from contextvars import ContextVar
from enum import StrEnum
from typing import Any
from uuid import UUID

_request_context_var: ContextVar[dict[str, Any]] = ContextVar("request_context")


class ContextKey(StrEnum):
    """标准上下文 Key 定义，新增公共字段时统一在这里扩展。"""

    USER_ID = "user_id"
    TRACE_ID = "trace_id"


type ContextKeyType = str | ContextKey


def _normalize_key(key: ContextKeyType) -> str:
    return key.value if isinstance(key, ContextKey) else key


class _RequestContextManager:
    """
    请求上下文管理工具类
    """

    @staticmethod
    def init(**kwargs) -> dict[str, Any]:
        """
        初始化上下文，必须在中间件开始时调用
        """
        ctx = {
            **kwargs,
        }
        _request_context_var.set(ctx)
        return ctx

    @staticmethod
    def get(key: ContextKeyType, default: Any = None) -> Any:
        normalized_key = _normalize_key(key)
        try:
            ctx = _request_context_var.get()
            return ctx.get(normalized_key, default)
        except LookupError:
            # 如果没有 init，返回 default，兼容非 Web 环境调用
            return default

    @staticmethod
    def set(key: ContextKeyType, value: Any):
        normalized_key = _normalize_key(key)
        try:
            ctx = _request_context_var.get()
            ctx[normalized_key] = value
        except LookupError as e:
            # 修改：移除自动 init，直接报错或记录严重错误
            # 如果这是一个纯 Web 包，应该 raise 异常。
            # 如果为了兼容，打印 Error 且不执行操作可能更安全。
            raise RuntimeError(
                "Request Context not initialized. Is Middleware added?"
            ) from e

    @staticmethod
    def all() -> dict[str, Any]:
        try:
            return _request_context_var.get()
        except LookupError:
            return {}

    @staticmethod
    def clear():
        with contextlib.suppress(LookupError):
            _request_context_var.get().clear()


_ctx_manager = _RequestContextManager()


def init(**kwargs) -> dict[str, Any]:
    ctx = _ctx_manager.init(**kwargs)
    return ctx


def clear():
    _ctx_manager.clear()


def set_val(key: ContextKeyType, value: Any):
    _ctx_manager.set(key, value)


def get_val(key: ContextKeyType, default: Any = None):
    return _ctx_manager.get(key, default)


def set_user_id(user_id: UUID):
    _ctx_manager.set(ContextKey.USER_ID, user_id)


def get_user_id() -> UUID:
    user_id = _ctx_manager.get(ContextKey.USER_ID)
    if user_id is None:
        raise LookupError("user_id is not set")
    return user_id


def set_trace_id(trace_id: str):
    if not trace_id:
        raise ValueError("trace_id is mandatory and cannot be empty or None")

    if not isinstance(trace_id, str):
        raise ValueError("trace_id must be a string")

    _ctx_manager.set(ContextKey.TRACE_ID, trace_id)


def get_trace_id() -> str:
    trace_id = _ctx_manager.get(ContextKey.TRACE_ID)

    if not is_valid_trace_id(trace_id):
        raise LookupError(
            f"trace_id is invalid or not set, current value: {repr(trace_id)}"
        )

    return trace_id


def is_valid_trace_id(trace_id: Any) -> bool:
    """
    判断 trace_id 是否有效

    Args:
        trace_id: 待检查的 trace_id

    Returns:
        bool: 如果 trace_id 有效返回 True，否则返回 False
    """
    if not isinstance(trace_id, str):
        return False

    return trace_id not in ("unknown", "-")
