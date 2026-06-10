from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from internal.agents.router import AgentRoute
from internal.cache import new_agent_action_cache
from internal.core import AppException, errors


class AgentConfirmationStore(Protocol):
    """Agent confirmation resolver 使用的最小 pending action 存储协议。"""

    async def get_pending_action(self, *, token: str) -> dict[str, object] | None: ...


@dataclass(frozen=True, slots=True)
class AgentConfirmationContext:
    """服务端 pending action 解析出的确认上下文。"""

    route: AgentRoute
    action: str
    session_id: str | None = None


class AgentConfirmationResolver:
    """根据 confirmation_token 解析服务端可信的确认动作上下文。"""

    def __init__(self, *, action_store: AgentConfirmationStore):
        self._action_store = action_store

    async def resolve(
        self,
        *,
        user_id: int,
        confirmation_token: str,
    ) -> AgentConfirmationContext:
        """读取 pending action，并返回其服务端可信业务路由。"""
        if not confirmation_token or len(confirmation_token) > 128:
            raise AppException(errors.BadRequest, message="确认 token 无效")

        pending = await self._action_store.get_pending_action(token=confirmation_token)
        if pending is None:
            raise AppException(errors.BadRequest, message="确认 token 无效、已过期或已使用")
        if pending.get("user_id") != user_id:
            raise AppException(errors.Forbidden, message="确认 token 不属于当前用户")

        route = _read_route(pending)
        action = _read_required_str(pending, "action")
        session_id = _read_optional_str(pending, "session_id")
        return AgentConfirmationContext(route=route, action=action, session_id=session_id)


_agent_confirmation_resolver: AgentConfirmationResolver | None = None


def new_agent_confirmation_resolver() -> AgentConfirmationResolver:
    """依赖注入：获取 AgentConfirmationResolver 单例。"""
    global _agent_confirmation_resolver
    if _agent_confirmation_resolver is None:
        _agent_confirmation_resolver = AgentConfirmationResolver(
            action_store=new_agent_action_cache(),
        )
    return _agent_confirmation_resolver


def _read_route(payload: dict[str, object]) -> AgentRoute:
    raw_route = _read_required_str(payload, "route")
    try:
        return AgentRoute(raw_route)
    except ValueError as exc:
        raise AppException(errors.BadRequest, message="确认 token 对应的业务域不受支持") from exc


def _read_required_str(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise AppException(errors.BadRequest, message=f"确认 token 缺少动作字段: {key}")
    return value


def _read_optional_str(payload: dict[str, object], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise AppException(errors.BadRequest, message=f"确认 token 动作字段无效: {key}")
    return value or None
