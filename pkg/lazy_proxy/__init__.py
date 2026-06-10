"""生命周期对象的延迟代理工具。

当模块需要先暴露一个对象，但真实实例要等应用启动流程中再初始化时使用，
例如配置、Redis、调度器、日志器或其他客户端单例。
"""

from collections.abc import Callable
from typing import Any, cast, overload


@overload
def lazy_proxy[T](getter: Callable[[], T]) -> T: ...


@overload
def lazy_proxy[T](getter: Callable[[], T], *, __type__: type[T]) -> T: ...


def lazy_proxy[T](getter: Callable[[], T], **kwargs: Any) -> T:
    """Create a lazy proxy that resolves the real object at access time."""
    return cast(T, _LazyProxy(getter))


class _LazyProxy[T]:
    """Forward attribute access to a lazily resolved object."""

    __slots__ = ("_getter",)

    def __init__(self, getter: Callable[[], T]) -> None:
        object.__setattr__(self, "_getter", getter)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._getter(), name)

    def __repr__(self) -> str:
        try:
            return repr(self._getter())
        except RuntimeError:
            return "<_LazyProxy: uninitialized>"
