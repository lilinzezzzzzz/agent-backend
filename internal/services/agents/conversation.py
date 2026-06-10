from __future__ import annotations

from sqlalchemy import select, update

from internal.core import AppException, errors
from internal.dao.agent_conversation import (
    AgentMessageDao,
    AgentRunDao,
    AgentRunStepDao,
    AgentSessionDao,
    new_agent_message_dao,
    new_agent_run_dao,
    new_agent_run_step_dao,
    new_agent_session_dao,
)
from internal.models.agent_conversation import AgentMessage, AgentRun, AgentSession
from internal.services.dto.agent import (
    AgentConversationContextDTO,
    AgentMessageDTO,
    AgentRunResultDTO,
    AgentRunStartDTO,
    to_json_object,
    to_json_value,
)
from pkg.database.dao import execute_transaction
from pkg.ids import uuid6_unique_str_id
from pkg.toolkit.timer import utc_now_naive


class AgentConversationService:
    """Agent 会话存储用例服务。"""

    def __init__(self, *, storage_backend: DatabaseAgentStorageBackend):
        self._storage_backend = storage_backend

    async def start_run(self, **kwargs) -> AgentRunStartDTO:
        """创建或复用会话，并创建 running run。"""
        return await self._storage_backend.start_run(**kwargs)

    async def load_context(self, **kwargs) -> AgentConversationContextDTO:
        """读取会话上下文窗口。"""
        return await self._storage_backend.load_context(**kwargs)

    async def complete_run(self, **kwargs) -> None:
        """标记 run 完成并写入最终消息与步骤。"""
        await self._storage_backend.complete_run(**kwargs)

    async def fail_run(self, **kwargs) -> None:
        """标记 run 失败。"""
        await self._storage_backend.fail_run(**kwargs)


class DatabaseAgentStorageBackend:
    """基于当前主数据库的 Agent 会话存储后端。"""

    def __init__(
        self,
        *,
        session_dao: AgentSessionDao,
        message_dao: AgentMessageDao,
        run_dao: AgentRunDao,
        run_step_dao: AgentRunStepDao,
    ):
        self._session_dao = session_dao
        self._message_dao = message_dao
        self._run_dao = run_dao
        self._run_step_dao = run_step_dao

    async def start_run(
        self,
        *,
        user_id: int,
        session_id: str | None,
        entrypoint: str,
        agent_name: str,
        question: str,
        max_steps: int,
        trace_id: str | None,
    ) -> AgentRunStartDTO:
        """创建或校验会话，并写入用户消息和 running run。"""
        now = utc_now_naive()
        resolved_session_id = session_id or uuid6_unique_str_id()
        run_id = uuid6_unique_str_id()
        user_message_id = uuid6_unique_str_id()

        async def _tx(sess) -> None:
            session = await _get_session_for_update(
                sess=sess,
                session_id=resolved_session_id,
                user_id=user_id,
            )
            if session_id is not None and session is None:
                raise AppException(errors.NotFound, message="Agent 会话不存在")

            if session is None:
                session = AgentSession.create(
                    session_id=resolved_session_id,
                    user_id=user_id,
                    entrypoint=entrypoint,
                    status="active",
                    title=_build_session_title(question),
                    rolling_summary=None,
                    working_state={},
                    recent_token_count=0,
                    message_count=1,
                    last_message_at=now,
                    expires_at=None,
                    creator_id=user_id,
                    created_at=now,
                    updated_at=now,
                )
                sess.add(session)
            else:
                await sess.execute(
                    update(AgentSession)
                    .where(AgentSession.id == session.id)
                    .values(
                        message_count=AgentSession.message_count + 1,
                        last_message_at=now,
                        updater_id=user_id,
                        updated_at=now,
                    )
                )

            sess.add(
                AgentMessage.create(
                    message_id=user_message_id,
                    session_id=resolved_session_id,
                    run_id=run_id,
                    user_id=user_id,
                    role="user",
                    content=question,
                    content_summary=None,
                    token_count=0,
                    message_metadata=None,
                    creator_id=user_id,
                    created_at=now,
                    updated_at=now,
                )
            )
            sess.add(
                AgentRun.create(
                    run_id=run_id,
                    session_id=resolved_session_id,
                    user_id=user_id,
                    entrypoint=entrypoint,
                    agent_name=agent_name,
                    route=None,
                    status="running",
                    max_steps=max_steps,
                    trace_id=trace_id,
                    started_at=now,
                    ended_at=None,
                    elapsed_ms=0,
                    error_code=None,
                    error_message=None,
                    run_metadata=None,
                    creator_id=user_id,
                    created_at=now,
                    updated_at=now,
                )
            )

        await _execute_storage_transaction(self._session_dao.session_provider, _tx)
        return AgentRunStartDTO(
            session_id=resolved_session_id,
            run_id=run_id,
            user_message_id=user_message_id,
        )

    async def load_context(
        self,
        *,
        user_id: int,
        session_id: str,
        max_recent_messages: int,
        max_recent_chars: int,
        exclude_run_id: str | None = None,
    ) -> AgentConversationContextDTO:
        """读取滚动摘要和最近消息窗口。"""
        session = await self._session_dao.get_by_session_id_for_user(
            session_id=session_id,
            user_id=user_id,
        )
        if session is None:
            raise AppException(errors.NotFound, message="Agent 会话不存在")

        recent_messages = await self._message_dao.list_recent_messages(
            user_id=user_id,
            session_id=session_id,
            limit=max_recent_messages,
            max_chars=max_recent_chars,
            exclude_run_id=exclude_run_id,
        )
        return AgentConversationContextDTO(
            session_id=session_id,
            rolling_summary=session.rolling_summary,
            recent_messages=tuple(
                AgentMessageDTO(role=message.role, content=message.content)
                for message in recent_messages
            ),
            working_state=to_json_object(session.working_state or {}),
            truncated=len(recent_messages) >= max_recent_messages,
        )

    async def complete_run(
        self,
        *,
        user_id: int,
        session_id: str,
        run_id: str,
        route: str | None,
        result: AgentRunResultDTO,
    ) -> None:
        """写入 assistant 消息、steps，并把 run 标记为终态。"""
        now = utc_now_naive()

        async def _tx(sess) -> None:
            run = await _get_run_for_update(
                sess=sess,
                run_id=run_id,
                session_id=session_id,
                user_id=user_id,
            )
            if run is None:
                raise AppException(errors.NotFound, message="Agent run 不存在")

            sess.add(
                AgentMessage.create(
                    message_id=uuid6_unique_str_id(),
                    session_id=session_id,
                    run_id=run_id,
                    user_id=user_id,
                    role="assistant",
                    content=result.answer or "",
                    content_summary=None,
                    token_count=0,
                    message_metadata=None,
                    creator_id=user_id,
                    created_at=now,
                    updated_at=now,
                )
            )

            step_rows = [
                {
                    "run_id": run_id,
                    "session_id": session_id,
                    "user_id": user_id,
                    "step_index": step.index,
                    "status": step.status,
                    "action_type": step.action_type,
                    "tool": step.tool,
                    "args": step.args,
                    "action_result": to_json_value(step.action_result),
                    "artifact_id": None,
                    "error": step.error,
                    "elapsed_ms": step.elapsed_ms,
                    "creator_id": user_id,
                    "created_at": now,
                    "updated_at": now,
                }
                for step in result.steps
            ]
            if step_rows:
                stmt = self._run_step_dao.build_insert_rows_stmt(rows=step_rows)
                if stmt is not None:
                    await sess.execute(stmt)

            await sess.execute(
                update(AgentRun)
                .where(AgentRun.id == run.id)
                .values(
                    route=route,
                    status=result.status,
                    ended_at=now,
                    elapsed_ms=_elapsed_ms(run.started_at, now),
                    updater_id=user_id,
                    updated_at=now,
                )
            )
            await sess.execute(
                update(AgentSession)
                .where(
                    AgentSession.session_id == session_id,
                    AgentSession.user_id == user_id,
                    AgentSession.deleted_at.is_(None),
                )
                .values(
                    message_count=AgentSession.message_count + 1,
                    last_message_at=now,
                    updater_id=user_id,
                    updated_at=now,
                )
            )

        await _execute_storage_transaction(self._run_dao.session_provider, _tx)

    async def fail_run(
        self,
        *,
        user_id: int,
        session_id: str,
        run_id: str,
        error_code: str,
        error_message: str,
    ) -> None:
        """把 running run 标记为 failed。"""
        now = utc_now_naive()

        async def _tx(sess) -> None:
            run = await _get_run_for_update(
                sess=sess,
                run_id=run_id,
                session_id=session_id,
                user_id=user_id,
            )
            if run is None:
                return
            await sess.execute(
                update(AgentRun)
                .where(AgentRun.id == run.id)
                .values(
                    status="failed",
                    ended_at=now,
                    elapsed_ms=_elapsed_ms(run.started_at, now),
                    error_code=error_code,
                    error_message=error_message[:2000],
                    updater_id=user_id,
                    updated_at=now,
                )
            )

        await _execute_storage_transaction(self._run_dao.session_provider, _tx)


_agent_conversation_service: AgentConversationService | None = None


def new_agent_conversation_service() -> AgentConversationService:
    """依赖注入：获取 AgentConversationService 单例。"""
    global _agent_conversation_service
    if _agent_conversation_service is None:
        _agent_conversation_service = AgentConversationService(
            storage_backend=DatabaseAgentStorageBackend(
                session_dao=new_agent_session_dao(),
                message_dao=new_agent_message_dao(),
                run_dao=new_agent_run_dao(),
                run_step_dao=new_agent_run_step_dao(),
            )
        )
    return _agent_conversation_service


async def _execute_storage_transaction(session_provider, callback) -> None:
    try:
        await execute_transaction(session_provider, callback)
    except RuntimeError as exc:
        if isinstance(exc.__cause__, AppException):
            raise exc.__cause__ from exc
        raise


async def _get_session_for_update(*, sess, session_id: str, user_id: int) -> AgentSession | None:
    result = await sess.execute(
        select(AgentSession).where(
            AgentSession.session_id == session_id,
            AgentSession.user_id == user_id,
            AgentSession.deleted_at.is_(None),
        )
    )
    return result.scalars().first()


async def _get_run_for_update(
    *,
    sess,
    run_id: str,
    session_id: str,
    user_id: int,
) -> AgentRun | None:
    result = await sess.execute(
        select(AgentRun).where(
            AgentRun.run_id == run_id,
            AgentRun.session_id == session_id,
            AgentRun.user_id == user_id,
            AgentRun.deleted_at.is_(None),
        )
    )
    return result.scalars().first()


def _build_session_title(question: str) -> str:
    return question.strip()[:128] or "新会话"


def _elapsed_ms(started_at, ended_at) -> float:
    return round((ended_at - started_at).total_seconds() * 1000, 3)
