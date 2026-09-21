from __future__ import annotations

from functools import cache
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from internal.infra.database import get_read_session, get_session
from internal.models.agent_conversation import (
    AgentMessage,
    AgentRun,
    AgentRunAttempt,
    AgentRunCheckpoint,
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
        self,
        *,
        run_id: str,
        user_id: UUID,
        session: AsyncSession | None = None,
    ) -> AgentRun | None:
        """按 run_id 和 user_id 查询未删除运行记录。

        传入 `session` 时复用调用方连接，用于必须读取主库权威状态的场景；不传时走
        `read_session_provider`（生产环境可能是只读副本）。
        """
        statement = self.select_stmt().where(
            self.model_cls.run_id == run_id,
            self.model_cls.user_id == user_id,
        )
        return await self.fetch_first(statement, session=session)


class AgentRunCheckpointDao(BaseDao[AgentRunCheckpoint]):
    """Agent run checkpoint DAO。"""

    _model_cls: type[AgentRunCheckpoint] = AgentRunCheckpoint


class AgentRunAttemptDao(BaseDao[AgentRunAttempt]):
    """Agent run attempt DAO。"""

    _model_cls: type[AgentRunAttempt] = AgentRunAttempt

    async def max_attempt_no(self, *, sess, run_id: str) -> int:
        """读取指定 run 当前最大 attempt 序号；没有记录时返回 0。"""
        statement = select(func.max(self.model_cls.attempt_no)).where(
            self.model_cls.run_id == run_id
        )
        result = await sess.execute(statement)
        return result.scalar() or 0

    async def latest_attempt(
        self,
        *,
        run_id: str,
        user_id: UUID,
        session: AsyncSession | None = None,
    ) -> AgentRunAttempt | None:
        """按 attempt 序号倒序读取最近一次执行尝试。

        传入 `session` 时复用调用方连接，保证与同一次权威读取使用一致快照。
        """
        statement = (
            self.select_stmt()
            .where(
                self.model_cls.run_id == run_id,
                self.model_cls.user_id == user_id,
            )
            .order_by(self.model_cls.attempt_no.desc())
        )
        return await self.fetch_first(statement, session=session)


class AgentRunStepDao(BaseDao[AgentRunStep]):
    """Agent run step DAO。"""

    _model_cls: type[AgentRunStep] = AgentRunStep

    async def list_step_indexes(self, *, sess, run_id: str) -> set[int]:
        """读取指定 run 已提交的步骤序号集合。"""
        statement = select(self.model_cls.step_index).where(
            self.model_cls.run_id == run_id
        )
        result = await sess.execute(statement)
        return {row for row in result.scalars().all() if row is not None}


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


@cache
def new_agent_run_checkpoint_dao() -> AgentRunCheckpointDao:
    """依赖注入：获取 AgentRunCheckpointDao 单例。"""
    return AgentRunCheckpointDao(
        session_provider=get_session, read_session_provider=get_read_session
    )


@cache
def new_agent_run_attempt_dao() -> AgentRunAttemptDao:
    """依赖注入：获取 AgentRunAttemptDao 单例。"""
    return AgentRunAttemptDao(
        session_provider=get_session, read_session_provider=get_read_session
    )
