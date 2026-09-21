"""受管理执行 Service 的编排测试（真实存储后端 + SQLite + 脚本化 LLM）。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

import pytest

from internal.agents.order import OrderAgentBuilder
from internal.agents.registry import AgentDefinitionRegistry
from internal.core import AppException, errors
from internal.dao.agent_conversation import (
    AgentMessageDao,
    AgentRunAttemptDao,
    AgentRunCheckpointDao,
    AgentRunDao,
    AgentRunStepDao,
    AgentSessionDao,
)
from internal.models.agent_conversation import AgentRunCheckpoint
from internal.schemas.agent import AgentStreamEventName
from internal.services.agents.conversation import DatabaseAgentStorageBackend
from internal.services.agents.execution import (
    AgentExecutionLimits,
    AgentExecutionService,
)
from pkg.agents import LLMActionModel
from pkg.database.audit import AuditActor
from pkg.toolkit.json import orjson_loads

TEST_USER_ID = UUID("00000000-0000-7000-8000-000000000999")
OTHER_USER_ID = UUID("00000000-0000-7000-8000-000000001000")

CREATE_KEY = "create_key_exec_1"
RESUME_KEY = "resume_key_exec_1"
QUESTION = "订单 1001 到哪了？"
FINAL_ANSWER = "订单 1001 已发货。"


class StubAuditService:
    """测试用审计写入：只记录调用次数，不访问数据库。"""

    def __init__(self) -> None:
        self.records: list[str] = []

    async def record_agent_run(self, **kwargs) -> bool:
        self.records.append(kwargs["result"].run_id)
        return True


class StubActionStore:
    """确认动作缓存桩：受管理测试默认没有待确认动作。"""

    def __init__(self, *, pending: dict[str, Any] | None = None) -> None:
        self.pending = dict(pending or {})
        self.lookups: list[str] = []

    async def get_pending_action(self, *, token: str):
        self.lookups.append(token)
        return self.pending.get(token)


class StubRagService:
    """订单知识检索占位；测试不触发检索工具。"""

    async def retrieve(self, **kwargs):  # pragma: no cover - 不应被调用
        raise AssertionError("RAG should not be called in this test")


class ScriptedOrderService:
    """订单服务桩：记录调用次数，并可在首次调用时触发打断。"""

    def __init__(self, *, on_first_call=None) -> None:
        self.on_first_call = on_first_call
        self.calls: list[str] = []

    async def get_order_status(self, *, user_id: UUID, order_id: str):
        self.calls.append(order_id)
        if self.on_first_call is not None:
            hook, self.on_first_call = self.on_first_call, None
            await hook()
        return StubOrderStatus(order_id=order_id)


class StubOrderStatus:
    def __init__(self, *, order_id: str) -> None:
        self.order_id = order_id

    def to_action_result(self) -> dict[str, Any]:
        return {"ok": True, "order_id": self.order_id, "status": "shipped"}


class ScriptedLLMClient:
    """按 response_model 返回脚本化结构化输出，并记录 action maker 的输入。"""

    provider = "test-provider"
    model = "test-model"

    def __init__(self, actions: list[Mapping[str, Any]]) -> None:
        self._actions = list(actions)
        self.action_inputs: list[dict[str, Any]] = []

    async def response_structured(self, *, input, response_model, **kwargs):
        assert response_model is LLMActionModel, response_model
        payload = orjson_loads(input[-1]["content"])
        self.action_inputs.append(payload)
        if not self._actions:
            raise AssertionError("scripted LLM ran out of actions")
        return response_model.model_validate(self._actions.pop(0))


def build_storage(db_session) -> DatabaseAgentStorageBackend:
    return DatabaseAgentStorageBackend(
        session_dao=AgentSessionDao(session_provider=db_session),
        message_dao=AgentMessageDao(session_provider=db_session),
        run_dao=AgentRunDao(session_provider=db_session),
        run_step_dao=AgentRunStepDao(session_provider=db_session),
        checkpoint_dao=AgentRunCheckpointDao(session_provider=db_session),
        attempt_dao=AgentRunAttemptDao(session_provider=db_session),
    )


def build_service(
    db_session,
    *,
    llm_client: ScriptedLLMClient,
    order_service: ScriptedOrderService | None = None,
    action_store: StubActionStore | None = None,
    storage: DatabaseAgentStorageBackend | None = None,
) -> tuple[AgentExecutionService, StubAuditService]:
    storage = storage or build_storage(db_session)
    audit_service = StubAuditService()
    service = AgentExecutionService(
        storage=storage,
        definitions=AgentDefinitionRegistry(
            llm_client=llm_client,
            order_service=order_service or ScriptedOrderService(),
            rag_service=StubRagService(),
        ),
        llm_client=llm_client,
        audit_service=audit_service,
        action_store=action_store or StubActionStore(),
        # control poll 保持较大值，避免测试中与内存 SQLite 争用同一连接。
        limits=AgentExecutionLimits(
            lease_seconds=30,
            control_poll_seconds=30,
            cancel_grace_seconds=5,
            stream_buffer_size=8,
        ),
    )
    return service, audit_service


async def count_messages(db_session, *, role: str) -> int:
    dao = AgentMessageDao(session_provider=db_session)
    rows = await dao.fetch_all(dao.select_stmt().where(dao.model_cls.role == role))
    return len(rows)


async def count_steps(db_session, run_id: str) -> int:
    dao = AgentRunStepDao(session_provider=db_session)
    rows = await dao.fetch_all(dao.select_stmt().where(dao.model_cls.run_id == run_id))
    return len(rows)


@pytest.mark.asyncio
async def test_resume_completes_run_and_replays_same_request_key(db_session) -> None:
    llm_client = ScriptedLLMClient([{"type": "final", "answer": FINAL_ANSWER}])
    service, audit_service = build_service(db_session, llm_client=llm_client)

    created = await service.create_run(
        user_id=TEST_USER_ID,
        entrypoint="order_support",
        question=QUESTION,
        session_id=None,
        max_steps=3,
        request_key=CREATE_KEY,
    )
    assert created.status == "ready"

    resumed = await service.resume_run(
        user_id=TEST_USER_ID, run_id=created.run_id, request_key=RESUME_KEY
    )

    assert resumed.result.status == "completed"
    assert resumed.result.answer == FINAL_ANSWER
    assert resumed.run.status == "completed"
    assert resumed.run.phase == "final_ready"
    assert resumed.run.resumable is False
    assert resumed.run.completed_steps == 1
    assert audit_service.records == [created.run_id]

    # 只有创建时写一次用户消息，终态提交写一次 assistant 消息。
    assert await count_messages(db_session, role="user") == 1
    assert await count_messages(db_session, role="assistant") == 1
    assert await count_steps(db_session, created.run_id) == 1

    # 同一 request_key 重试只返回已保存结果，不创建第二个执行者。
    replayed = await service.resume_run(
        user_id=TEST_USER_ID, run_id=created.run_id, request_key=RESUME_KEY
    )
    assert replayed.result.answer == FINAL_ANSWER
    assert await count_messages(db_session, role="assistant") == 1

    attempt_dao = AgentRunAttemptDao(session_provider=db_session)
    attempts = await attempt_dao.fetch_all(attempt_dao.select_stmt())
    assert len(attempts) == 1


@pytest.mark.asyncio
async def test_interrupt_during_tool_keeps_step_and_resume_does_not_redo_it(
    db_session,
) -> None:
    storage = build_storage(db_session)
    created_holder: dict[str, str] = {}

    async def request_interrupt() -> None:
        await storage.request_run_interrupt(
            user_id=TEST_USER_ID,
            run_id=created_holder["run_id"],
            reason="user_cancel",
        )

    order_service = ScriptedOrderService(on_first_call=request_interrupt)
    llm_client = ScriptedLLMClient(
        [
            {
                "type": "tool_call",
                "tool": "get_order_status",
                "args": {"order_id": "1001"},
            },
            {"type": "final", "answer": FINAL_ANSWER},
        ]
    )
    service, _ = build_service(
        db_session, llm_client=llm_client, order_service=order_service
    )

    created = await service.create_run(
        user_id=TEST_USER_ID,
        entrypoint="order_support",
        question=QUESTION,
        session_id=None,
        max_steps=3,
        request_key=CREATE_KEY,
    )
    created_holder["run_id"] = created.run_id

    paused = await service.resume_run(
        user_id=TEST_USER_ID, run_id=created.run_id, request_key=RESUME_KEY
    )

    # 工具返回后先保存结果再暂停：步骤 0 已提交，模型不会被再次调用生成同一动作。
    assert paused.result.status == "interrupted"
    assert paused.run.status == "interrupted"
    assert paused.run.resumable is True
    assert paused.run.completed_steps == 1
    assert order_service.calls == ["1001"]
    assert await count_messages(db_session, role="assistant") == 0
    assert llm_client.action_inputs[0]["previous_steps"] == []

    completed = await service.resume_run(
        user_id=TEST_USER_ID, run_id=created.run_id, request_key="resume_key_exec_2"
    )

    assert completed.result.status == "completed"
    assert completed.result.answer == FINAL_ANSWER
    assert order_service.calls == ["1001"]
    # 恢复时动作 maker 看到的第一个输入已经包含已提交的步骤 0。
    assert len(llm_client.action_inputs[1]["previous_steps"]) == 1
    assert await count_messages(db_session, role="assistant") == 1
    assert await count_steps(db_session, created.run_id) == 2

    attempt_dao = AgentRunAttemptDao(session_provider=db_session)
    attempts = await attempt_dao.fetch_all(
        attempt_dao.select_stmt().order_by(attempt_dao.model_cls.attempt_no)
    )
    assert [a.attempt_no for a in attempts] == [1, 2]
    assert attempts[0].status == "interrupted"
    assert attempts[1].status == "completed"


@pytest.mark.asyncio
async def test_interrupt_request_before_first_execution_returns_conflict(
    db_session,
) -> None:
    service, _ = build_service(
        db_session,
        llm_client=ScriptedLLMClient([{"type": "final", "answer": FINAL_ANSWER}]),
    )
    created = await service.create_run(
        user_id=TEST_USER_ID,
        entrypoint="order_support",
        question=QUESTION,
        session_id=None,
        max_steps=3,
        request_key=CREATE_KEY,
    )

    with pytest.raises(AppException) as excinfo:
        await service.interrupt_run(
            user_id=TEST_USER_ID, run_id=created.run_id, reason="too_early"
        )
    assert excinfo.value.error is errors.AgentRunStateConflict


@pytest.mark.asyncio
async def test_chat_entrypoint_routes_then_freezes_route(db_session) -> None:
    llm_client = ScriptedLLMClient([{"type": "final", "answer": FINAL_ANSWER}])
    service, _ = build_service(db_session, llm_client=llm_client)

    created = await service.create_run(
        user_id=TEST_USER_ID,
        entrypoint="chat",
        question=QUESTION,
        session_id=None,
        max_steps=3,
        request_key=CREATE_KEY,
    )
    view_before = await service.get_run(user_id=TEST_USER_ID, run_id=created.run_id)
    assert view_before.phase == "routing"
    assert view_before.route is None

    resumed = await service.resume_run(
        user_id=TEST_USER_ID, run_id=created.run_id, request_key=RESUME_KEY
    )

    assert resumed.result.status == "completed"
    assert resumed.run.route == "order"
    assert resumed.run.agent_name == "order_support"


@pytest.mark.asyncio
async def test_chat_entrypoint_unsupported_returns_degraded_answer(db_session) -> None:
    llm_client = ScriptedLLMClient([])
    service, _ = build_service(db_session, llm_client=llm_client)

    created = await service.create_run(
        user_id=TEST_USER_ID,
        entrypoint="chat",
        question="请帮我写一首诗",
        session_id=None,
        max_steps=3,
        request_key=CREATE_KEY,
    )
    resumed = await service.resume_run(
        user_id=TEST_USER_ID, run_id=created.run_id, request_key=RESUME_KEY
    )

    assert resumed.result.status == "completed"
    assert resumed.result.answer is not None
    assert "仅支持" in resumed.result.answer
    assert resumed.run.route == "unsupported"
    assert llm_client.action_inputs == []
    assert await count_messages(db_session, role="assistant") == 1


@pytest.mark.asyncio
async def test_definition_version_mismatch_refuses_resume(db_session) -> None:
    llm_client = ScriptedLLMClient([{"type": "final", "answer": FINAL_ANSWER}])
    service, _ = build_service(db_session, llm_client=llm_client)
    storage = build_storage(db_session)

    created = await service.create_run(
        user_id=TEST_USER_ID,
        entrypoint="order_support",
        question=QUESTION,
        session_id=None,
        max_steps=3,
        request_key=CREATE_KEY,
    )

    checkpoint_dao = AgentRunCheckpointDao(session_provider=db_session)
    row = await checkpoint_dao.fetch_first(
        checkpoint_dao.select_stmt().where(AgentRunCheckpoint.run_id == created.run_id)
    )
    payload = dict(row.payload)
    payload["definition_version"] = "other-v9"
    await checkpoint_dao.execute_update(
        checkpoint_dao.update_stmt(
            AgentRunCheckpoint.id == row.id,
            values={"payload": payload},
            audit_actor=AuditActor.user(TEST_USER_ID),
        )
    )

    with pytest.raises(AppException) as excinfo:
        await service.resume_run(
            user_id=TEST_USER_ID,
            run_id=created.run_id,
            request_key=RESUME_KEY,
        )
    assert excinfo.value.error is errors.AgentDefinitionIncompatible
    assert storage is not None


@pytest.mark.asyncio
async def test_resume_stream_emits_managed_events(db_session) -> None:
    llm_client = ScriptedLLMClient([{"type": "final", "answer": FINAL_ANSWER}])
    service, _ = build_service(db_session, llm_client=llm_client)

    created = await service.create_run(
        user_id=TEST_USER_ID,
        entrypoint="order_support",
        question=QUESTION,
        session_id=None,
        max_steps=3,
        request_key=CREATE_KEY,
    )

    events = [
        event
        async for event in service.resume_run_stream(
            user_id=TEST_USER_ID,
            run_id=created.run_id,
            request_key=RESUME_KEY,
        )
    ]
    names = [event.event for event in events]

    # run 创建后的第一次执行发送 run_started；后续 attempt 才发送 run_resumed。
    assert names[0] is AgentStreamEventName.RUN_STARTED
    assert AgentStreamEventName.STEP_COMPLETED in names
    assert names[-1] is AgentStreamEventName.RUN_COMPLETED
    for event in events:
        assert event.data["run_id"] == created.run_id
        assert event.data["session_id"] == created.session_id
        assert event.data["attempt_no"] == 1
    assert events[-1].result is not None
    assert events[-1].result.answer == FINAL_ANSWER

    # 重复 resume 只输出当前状态，不接管或重放原流。
    replayed = [
        event
        async for event in service.resume_run_stream(
            user_id=TEST_USER_ID,
            run_id=created.run_id,
            request_key=RESUME_KEY,
        )
    ]
    assert [event.event for event in replayed] == [AgentStreamEventName.RUN_STATUS]


@pytest.mark.asyncio
async def test_resume_refuses_when_confirmation_token_expired(db_session) -> None:
    """checkpoint 中的确认 token 过期或被消费时拒绝恢复，不自动重新签发。"""
    llm_client = ScriptedLLMClient(
        [
            {
                "type": "tool_call",
                "tool": "prepare_invoice_request",
                "args": {"order_id": "1001"},
            },
            {"type": "final", "answer": "请确认开票申请。"},
        ]
    )
    action_store = StubActionStore()
    service, _ = build_service(
        db_session, llm_client=llm_client, action_store=action_store
    )

    created = await service.create_run(
        user_id=TEST_USER_ID,
        entrypoint="order_support",
        question="订单 1001 开票",
        session_id=None,
        max_steps=3,
        request_key=CREATE_KEY,
    )
    # 直接构造带确认 token 的已提交现场，模拟暂停期间 token 失效。
    checkpoint_dao = AgentRunCheckpointDao(session_provider=db_session)
    row = await checkpoint_dao.fetch_first(
        checkpoint_dao.select_stmt().where(AgentRunCheckpoint.run_id == created.run_id)
    )
    payload = dict(row.payload)
    payload["next_step_index"] = 1
    payload["steps"] = [
        {
            "index": 0,
            "status": "completed",
            "action": {
                "type": "tool_call",
                "tool": "prepare_invoice_request",
                "args": {"order_id": "1001"},
                "call_id": None,
            },
            "action_result": {
                "ok": True,
                "confirmation": {"token": "expired-token", "action": "invoice"},
            },
            "error": None,
            "elapsed_ms": 1.0,
        }
    ]
    await checkpoint_dao.execute_update(
        checkpoint_dao.update_stmt(
            AgentRunCheckpoint.id == row.id,
            values={"payload": payload, "next_step_index": 1},
            audit_actor=AuditActor.user(TEST_USER_ID),
        )
    )

    with pytest.raises(AppException) as excinfo:
        await service.resume_run(
            user_id=TEST_USER_ID,
            run_id=created.run_id,
            request_key=RESUME_KEY,
        )
    assert excinfo.value.error is errors.AgentResumeUnsafe
    assert action_store.lookups == ["expired-token"]


@pytest.mark.asyncio
async def test_resume_stream_after_interrupt_starts_with_run_resumed(
    db_session,
) -> None:
    storage = build_storage(db_session)
    holder: dict[str, str] = {}

    async def request_interrupt() -> None:
        await storage.request_run_interrupt(
            user_id=TEST_USER_ID, run_id=holder["run_id"], reason="user_cancel"
        )

    order_service = ScriptedOrderService(on_first_call=request_interrupt)
    llm_client = ScriptedLLMClient(
        [
            {
                "type": "tool_call",
                "tool": "get_order_status",
                "args": {"order_id": "1001"},
            },
            {"type": "final", "answer": FINAL_ANSWER},
        ]
    )
    service, _ = build_service(
        db_session, llm_client=llm_client, order_service=order_service
    )
    created = await service.create_run(
        user_id=TEST_USER_ID,
        entrypoint="order_support",
        question=QUESTION,
        session_id=None,
        max_steps=3,
        request_key=CREATE_KEY,
    )
    holder["run_id"] = created.run_id

    first = [
        event
        async for event in service.resume_run_stream(
            user_id=TEST_USER_ID,
            run_id=created.run_id,
            request_key=RESUME_KEY,
        )
    ]
    first_names = [event.event for event in first]
    assert first_names[-1] is AgentStreamEventName.RUN_INTERRUPTED
    assert AgentStreamEventName.RUN_COMPLETED not in first_names

    second = [
        event
        async for event in service.resume_run_stream(
            user_id=TEST_USER_ID,
            run_id=created.run_id,
            request_key="resume_key_exec_2",
        )
    ]
    second_names = [event.event for event in second]
    assert second_names[0] is AgentStreamEventName.RUN_RESUMED
    assert second_names[-1] is AgentStreamEventName.RUN_COMPLETED
    assert second[-1].data["attempt_no"] == 2
    assert second[-1].result.answer == FINAL_ANSWER


@pytest.mark.asyncio
async def test_resume_rejects_runs_of_other_users(db_session) -> None:
    service, _ = build_service(
        db_session,
        llm_client=ScriptedLLMClient([{"type": "final", "answer": FINAL_ANSWER}]),
    )
    created = await service.create_run(
        user_id=TEST_USER_ID,
        entrypoint="order_support",
        question=QUESTION,
        session_id=None,
        max_steps=3,
        request_key=CREATE_KEY,
    )

    with pytest.raises(AppException) as get_error:
        await service.get_run(user_id=OTHER_USER_ID, run_id=created.run_id)
    assert get_error.value.error is errors.NotFound

    with pytest.raises(AppException) as resume_error:
        await service.resume_run(
            user_id=OTHER_USER_ID,
            run_id=created.run_id,
            request_key=RESUME_KEY,
        )
    assert resume_error.value.error is errors.NotFound


@pytest.mark.asyncio
async def test_create_run_freezes_session_context(db_session) -> None:
    """创建 run 时冻结有界会话上下文，恢复不再重新读取会话历史。"""
    llm_client = ScriptedLLMClient(
        [
            {"type": "final", "answer": FINAL_ANSWER},
            {"type": "final", "answer": FINAL_ANSWER},
        ]
    )
    service, _ = build_service(db_session, llm_client=llm_client)

    first = await service.create_run(
        user_id=TEST_USER_ID,
        entrypoint="order_support",
        question=QUESTION,
        session_id=None,
        max_steps=3,
        request_key=CREATE_KEY,
    )
    await service.resume_run(
        user_id=TEST_USER_ID, run_id=first.run_id, request_key=RESUME_KEY
    )

    second = await service.create_run(
        user_id=TEST_USER_ID,
        entrypoint="order_support",
        question="那退款要多久？",
        session_id=first.session_id,
        max_steps=3,
        request_key="create_key_exec_2",
    )
    await service.resume_run(
        user_id=TEST_USER_ID, run_id=second.run_id, request_key="resume_key_exec_2"
    )

    checkpoint_dao = AgentRunCheckpointDao(session_provider=db_session)
    row = await checkpoint_dao.fetch_first(
        checkpoint_dao.select_stmt().where(AgentRunCheckpoint.run_id == second.run_id)
    )
    context = row.payload["session_context"]
    assert context["session_id"] == first.session_id
    assert [item["role"] for item in context["recent_messages"]] == [
        "user",
        "assistant",
    ]


@pytest.mark.asyncio
async def test_order_builder_declares_replay_policies() -> None:
    """订单工具必须显式声明重放策略，未声明的一律按 non_replayable 处理。"""
    from pkg.agents import ToolReplayPolicy

    builder = OrderAgentBuilder(
        llm_client=ScriptedLLMClient([]),
        order_service=ScriptedOrderService(),
        rag_service=StubRagService(),
        user_id=TEST_USER_ID,
        max_steps=2,
    )
    tools = {tool.name: tool for tool in builder.build_tools()}

    assert (
        tools["prepare_invoice_request"].replay_policy
        is ToolReplayPolicy.NON_REPLAYABLE
    )
    assert tools["get_order_status"].replay_policy is ToolReplayPolicy.REPLAY_SAFE
    assert (
        tools["calculate_refund_amount"].replay_policy is ToolReplayPolicy.REPLAY_SAFE
    )
    assert tools["search_order_knowledge"].replay_policy is ToolReplayPolicy.REPLAY_SAFE
    assert tools["get_return_policy"].replay_policy is ToolReplayPolicy.REPLAY_SAFE


@pytest.mark.asyncio
async def test_cancel_converts_to_persisted_interrupt_intent(db_session) -> None:
    """取消与慢消费都转换成持久化打断意图，并带上可诊断原因。"""
    service, _ = build_service(
        db_session,
        llm_client=ScriptedLLMClient([{"type": "final", "answer": FINAL_ANSWER}]),
    )
    storage = build_storage(db_session)
    created = await service.create_run(
        user_id=TEST_USER_ID,
        entrypoint="order_support",
        question=QUESTION,
        session_id=None,
        max_steps=3,
        request_key=CREATE_KEY,
    )
    await storage.claim_managed_run(
        user_id=TEST_USER_ID,
        run_id=created.run_id,
        request_key=RESUME_KEY,
        request_digest="resume-digest",
        trace_id=None,
        lease_seconds=30,
        resolve_stale_status=lambda state: ("interrupted", None),
    )

    status = await service.request_cancel_interrupt(
        user_id=TEST_USER_ID, run_id=created.run_id
    )
    assert status == "interrupt_requested"

    state = await storage.load_managed_run(user_id=TEST_USER_ID, run_id=created.run_id)
    assert state.status == "interrupt_requested"
    assert state.interrupt_reason == "client_cancelled"

    # 重复调用返回当前状态；慢客户端写入不同原因。
    assert (
        await service.request_cancel_interrupt(
            user_id=TEST_USER_ID, run_id=created.run_id, slow_client=True
        )
        == "interrupt_requested"
    )
    state = await storage.load_managed_run(user_id=TEST_USER_ID, run_id=created.run_id)
    assert state.interrupt_reason == "client_cancelled"


@pytest.mark.asyncio
async def test_payment_entrypoint_uses_same_orchestration(db_session) -> None:
    """支付入口复用同一套 claim/checkpoint/恢复编排，不复制执行循环。"""
    llm_client = ScriptedLLMClient(
        [{"type": "final", "answer": "当前支持微信和支付宝。"}]
    )
    service, _ = build_service(db_session, llm_client=llm_client)

    created = await service.create_run(
        user_id=TEST_USER_ID,
        entrypoint="payment_support",
        question="支持哪些支付方式？",
        session_id=None,
        max_steps=3,
        request_key=CREATE_KEY,
    )
    view = await service.get_run(user_id=TEST_USER_ID, run_id=created.run_id)
    assert view.agent_name == "payment_support"
    assert view.route == "payment"
    assert view.phase == "before_action"

    resumed = await service.resume_run(
        user_id=TEST_USER_ID, run_id=created.run_id, request_key=RESUME_KEY
    )

    assert resumed.result.status == "completed"
    assert resumed.result.answer == "当前支持微信和支付宝。"
    assert resumed.run.agent_name == "payment_support"
    assert await count_messages(db_session, role="assistant") == 1


@pytest.mark.asyncio
async def test_interrupt_before_model_persists_paused_state(db_session, monkeypatch):
    storage = build_storage(db_session)
    llm = ScriptedLLMClient([])
    service, _ = build_service(db_session, llm_client=llm, storage=storage)
    created = await service.create_run(
        user_id=TEST_USER_ID,
        entrypoint="order_support",
        question=QUESTION,
        session_id=None,
        max_steps=3,
        request_key=CREATE_KEY,
    )
    original = service._claim

    async def claim_and_interrupt(**kwargs):
        claim = await original(**kwargs)
        await storage.request_run_interrupt(
            user_id=TEST_USER_ID, run_id=claim.run_id, reason="stop"
        )
        return claim

    monkeypatch.setattr(service, "_claim", claim_and_interrupt)
    result = await service.resume_run(
        user_id=TEST_USER_ID, run_id=created.run_id, request_key=RESUME_KEY
    )
    assert result.run.status == result.result.status == "interrupted"
    assert result.run.resumable
    assert llm.action_inputs == []


@pytest.mark.asyncio
async def test_interrupt_during_model_preserves_generated_action(db_session):
    storage = build_storage(db_session)
    holder = {}

    class InterruptingLLM(ScriptedLLMClient):
        async def response_structured(self, **kwargs):
            if not self.action_inputs:
                await storage.request_run_interrupt(
                    user_id=TEST_USER_ID, run_id=holder["run_id"], reason="stop"
                )
            return await super().response_structured(**kwargs)

    llm = InterruptingLLM(
        [
            {
                "type": "tool_call",
                "tool": "get_order_status",
                "args": {"order_id": "1001"},
            },
            {"type": "final", "answer": FINAL_ANSWER},
        ]
    )
    order = ScriptedOrderService()
    service, _ = build_service(
        db_session, llm_client=llm, order_service=order, storage=storage
    )
    created = await service.create_run(
        user_id=TEST_USER_ID,
        entrypoint="order_support",
        question=QUESTION,
        session_id=None,
        max_steps=3,
        request_key=CREATE_KEY,
    )
    holder["run_id"] = created.run_id
    paused = await service.resume_run(
        user_id=TEST_USER_ID, run_id=created.run_id, request_key=RESUME_KEY
    )
    assert paused.run.phase == "before_tool"
    assert order.calls == []
    result = await service.resume_run(
        user_id=TEST_USER_ID, run_id=created.run_id, request_key="resume_next_key"
    )
    assert result.run.status == "completed"
    assert order.calls == ["1001"]
    assert len(llm.action_inputs) == 2
    assert len(llm.action_inputs[1]["previous_steps"]) == 1


@pytest.mark.asyncio
async def test_interrupt_during_routing_preserves_route(db_session):
    """统一入口在路由模型调用期间被打断：已选 route 随暂停现场保存，恢复不再重新路由。"""
    from internal.agents.router import AgentRoute, AgentRouterActionModel

    storage = build_storage(db_session)
    holder: dict[str, str] = {}

    class InterruptingRouterLLM(ScriptedLLMClient):
        def __init__(self, actions):
            super().__init__(actions)
            self.router_calls = 0

        async def response_structured(self, *, input, response_model, **kwargs):
            if response_model is AgentRouterActionModel:
                self.router_calls += 1
                await storage.request_run_interrupt(
                    user_id=TEST_USER_ID, run_id=holder["run_id"], reason="stop"
                )
                return AgentRouterActionModel(route=AgentRoute.ORDER)
            return await super().response_structured(
                input=input, response_model=response_model, **kwargs
            )

    llm = InterruptingRouterLLM(
        [
            {
                "type": "tool_call",
                "tool": "get_order_status",
                "args": {"order_id": "1001"},
            },
            {"type": "final", "answer": FINAL_ANSWER},
        ]
    )
    order = ScriptedOrderService()
    service, _ = build_service(
        db_session, llm_client=llm, order_service=order, storage=storage
    )
    created = await service.create_run(
        user_id=TEST_USER_ID,
        entrypoint="chat",
        # 问题不含规则路由关键词，必须走 LLM Router。
        question="帮我看一下这个情况",
        session_id=None,
        max_steps=3,
        request_key=CREATE_KEY,
    )
    holder["run_id"] = created.run_id

    paused = await service.resume_run(
        user_id=TEST_USER_ID, run_id=created.run_id, request_key=RESUME_KEY
    )

    assert paused.run.status == "interrupted"
    assert paused.run.resumable is True
    assert paused.run.phase == "before_action"
    assert paused.run.route == "order"
    assert paused.run.agent_name == "order_support"
    assert llm.router_calls == 1
    assert order.calls == []

    checkpoint_dao = AgentRunCheckpointDao(session_provider=db_session)
    row = await checkpoint_dao.fetch_first(
        checkpoint_dao.select_stmt().where(AgentRunCheckpoint.run_id == created.run_id)
    )
    assert row.payload["route"] == "order"
    assert row.payload["agent_name"] == "order_support"
    assert row.payload["pending_action"] is None

    completed = await service.resume_run(
        user_id=TEST_USER_ID, run_id=created.run_id, request_key="resume_key_exec_2"
    )

    assert completed.run.status == "completed"
    assert completed.result.answer == FINAL_ANSWER
    assert llm.router_calls == 1
    assert order.calls == ["1001"]


@pytest.mark.asyncio
async def test_unavailable_logger_does_not_mask_provider_failure(
    db_session, monkeypatch
) -> None:
    """日志不可用时仍必须给出稳定错误码并落库终态。

    `except` 分支里的日志调用若自身抛异常，异常映射会被跳过：调用方拿到与业务无关的
    次生异常，run 也会停在 `interrupted` 而不是 `failed`。
    """
    from internal.services.agents import execution as execution_module

    class FailingLLM(ScriptedLLMClient):
        async def response_structured(self, **kwargs):
            raise RuntimeError("provider unavailable")

    class UnavailableLogger:
        """复现 `pkg.logger` 未初始化时的行为：任何一次日志调用都会抛异常。"""

        def warning(self, *args, **kwargs):
            raise RuntimeError("Logger not initialized. Call init_logger() first.")

    monkeypatch.setattr(execution_module, "logger", UnavailableLogger())
    service, _ = build_service(db_session, llm_client=FailingLLM([]))
    created = await service.create_run(
        user_id=TEST_USER_ID,
        entrypoint="order_support",
        question=QUESTION,
        session_id=None,
        max_steps=3,
        request_key=CREATE_KEY,
    )

    with pytest.raises(AppException) as excinfo:
        await service.resume_run(
            user_id=TEST_USER_ID, run_id=created.run_id, request_key=RESUME_KEY
        )
    assert excinfo.value.error is errors.ServiceUnavailable

    view = await service.get_run(user_id=TEST_USER_ID, run_id=created.run_id)
    assert view.status == "failed"
    assert view.resumable is False
    attempt_dao = AgentRunAttemptDao(session_provider=db_session)
    attempts = await attempt_dao.fetch_all(attempt_dao.select_stmt())
    assert [attempt.status for attempt in attempts] == ["failed"]


@pytest.mark.asyncio
async def test_provider_failure_persists_terminal_status(db_session):
    class FailingLLM(ScriptedLLMClient):
        async def response_structured(self, **kwargs):
            raise RuntimeError("provider unavailable")

    service, _ = build_service(db_session, llm_client=FailingLLM([]))
    created = await service.create_run(
        user_id=TEST_USER_ID,
        entrypoint="order_support",
        question=QUESTION,
        session_id=None,
        max_steps=3,
        request_key=CREATE_KEY,
    )
    with pytest.raises(AppException):
        await service.resume_run(
            user_id=TEST_USER_ID, run_id=created.run_id, request_key=RESUME_KEY
        )
    view = await service.get_run(user_id=TEST_USER_ID, run_id=created.run_id)
    assert view.status == "failed"
    assert view.ended_at is not None
    assert view.stop_reason


@pytest.mark.asyncio
@pytest.mark.parametrize("expire_grace", [False, True])
async def test_outer_scope_cancellation_has_bounded_safe_tool_cleanup(
    db_session, expire_grace
):
    import anyio
    from dataclasses import replace
    from time import monotonic

    started = anyio.Event()
    tool_cancelled = False

    async def slow_tool():
        nonlocal tool_cancelled
        started.set()
        try:
            await anyio.sleep(2 if expire_grace else 0.03)
        except anyio.get_cancelled_exc_class():
            tool_cancelled = True
            raise

    service, _ = build_service(
        db_session,
        llm_client=ScriptedLLMClient(
            [
                {
                    "type": "tool_call",
                    "tool": "get_order_status",
                    "args": {"order_id": "1001"},
                },
                {"type": "final", "answer": FINAL_ANSWER},
            ]
        ),
        order_service=ScriptedOrderService(on_first_call=slow_tool),
    )
    service._limits = replace(service._limits, cancel_grace_seconds=0.3)
    created = await service.create_run(
        user_id=TEST_USER_ID,
        entrypoint="order_support",
        question=QUESTION,
        session_id=None,
        max_steps=3,
        request_key=CREATE_KEY,
    )
    began = monotonic()
    with anyio.CancelScope() as request_scope:
        async with anyio.create_task_group() as tasks:

            async def cancel_after_tool_starts():
                await started.wait()
                request_scope.cancel()

            tasks.start_soon(cancel_after_tool_starts)
            await service.resume_run(
                user_id=TEST_USER_ID, run_id=created.run_id, request_key=RESUME_KEY
            )
    assert monotonic() - began < 1.5
    view = await service.get_run(user_id=TEST_USER_ID, run_id=created.run_id)
    assert tool_cancelled is expire_grace
    assert view.status == ("recovery_required" if expire_grace else "interrupted")
    assert view.completed_steps == (0 if expire_grace else 1)
