from __future__ import annotations

from functools import cache
from uuid import UUID

from internal.infra.database import get_read_session, get_session
from internal.models.agent_conversation import (
    AgentMessage,
    AgentRun,
    AgentRunStep,
    AgentSession,
)
from pkg.database.dao import BaseDao


class AgentSessionDao(BaseDao[AgentSession]):
    """Agent session DAO。"""

    _model_cls: type[AgentSession] = AgentSession

    async def get_by_session_id_for_user(
        self, *, session_id: str, user_id: UUID
    ) -> AgentSession | None:
        """按 session_id 和 user_id 查询未删除会话。"""
        statement = self.select_stmt().where(
            self.model_cls.session_id == session_id,
            self.model_cls.user_id == user_id,
        )
        return await self.fetch_first(statement)


class AgentMessageDao(BaseDao[AgentMessage]):
    """Agent message DAO。"""

    _model_cls: type[AgentMessage] = AgentMessage

    async def list_recent_messages(
        self,
        *,
        user_id: UUID,
        session_id: str,
        limit: int,
        max_chars: int,
        exclude_run_id: str | None = None,
    ) -> list[AgentMessage]:
        """读取最近消息，按创建时间正序返回。"""
        stmt = (
            self.select_stmt()
            .where(
                self.model_cls.user_id == user_id,
                self.model_cls.session_id == session_id,
            )
            .order_by(self.model_cls.created_at.desc(), self.model_cls.id.desc())
            .limit(limit)
        )
        if exclude_run_id is not None:
            stmt = stmt.where(self.model_cls.run_id != exclude_run_id)

        rows = await self.fetch_all(stmt)

        messages: list[AgentMessage] = []
        current_chars = 0
        for message in rows:
            content_len = len(message.content or "")
            if messages and current_chars + content_len > max_chars:
                break
            messages.append(message)
            current_chars += content_len
        return list(reversed(messages))


class AgentRunDao(BaseDao[AgentRun]):
    """Agent run DAO。"""

    _model_cls: type[AgentRun] = AgentRun

    async def get_by_run_id_for_user(
        self, *, run_id: str, user_id: UUID
    ) -> AgentRun | None:
        """按 run_id 和 user_id 查询未删除运行记录。"""
        statement = self.select_stmt().where(
            self.model_cls.run_id == run_id,
            self.model_cls.user_id == user_id,
        )
        return await self.fetch_first(statement)


class AgentRunStepDao(BaseDao[AgentRunStep]):
    """Agent run step DAO。"""

    _model_cls: type[AgentRunStep] = AgentRunStep


@cache
def new_agent_session_dao() -> AgentSessionDao:
    """依赖注入：获取 AgentSessionDao 单例。"""
    return AgentSessionDao(
        session_provider=get_session, read_session_provider=get_read_session
    )


@cache
def new_agent_message_dao() -> AgentMessageDao:
    """依赖注入：获取 AgentMessageDao 单例。"""
    return AgentMessageDao(
        session_provider=get_session, read_session_provider=get_read_session
    )


@cache
def new_agent_run_dao() -> AgentRunDao:
    """依赖注入：获取 AgentRunDao 单例。"""
    return AgentRunDao(
        session_provider=get_session, read_session_provider=get_read_session
    )


@cache
def new_agent_run_step_dao() -> AgentRunStepDao:
    """依赖注入：获取 AgentRunStepDao 单例。"""
    return AgentRunStepDao(
        session_provider=get_session, read_session_provider=get_read_session
    )
