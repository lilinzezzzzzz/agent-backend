from datetime import datetime
from typing import Any

import pytest

from internal.services.agents.audit import (
    AgentAuditContext,
    AgentAuditService,
    AuditedAgentLLMClient,
    record_agent_audit,
)
from internal.schemas.agent import AgentRunResultDTO, AgentStepDTO
from pkg.agents import LLMActionModel


class FakeAgentAuditDao:
    def __init__(self):
        self.inserted: Any | None = None

    async def insert(self, instance: Any) -> None:
        self.inserted = instance


class FakeAgentAuditService:
    def __init__(self):
        self.records: list[dict[str, Any]] = []

    async def record_agent_run(self, **kwargs: Any) -> bool:
        self.records.append(kwargs)
        return True


class FakeBackgroundTaskManager:
    def __init__(self):
        self.tasks: list[dict[str, Any]] = []

    async def add_task(
        self,
        task_id: str,
        *,
        coro_func: Any,
        args_tuple: tuple[Any, ...] = (),
        kwargs_dict: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> bool:
        self.tasks.append(
            {
                "task_id": task_id,
                "coro_func": coro_func,
                "args_tuple": args_tuple,
                "kwargs_dict": kwargs_dict,
                "timeout": timeout,
            }
        )
        await coro_func(*args_tuple, **(kwargs_dict or {}))
        return True


class UninitializedBackgroundTaskManager:
    async def add_task(self, *args: Any, **kwargs: Any) -> bool:
        raise RuntimeError("Background task manager not initialized.")


class ShuttingDownBackgroundTaskManager:
    async def add_task(self, *args: Any, **kwargs: Any) -> bool:
        raise RuntimeError("AsyncTaskManagerAnyIO is shutting down.")


class FakeLLMClient:
    provider = "fake-provider"
    model = "fake-model"

    def __init__(self):
        self.calls: list[dict[str, Any]] = []

    async def chat_completion_structured(self, **kwargs: Any) -> LLMActionModel:
        self.calls.append(kwargs)
        return LLMActionModel(
            type="tool_call",
            tool="send_email",
            args={"email": "buyer@example.com", "order_id": "1001"},
        )


@pytest.mark.asyncio
async def test_agent_audit_service_redacts_sensitive_payloads() -> None:
    dao = FakeAgentAuditDao()
    service = AgentAuditService(audit_dao=dao)
    result = AgentRunResultDTO(
        run_id="run_audit_1",
        status="completed",
        answer="已发送到 buyer@example.com",
        steps=[
            AgentStepDTO(
                index=0,
                status="completed",
                action_type="tool_call",
                tool="prepare_invoice_request",
                args={
                    "order_id": "1001",
                    "email": "buyer@example.com",
                    "tax_no": "91310000SECRET",
                },
                action_result={
                    "ok": True,
                    "confirmation": {
                        "token": "confirmation-token",
                        "summary": "发送到 buyer@example.com",
                    },
                },
                elapsed_ms=1.2,
            )
        ],
    )

    saved = await service.record_agent_run(
        agent_name="order_support",
        user_id=999,
        trace_id="trace-audit",
        user_input="发票发送到 buyer@example.com，手机 13812345678",
        max_steps=3,
        result=result,
        started_at=datetime(2026, 1, 1, 0, 0, 0),
        ended_at=datetime(2026, 1, 1, 0, 0, 1),
        llm_calls=[
            {
                "request": {
                    "messages": [{"role": "user", "content": "buyer@example.com"}]
                },
                "raw_response": {"email": "buyer@example.com", "token": "llm-token"},
            }
        ],
        metadata={"confirmation_token": "confirmation-token"},
    )

    assert saved is True
    assert dao.inserted is not None
    assert dao.inserted.run_id == "run_audit_1"
    assert dao.inserted.agent_name == "order_support"
    assert dao.inserted.user_input == "发票发送到 bu....com，手机 13...5678"
    assert dao.inserted.final_answer == "已发送到 bu....com"
    assert dao.inserted.steps[0]["args"]["email"] == "bu....com"
    assert dao.inserted.steps[0]["args"]["tax_no"] == "91...CRET"
    assert (
        dao.inserted.steps[0]["action_result"]["confirmation"]["token"] == "[REDACTED]"
    )
    assert dao.inserted.llm_calls[0]["raw_response"]["email"] == "bu....com"
    assert dao.inserted.llm_calls[0]["raw_response"]["token"] == "[REDACTED]"
    assert dao.inserted.audit_metadata["confirmation_token"] == "co...oken"


@pytest.mark.asyncio
async def test_record_agent_audit_uses_background_task_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_manager = FakeBackgroundTaskManager()
    monkeypatch.setattr(
        "internal.services.agents.audit.background_task_manager", task_manager
    )
    audit_writer = FakeAgentAuditService()
    audit_context = AgentAuditContext.start(
        agent_name="order_support",
        user_id=999,
        user_input="查询订单",
        max_steps=3,
    )
    result = AgentRunResultDTO(
        run_id="run_async_audit_1",
        status="completed",
        answer="完成",
        steps=[],
        audit_metadata={"route": "order"},
    )

    saved = await record_agent_audit(
        audit_writer=audit_writer,
        audit_context=audit_context,
        result=result,
        metadata={"source": "test"},
    )

    assert saved is True
    assert len(task_manager.tasks) == 1
    assert (
        task_manager.tasks[0]["task_id"]
        == "agent_audit:order_support:run_async_audit_1"
    )
    assert task_manager.tasks[0]["timeout"] == 10
    assert len(audit_writer.records) == 1
    assert audit_writer.records[0]["agent_name"] == "order_support"
    assert audit_writer.records[0]["metadata"] == {"route": "order", "source": "test"}


@pytest.mark.asyncio
async def test_record_agent_audit_falls_back_when_background_task_manager_is_uninitialized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "internal.services.agents.audit.background_task_manager",
        UninitializedBackgroundTaskManager(),
    )
    audit_writer = FakeAgentAuditService()
    audit_context = AgentAuditContext.start(
        agent_name="order_support",
        user_id=999,
        user_input="查询订单",
        max_steps=3,
    )
    result = AgentRunResultDTO(
        run_id="run_sync_fallback_1",
        status="completed",
        answer="完成",
        steps=[],
    )

    saved = await record_agent_audit(
        audit_writer=audit_writer,
        audit_context=audit_context,
        result=result,
    )

    assert saved is True
    assert len(audit_writer.records) == 1
    assert audit_writer.records[0]["result"] == result


@pytest.mark.asyncio
async def test_record_agent_audit_does_not_write_inline_when_background_task_manager_is_shutting_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "internal.services.agents.audit.background_task_manager",
        ShuttingDownBackgroundTaskManager(),
    )
    audit_writer = FakeAgentAuditService()
    audit_context = AgentAuditContext.start(
        agent_name="order_support",
        user_id=999,
        user_input="查询订单",
        max_steps=3,
    )
    result = AgentRunResultDTO(
        run_id="run_shutdown_1",
        status="completed",
        answer="完成",
        steps=[],
    )

    saved = await record_agent_audit(
        audit_writer=audit_writer,
        audit_context=audit_context,
        result=result,
    )

    assert saved is False
    assert audit_writer.records == []


@pytest.mark.asyncio
async def test_audited_agent_llm_client_records_structured_calls() -> None:
    audit_context = AgentAuditContext.start(
        agent_name="order_support",
        user_id=999,
        user_input="订单 1001 到哪了？",
        max_steps=3,
    )
    llm_client = FakeLLMClient()
    audited_client = AuditedAgentLLMClient(
        llm_client=llm_client,
        audit_context=audit_context,
    )

    action = await audited_client.chat_completion_structured(
        messages=[{"role": "user", "content": "订单 1001 到哪了？"}],
        response_model=LLMActionModel,
        temperature=0,
        max_tokens=128,
        thinking=False,
    )

    assert action.tool == "send_email"
    assert len(audit_context.llm_calls) == 1
    call = audit_context.llm_calls[0]
    assert call["provider"] == "fake-provider"
    assert call["model"] == "fake-model"
    assert call["response_model"] == "LLMActionModel"
    assert call["request"]["max_tokens"] == 128
    assert call["request"]["extra"]["thinking"] is False
    assert call["parsed_response"]["args"]["email"] == "bu....com"
