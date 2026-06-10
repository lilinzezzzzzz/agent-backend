from __future__ import annotations

import pytest

from internal.core import AppException, errors
from internal.dao.agent_conversation import AgentMessageDao, AgentRunDao, AgentRunStepDao, AgentSessionDao
from internal.models.agent_conversation import AgentMessage, AgentRun, AgentRunStep, AgentSession
from internal.services.agents.conversation import AgentConversationService, DatabaseAgentStorageBackend
from internal.services.dto.agent import AgentRunDTO, AgentStepDTO


def _new_conversation_service(db_session) -> AgentConversationService:
    session_dao = AgentSessionDao(session_provider=db_session)
    message_dao = AgentMessageDao(session_provider=db_session)
    run_dao = AgentRunDao(session_provider=db_session)
    run_step_dao = AgentRunStepDao(session_provider=db_session)
    return AgentConversationService(
        storage_backend=DatabaseAgentStorageBackend(
            session_dao=session_dao,
            message_dao=message_dao,
            run_dao=run_dao,
            run_step_dao=run_step_dao,
        )
    )


@pytest.mark.asyncio
async def test_agent_conversation_service_creates_and_completes_run(db_session) -> None:
    service = _new_conversation_service(db_session)

    started = await service.start_run(
        user_id=999,
        session_id=None,
        entrypoint="order_support",
        agent_name="order_support",
        question="订单 1001 到哪了？",
        max_steps=3,
        trace_id="trace-agent",
    )
    context = await service.load_context(
        user_id=999,
        session_id=started.session_id,
        max_recent_messages=20,
        max_recent_chars=12000,
        exclude_run_id=started.run_id,
    )
    result = AgentRunDTO(
        session_id=started.session_id,
        run_id=started.run_id,
        status="completed",
        answer="订单 1001 正在运输中。",
        steps=[
            AgentStepDTO(
                index=0,
                status="completed",
                action_type="tool_call",
                tool="get_order_status",
                args={"order_id": "1001"},
                action_result={"ok": True, "status": "运输中"},
                elapsed_ms=1.2,
            )
        ],
    )

    await service.complete_run(
        user_id=999,
        session_id=started.session_id,
        run_id=started.run_id,
        route="order",
        result=result,
    )

    session_dao = AgentSessionDao(session_provider=db_session)
    message_dao = AgentMessageDao(session_provider=db_session)
    run_dao = AgentRunDao(session_provider=db_session)
    step_dao = AgentRunStepDao(session_provider=db_session)
    session = await session_dao.querier_unsorted.eq_(AgentSession.session_id, started.session_id).first()
    messages = await message_dao.querier_unsorted.eq_(AgentMessage.session_id, started.session_id).all()
    run = await run_dao.querier_unsorted.eq_(AgentRun.run_id, started.run_id).first()
    steps = await step_dao.querier_unsorted.eq_(AgentRunStep.run_id, started.run_id).all()

    assert context.recent_messages == ()
    assert session is not None
    assert session.user_id == 999
    assert session.message_count == 2
    assert run is not None
    assert run.status == "completed"
    assert run.route == "order"
    assert run.trace_id == "trace-agent"
    assert [message.role for message in sorted(messages, key=lambda item: item.created_at)] == ["user", "assistant"]
    assert steps[0].tool == "get_order_status"
    assert steps[0].action_result == {"ok": True, "status": "运输中"}


@pytest.mark.asyncio
async def test_agent_conversation_service_rejects_session_from_another_user(db_session) -> None:
    service = _new_conversation_service(db_session)
    started = await service.start_run(
        user_id=999,
        session_id=None,
        entrypoint="order_support",
        agent_name="order_support",
        question="订单 1001 到哪了？",
        max_steps=3,
        trace_id=None,
    )

    with pytest.raises(AppException) as exc_info:
        await service.start_run(
            user_id=1000,
            session_id=started.session_id,
            entrypoint="order_support",
            agent_name="order_support",
            question="继续查询",
            max_steps=3,
            trace_id=None,
        )

    assert exc_info.value.error == errors.NotFound
