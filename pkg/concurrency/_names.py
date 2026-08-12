from collections.abc import Callable, Coroutine, Mapping
from functools import partial
from typing import Any


def format_callable_name(
    func_name: str | None,
    *,
    bound_owner: Any = None,
    fallback: str = "background-task",
) -> str:
    if not isinstance(func_name, str):
        return fallback
    if bound_owner is not None:
        owner_name = (
            bound_owner.__name__
            if isinstance(bound_owner, type)
            else bound_owner.__class__.__name__
        )
        return f"{owner_name}.{func_name}"
    return "lambda_func" if func_name == "<lambda>" else func_name


def get_coroutine_name(coro: Coroutine[Any, Any, Any]) -> str:
    code = getattr(coro, "cr_code", None)
    func_name = getattr(code, "co_name", None)

    frame = getattr(coro, "cr_frame", None)
    frame_locals = getattr(frame, "f_locals", None)
    if isinstance(frame_locals, Mapping):
        bound_self = frame_locals.get("self")
        if bound_self is not None:
            return format_callable_name(func_name, bound_owner=bound_self)

        bound_cls = frame_locals.get("cls")
        if isinstance(bound_cls, type):
            return format_callable_name(func_name, bound_owner=bound_cls)

    return format_callable_name(func_name)


def get_callable_name(func: Callable[..., Any]) -> str:
    while isinstance(func, partial):
        func = func.func

    bound_self = getattr(func, "__self__", None)
    func_name = getattr(func, "__name__", None)
    return format_callable_name(func_name, bound_owner=bound_self, fallback=str(func))
