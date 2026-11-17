"""生命周期对象的延迟属性读取代理。

当模块需要先暴露一个对象，但真实实例要等应用启动流程中再初始化时使用，
例如配置、Redis、调度器、日志器或其他客户端单例。

每次读取目标属性时重新调用 getter，不缓存目标，便于生命周期重置或替换实例。
创建代理和 repr() 不调用 getter；属性访问中的 getter 异常原样传播。

代理不转发属性赋值、删除或特殊方法协议，例如调用、迭代、运算和上下文管理。
布尔判断使用代理自身语义，不能用于检查目标是否就绪；也不保证真实类型判断或
序列化透明。需要这些能力时，应显式调用 getter 获取真实对象。
"""

from collections.abc import Callable
from typing import Any, cast


def lazy_proxy[T](getter: Callable[[], T]) -> T:
    """创建延迟属性读取代理，返回类型 T 仅用于静态类型提示。"""
    return cast(T, _LazyProxy(getter))


class _LazyProxy[T]:
    """通过 getter 转发属性读取，不提供完整的对象代理协议。"""

    __slots__ = ("_getter",)

    def __init__(self, getter: Callable[[], T]) -> None:
        object.__setattr__(self, "_getter", getter)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._getter(), name)

    def __repr__(self) -> str:
        return "<_LazyProxy>"
