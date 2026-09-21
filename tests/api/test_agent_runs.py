"""受管理 run API 的契约测试（依赖注入 fake Service，不访问数据库）。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from internal.controllers.api import agent as agent_controller
from internal.schemas.agent import (
    AgentRunCreateDTO,
    AgentRunEventContextDTO,
    AgentRunInterruptDTO,
    AgentRunResultDTO,
    AgentRunResumeDTO,
    AgentRunViewDTO,
    AgentStepDTO,
    AgentStreamEventDTO,
)
from internal.services.agents import new_agent_execution_service

TEST_USER_ID = UUID("00000000-0000-7000-8000-000000000999")
RUN_ID = "run_managed_api_1"
SESSION_ID = "session_managed_api_1"
STARTED_AT = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)


def build_view(*, status: str = "ready", resumable: bool = True) -> AgentRunViewDTO:
    return AgentRunViewDTO(
        run_id=RUN_ID,
        session_id=SESSION_ID,
        entrypoint="order_support",
        agent_name="order_support",
        status=status,
        phase="before_action",
        revision=1,
        attempt_no=1,
        completed_steps=0,
        max_steps=3,
        resumable=resumable,
        started_at=STARTED_AT,
        route="order",
        execution_version="v1",
        elapsed_ms=12.5,
    )


class FakeAgentExecutionService:
    """记录调用参数的受管理执行 Service 替身。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def create_run(self, **kwargs) -> AgentRunCreateDTO:
        self.calls.append(("create_run", kwargs))
        return AgentRunCreateDTO(
            run_id=RUN_ID,
            session_id=SESSION_ID,
            entrypoint=kwargs["entrypoint"],
            status="ready",
            max_steps=kwargs["max_steps"],
        )

    async def get_run(self, **kwargs) -> AgentRunViewDTO:
        self.calls.append(("get_run", kwargs))
        return build_view()

    async def interrupt_run(self, **kwargs) -> AgentRunInterruptDTO:
        self.calls.append(("interrupt_run", kwargs))
        return AgentRunInterruptDTO(
            run_id=kwargs["run_id"], status="interrupt_requested", accepted=True
        )

    async def resume_run(self, **kwargs) -> AgentRunResumeDTO:
        self.calls.append(("resume_run", kwargs))
        return AgentRunResumeDTO(
            run=build_view(status="completed", resumable=False),
            result=AgentRunResultDTO(
                run_id=RUN_ID,
                status="completed",
                answer="订单 1001 已发货。",
                steps=[
                    AgentStepDTO(
                        index=0,
                        status="completed",
                        action_type="final",
                        action_result=None,
                        elapsed_ms=1.0,
                    )
                ],
                session_id=SESSION_ID,
            ),
        )

    async def resume_run_stream(self, **kwargs):
        self.calls.append(("resume_run_stream", kwargs))
        context = AgentRunEventContextDTO(
            run_id=RUN_ID,
            session_id=SESSION_ID,
            attempt_no=2,
            checkpoint_revision=3,
            route="order",
        )
        yield AgentStreamEventDTO.run_resumed(
            run_id=RUN_ID, status="running", route="order"
        ).with_context(context)
        yield AgentStreamEventDTO.run_completed(
            result=AgentRunResultDTO(
                run_id=RUN_ID,
                status="completed",
                answer="订单 1001 已发货。",
                steps=[],
                session_id=SESSION_ID,
            ),
            route="order",
        ).with_context(context)


@pytest_asyncio.fixture
async def managed_run_client():
    app = FastAPI()
    app.include_router(agent_controller.router, prefix="/v1")
    service = FakeAgentExecutionService()
    app.dependency_overrides[new_agent_execution_service] = lambda: service

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client, service

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_create_run_endpoint_returns_ready_envelope(managed_run_client) -> None:
    client, service = managed_run_client

    response = await client.post(
        "/v1/agent/runs/create",
        json={
            "entrypoint": "order_support",
            "question": "订单 1001 到哪了？",
            "max_steps": 3,
            "request_key": "create_key_api_1",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "code": 20000,
        "message": "",
        "data": {
            "run_id": RUN_ID,
            "session_id": SESSION_ID,
            "entrypoint": "order_support",
            "status": "ready",
            "max_steps": 3,
        },
    }
    name, kwargs = service.calls[0]
    assert name == "create_run"
    assert kwargs["request_key"] == "create_key_api_1"
    assert kwargs["session_id"] is None


@pytest.mark.asyncio
async def test_create_run_rejects_confirmation_token(managed_run_client) -> None:
    client, service = managed_run_client

    response = await client.post(
        "/v1/agent/runs/create",
        json={
            "entrypoint": "order_support",
            "question": "订单 1001 到哪了？",
            "request_key": "create_key_api_1",
            "confirmation_token": "not-allowed",
        },
    )

    assert response.status_code == 422
    assert service.calls == []


@pytest.mark.asyncio
async def test_create_run_requires_request_key(managed_run_client) -> None:
    client, _ = managed_run_client

    response = await client.post(
        "/v1/agent/runs/create",
        json={"entrypoint": "order_support", "question": "订单 1001 到哪了？"},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_get_run_endpoint_returns_trimmed_view(managed_run_client) -> None:
    client, service = managed_run_client

    response = await client.get(f"/v1/agent/runs/{RUN_ID}")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["run_id"] == RUN_ID
    assert data["status"] == "ready"
    assert data["resumable"] is True
    assert data["completed_steps"] == 0
    # 不暴露原始 checkpoint。
    assert "checkpoint" not in data
    assert service.calls[0] == ("get_run", {"user_id": TEST_USER_ID, "run_id": RUN_ID})


@pytest.mark.asyncio
async def test_interrupt_endpoint_reports_accepted_status(managed_run_client) -> None:
    client, service = managed_run_client

    response = await client.post(
        f"/v1/agent/runs/{RUN_ID}/interrupt", json={"reason": "用户主动中止"}
    )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "run_id": RUN_ID,
        "status": "interrupt_requested",
        "accepted": True,
    }
    assert service.calls[0][1]["reason"] == "用户主动中止"


@pytest.mark.asyncio
async def test_interrupt_reason_is_length_bounded(managed_run_client) -> None:
    client, _ = managed_run_client

    response = await client.post(
        f"/v1/agent/runs/{RUN_ID}/interrupt", json={"reason": "x" * 201}
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_resume_endpoint_returns_run_and_result(managed_run_client) -> None:
    client, service = managed_run_client

    response = await client.post(
        f"/v1/agent/runs/{RUN_ID}/resume", json={"request_key": "resume_key_api_1"}
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["run"]["run_id"] == RUN_ID
    assert data["run"]["status"] == "completed"
    assert data["result"]["answer"] == "订单 1001 已发货。"
    assert data["result"]["steps"][0]["action_type"] == "final"
    assert service.calls[0][0] == "resume_run"


@pytest.mark.asyncio
async def test_resume_rejects_question_override(managed_run_client) -> None:
    """恢复不接受调用方替换原始问题或步数上限。"""
    client, service = managed_run_client

    response = await client.post(
        f"/v1/agent/runs/{RUN_ID}/resume",
        json={"request_key": "resume_key_api_1", "question": "换一个问题"},
    )

    assert response.status_code == 422
    assert service.calls == []


@pytest.mark.asyncio
async def test_resume_stream_endpoint_emits_sse_events(managed_run_client) -> None:
    client, service = managed_run_client

    response = await client.post(
        f"/v1/agent/runs/{RUN_ID}/resume/stream",
        json={"request_key": "resume_key_api_1"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    body = response.text
    assert "event: run_resumed" in body
    assert "event: run_completed" in body
    assert '"attempt_no":2' in body
    assert '"checkpoint_revision":3' in body
    assert service.calls[0][0] == "resume_run_stream"


@pytest.mark.asyncio
async def test_managed_run_paths_require_valid_path_id(managed_run_client) -> None:
    client, service = managed_run_client

    response = await client.get("/v1/agent/runs/" + "x" * 65)

    assert response.status_code == 422
    assert service.calls == []


def test_run_detail_schema_does_not_expose_checkpoint_fields() -> None:
    """公开视图只包含裁剪后的运行状态字段。"""
    from internal.schemas.agent import run_view_to_schema

    schema = run_view_to_schema(build_view())
    payload = json.loads(schema.model_dump_json())

    assert set(payload) == {
        "run_id",
        "session_id",
        "entrypoint",
        "agent_name",
        "status",
        "phase",
        "revision",
        "attempt_no",
        "completed_steps",
        "max_steps",
        "resumable",
        "execution_version",
        "route",
        "started_at",
        "ended_at",
        "elapsed_ms",
        "stop_reason",
        "answer",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("error_name", ["NotFound", "AgentRunStateConflict"])
async def test_stream_claim_failure_is_a_structured_sse_error(monkeypatch, error_name):
    from internal.core import AppException, errors
    from internal.services.agents.execution import AgentExecutionService

    error = getattr(errors, error_name)
    service = object.__new__(AgentExecutionService)

    async def reject(**kwargs):
        raise AppException(error, message="claim rejected")

    monkeypatch.setattr(service, "_claim", reject)
    response = agent_controller._managed_streaming_response(
        service.resume_run_stream(
            user_id=TEST_USER_ID, run_id=RUN_ID, request_key="resume_rejected"
        )
    )
    sent = []

    async def send(message):
        sent.append(message)

    async def receive():
        raise AssertionError("ASGI 2.4 response should not poll disconnect")

    await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
    assert sent[0]["status"] == 200
    body = b"".join(message.get("body", b"") for message in sent).decode()
    assert "event: error" in body
    assert str(error.code) in body
    assert sent[-1]["more_body"] is False
