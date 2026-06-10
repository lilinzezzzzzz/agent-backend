import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from internal.agents.order import ORDER_SUPPORT_SYSTEM_PROMPT, OrderAgentBuilder
from internal.agents.payment import PAYMENT_SUPPORT_SYSTEM_PROMPT, PaymentAgentBuilder
from internal.agents.router import AgentRoute, AgentRouterActionModel
from internal.core import AppException, errors
from internal.controllers.api import agent as agent_controller
from internal.schemas.agent import (
    AgentChatReqSchema,
    AgentOrderSupportReqSchema,
    AgentPaymentSupportReqSchema,
)
from internal.services.agents import (
    AgentRouterService,
    OrderAgentService,
    PaymentAgentService,
)
from internal.services.dto.agent import (
    AgentChatDTO,
    AgentRunResultDTO,
    AgentStepDTO,
    AgentStreamEventDTO,
    AgentStreamEventName,
)
from pkg.agents import LLMActionModel
from internal.services.dto.rag import RagEvidenceDTO, RagRetrievalDTO
from internal.services.order import OrderService
from pkg.vectors.contracts import RetrievalMode
from pkg.toolkit import context


class FakeOrderAgentService:
    async def answer_order_support_question(
        self,
        *,
        user_id: int,
        session_id: str | None = None,
        question: str,
        max_steps: int = 4,
        confirmation_token: str | None = None,
        idempotency_key: str | None = None,
    ) -> AgentRunResultDTO:
        assert user_id == 999
        assert session_id is None
        assert question == "订单 1001 到哪了？"
        assert max_steps == 3
        assert confirmation_token is None
        assert idempotency_key is None
        return AgentRunResultDTO(
            run_id="run_1",
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

    async def stream_order_support_question(
        self,
        *,
        user_id: int,
        session_id: str | None = None,
        question: str,
        max_steps: int = 4,
        confirmation_token: str | None = None,
        idempotency_key: str | None = None,
    ) -> AsyncIterator[AgentStreamEventDTO]:
        result = await self.answer_order_support_question(
            user_id=user_id,
            session_id=session_id,
            question=question,
            max_steps=max_steps,
            confirmation_token=confirmation_token,
            idempotency_key=idempotency_key,
        )
        yield AgentStreamEventDTO.run_started(run_id=result.run_id)
        for step in result.steps:
            yield AgentStreamEventDTO.step_completed(run_id=result.run_id, step=step)
        yield AgentStreamEventDTO.run_completed(result=result)


class FakeAgentRouterService:
    async def chat(
        self,
        *,
        user_id: int,
        session_id: str | None = None,
        question: str,
        max_steps: int = 4,
        confirmation_token: str | None = None,
        idempotency_key: str | None = None,
    ) -> AgentChatDTO:
        assert user_id == 999
        assert session_id is None
        assert question == "订单 1001 到哪了？"
        assert max_steps == 3
        assert confirmation_token is None
        assert idempotency_key is None
        result = await FakeOrderAgentService().answer_order_support_question(
            user_id=user_id,
            session_id=session_id,
            question=question,
            max_steps=max_steps,
        )
        return AgentChatDTO(route="order", result=result)

    async def stream_chat(
        self,
        *,
        user_id: int,
        session_id: str | None = None,
        question: str,
        max_steps: int = 4,
        confirmation_token: str | None = None,
        idempotency_key: str | None = None,
    ) -> AsyncIterator[AgentStreamEventDTO]:
        yield AgentStreamEventDTO.route(route="order")
        async for event in FakeOrderAgentService().stream_order_support_question(
            user_id=user_id,
            session_id=session_id,
            question=question,
            max_steps=max_steps,
            confirmation_token=confirmation_token,
            idempotency_key=idempotency_key,
        ):
            yield event.with_route("order")


class FakePaymentAgentService:
    async def answer_payment_support_question(
        self,
        *,
        user_id: int,
        session_id: str | None = None,
        question: str,
        max_steps: int = 4,
        confirmation_token: str | None = None,
        idempotency_key: str | None = None,
    ) -> AgentRunResultDTO:
        assert user_id == 999
        assert session_id is None
        assert question == "支付失败怎么办？"
        assert max_steps == 3
        assert confirmation_token is None
        assert idempotency_key is None
        return AgentRunResultDTO(
            run_id="run_payment_1",
            status="completed",
            answer="请检查余额、限额和支付渠道状态。",
            steps=[
                AgentStepDTO(
                    index=0,
                    status="completed",
                    action_type="tool_call",
                    tool="search_payment_knowledge",
                    args={"query": "支付失败怎么办？"},
                    action_result={"ok": True, "total": 1},
                    elapsed_ms=1.1,
                )
            ],
        )

    async def stream_payment_support_question(
        self,
        *,
        user_id: int,
        session_id: str | None = None,
        question: str,
        max_steps: int = 4,
        confirmation_token: str | None = None,
        idempotency_key: str | None = None,
    ) -> AsyncIterator[AgentStreamEventDTO]:
        result = await self.answer_payment_support_question(
            user_id=user_id,
            session_id=session_id,
            question=question,
            max_steps=max_steps,
            confirmation_token=confirmation_token,
            idempotency_key=idempotency_key,
        )
        yield AgentStreamEventDTO.run_started(run_id=result.run_id)
        for step in result.steps:
            yield AgentStreamEventDTO.step_completed(run_id=result.run_id, step=step)
        yield AgentStreamEventDTO.run_completed(result=result)


class RecordingOrderAgentService:
    def __init__(self):
        self.calls: list[dict[str, Any]] = []

    async def answer_order_support_question(self, **kwargs: Any) -> AgentRunResultDTO:
        self.calls.append(kwargs)
        return AgentRunResultDTO.final(answer="订单 Agent 已处理。")

    async def stream_order_support_question(
        self, **kwargs: Any
    ) -> AsyncIterator[AgentStreamEventDTO]:
        result = await self.answer_order_support_question(**kwargs)
        yield AgentStreamEventDTO.run_completed(result=result)


class RecordingPaymentAgentService:
    def __init__(self):
        self.calls: list[dict[str, Any]] = []

    async def answer_payment_support_question(self, **kwargs: Any) -> AgentRunResultDTO:
        self.calls.append(kwargs)
        return AgentRunResultDTO.final(answer="支付 Agent 已处理。")

    async def stream_payment_support_question(
        self, **kwargs: Any
    ) -> AsyncIterator[AgentStreamEventDTO]:
        result = await self.answer_payment_support_question(**kwargs)
        yield AgentStreamEventDTO.run_completed(result=result)


class FakeLLMClient:
    def __init__(self, actions: list[Any]):
        self._actions = actions
        self.calls: list[dict[str, Any]] = []

    async def chat_completion_structured(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self._actions.pop(0)


class FakeAgentActionStore:
    def __init__(self):
        self.pending: dict[str, dict[str, object]] = {}
        self.confirmation_results: dict[str, dict[str, object]] = {}
        self.idempotency_results: dict[tuple[int, str], dict[str, object]] = {}

    async def save_pending_action(
        self, *, token: str, payload: dict[str, object], expires_in_seconds: int
    ) -> bool:
        assert expires_in_seconds == 300
        self.pending[token] = payload
        return True

    async def get_pending_action(self, *, token: str) -> dict[str, object] | None:
        return self.pending.get(token)

    async def delete_pending_action(self, *, token: str) -> int:
        return int(self.pending.pop(token, None) is not None)

    async def save_confirmation_result(
        self, *, token: str, payload: dict[str, object], expires_in_seconds: int
    ) -> bool:
        assert expires_in_seconds == 86400
        self.confirmation_results[token] = payload
        return True

    async def get_confirmation_result(self, *, token: str) -> dict[str, object] | None:
        return self.confirmation_results.get(token)

    async def save_idempotency_result(
        self,
        *,
        user_id: int,
        idempotency_key: str,
        payload: dict[str, object],
        expires_in_seconds: int,
    ) -> bool:
        assert expires_in_seconds == 86400
        self.idempotency_results[(user_id, idempotency_key)] = payload
        return True

    async def get_idempotency_result(
        self, *, user_id: int, idempotency_key: str
    ) -> dict[str, object] | None:
        return self.idempotency_results.get((user_id, idempotency_key))

    async def acquire_confirmation_lock(self, *, token: str) -> str:
        return f"lock:{token}"

    async def release_confirmation_lock(self, *, token: str, identifier: str) -> bool:
        assert identifier == f"lock:{token}"
        return True


class FakeAgentAuditService:
    def __init__(self):
        self.records: list[dict[str, Any]] = []

    async def record_agent_run(self, **kwargs: Any) -> bool:
        self.records.append(kwargs)
        return True


class FakeRagService:
    def __init__(self):
        self.calls: list[dict[str, Any]] = []

    async def retrieve(self, **kwargs: Any) -> RagRetrievalDTO:
        self.calls.append(kwargs)
        requested_domain = kwargs.get("requested_domain")
        title = (
            "付款失败常见原因"
            if requested_domain == "payment"
            else "电子发票开具与通知"
        )
        source_uri = (
            "payment/help/failed"
            if requested_domain == "payment"
            else "order/help/invoice"
        )
        content = (
            "付款失败通常与余额不足、银行卡限额、渠道风控、网络超时或收银台订单过期有关。"
            if requested_domain == "payment"
            else "开票申请提交后通常会在 1-3 个工作日内完成，完成后会通知用户。"
        )
        return RagRetrievalDTO(
            run_id="rag_run_test",
            trace_id="trace_test",
            query=kwargs["query"],
            requested_domain=requested_domain,
            requested_kb_ids=kwargs.get("requested_kb_ids"),
            effective_domains=[requested_domain],
            effective_kb_ids=[2],
            retrieval_mode=RetrievalMode.HYBRID,
            evidence=[
                RagEvidenceDTO(
                    evidence_id="ev_001",
                    chunk_id=1001,
                    doc_id=10,
                    kb_id=2,
                    title=title,
                    source_uri=source_uri,
                    document_version="v1",
                    text_hash="sha256:test",
                    content_excerpt=content,
                    score=0.91,
                    retrieval_mode="hybrid",
                )
            ],
        )


def _new_order_service(
    action_store: FakeAgentActionStore | None = None,
) -> OrderService:
    return OrderService(
        action_store=action_store or FakeAgentActionStore(),
        confirmation_seconds=300,
        idempotency_seconds=86400,
    )


def _new_order_agent_service(
    llm_client: FakeLLMClient, *, action_store: FakeAgentActionStore | None = None
) -> OrderAgentService:
    return OrderAgentService(
        llm_client=llm_client,
        order_service=_new_order_service(action_store),
        rag_service=FakeRagService(),
        audit_service=FakeAgentAuditService(),
    )


def _new_payment_agent_service(llm_client: FakeLLMClient) -> PaymentAgentService:
    return PaymentAgentService(
        llm_client=llm_client,
        rag_service=FakeRagService(),
        audit_service=FakeAgentAuditService(),
    )


def _response_payload(response) -> dict[str, Any]:
    return response.model_dump(mode="json")


def _parse_sse_events(body: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for block in body.strip().split("\n\n"):
        event_name: str | None = None
        data: dict[str, Any] | None = None
        for line in block.splitlines():
            if line.startswith("event: "):
                event_name = line.removeprefix("event: ")
            elif line.startswith("data: "):
                data = json.loads(line.removeprefix("data: "))
        if event_name is not None and data is not None:
            events.append({"event": event_name, "data": data})
    return events


def test_order_agent_system_prompt_names_all_registered_tools() -> None:
    builder = OrderAgentBuilder(
        llm_client=FakeLLMClient([]),
        order_service=_new_order_service(),
        rag_service=FakeRagService(),
        user_id=999,
        max_steps=3,
    )
    registered_tools = builder.build_tools()

    assert [tool.name for tool in registered_tools] == [
        "get_order_status",
        "get_return_policy",
        "search_order_knowledge",
        "calculate_refund_amount",
        "prepare_invoice_request",
    ]
    for tool in registered_tools:
        assert f"`{tool.name}`" in ORDER_SUPPORT_SYSTEM_PROMPT


def test_order_agent_system_prompt_defines_when_tools_are_optional() -> None:
    prompt = ORDER_SUPPORT_SYSTEM_PROMPT

    assert "必须先调用对应工具" in prompt
    assert "才可以不调用工具并直接返回 final" in prompt
    assert "每轮只能返回一个动作" in prompt
    assert "缺少工具必填参数时" in prompt
    assert "仅当用户明确要求提交开票申请时" in prompt
    assert "不得伪造成功结果" in prompt


def test_payment_agent_system_prompt_names_all_registered_tools() -> None:
    builder = PaymentAgentBuilder(
        llm_client=FakeLLMClient([]),
        rag_service=FakeRagService(),
        user_id=999,
        max_steps=3,
    )
    registered_tools = builder.build_tools()

    assert [tool.name for tool in registered_tools] == [
        "get_supported_payment_methods",
        "search_payment_knowledge",
        "calculate_payment_total",
    ]
    for tool in registered_tools:
        assert f"`{tool.name}`" in PAYMENT_SUPPORT_SYSTEM_PROMPT


def test_payment_agent_system_prompt_rejects_real_payment_side_effects() -> None:
    prompt = PAYMENT_SUPPORT_SYSTEM_PROMPT

    assert "必须先调用对应工具" in prompt
    assert "当前不支持发起支付" in prompt
    assert "不能自行计算" in prompt
    assert "不要声称已经发起、提交或完成任何支付动作" in prompt


def test_agent_request_requires_confirmation_token_and_idempotency_key_together() -> (
    None
):
    with pytest.raises(ValidationError):
        AgentChatReqSchema(question="确认提交", confirmation_token="token-only")


@pytest_asyncio.fixture
async def agent_client():
    app = FastAPI()
    app.include_router(agent_controller.router, prefix="/v1")
    app.dependency_overrides[agent_controller.new_order_agent_service] = lambda: (
        FakeOrderAgentService()
    )
    app.dependency_overrides[agent_controller.new_agent_router_service] = lambda: (
        FakeAgentRouterService()
    )
    app.dependency_overrides[agent_controller.new_payment_agent_service] = lambda: (
        FakePaymentAgentService()
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_order_support_agent_endpoint(agent_client) -> None:
    response = await agent_client.post(
        "/v1/agent/order/support",
        json={"question": "订单 1001 到哪了？", "max_steps": 3},
    )

    assert response.status_code == 200
    assert response.json() == {
        "code": 20000,
        "message": "",
        "data": {
            "session_id": "",
            "run_id": "run_1",
            "status": "completed",
            "answer": "订单 1001 正在运输中。",
            "steps": [
                {
                    "index": 0,
                    "status": "completed",
                    "action_type": "tool_call",
                    "tool": "get_order_status",
                    "args": {"order_id": "1001"},
                    "action_result": {"ok": True, "status": "运输中"},
                    "error": None,
                    "elapsed_ms": 1.2,
                }
            ],
            "confirmation": None,
        },
    }


@pytest.mark.asyncio
async def test_agent_chat_endpoint_routes_to_order_agent(agent_client) -> None:
    response = await agent_client.post(
        "/v1/agent/chat",
        json={"question": "订单 1001 到哪了？", "max_steps": 3},
    )

    assert response.status_code == 200
    assert response.json()["data"]["route"] == "order"
    assert response.json()["data"]["result"]["answer"] == "订单 1001 正在运输中。"


@pytest.mark.asyncio
async def test_agent_chat_stream_endpoint_routes_to_order_agent(agent_client) -> None:
    response = await agent_client.post(
        "/v1/agent/chat/stream",
        json={"question": "订单 1001 到哪了？", "max_steps": 3},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse_events(response.text)

    assert [event["event"] for event in events] == [
        AgentStreamEventName.ROUTE.value,
        AgentStreamEventName.RUN_STARTED.value,
        AgentStreamEventName.STEP_COMPLETED.value,
        AgentStreamEventName.RUN_COMPLETED.value,
    ]
    assert events[0]["data"]["route"] == "order"
    assert events[1]["data"]["route"] == "order"
    assert events[2]["data"]["step"]["tool"] == "get_order_status"
    assert events[3]["data"]["result"]["answer"] == "订单 1001 正在运输中。"


@pytest.mark.asyncio
async def test_payment_support_agent_endpoint(agent_client) -> None:
    response = await agent_client.post(
        "/v1/agent/payment/support",
        json={"question": "支付失败怎么办？", "max_steps": 3},
    )

    assert response.status_code == 200
    assert response.json()["data"]["run_id"] == "run_payment_1"
    assert response.json()["data"]["answer"] == "请检查余额、限额和支付渠道状态。"
    assert response.json()["data"]["steps"][0]["tool"] == "search_payment_knowledge"


@pytest.mark.asyncio
async def test_order_support_agent_stream_endpoint(agent_client) -> None:
    response = await agent_client.post(
        "/v1/agent/order/support/stream",
        json={"question": "订单 1001 到哪了？", "max_steps": 3},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse_events(response.text)

    assert [event["event"] for event in events] == [
        AgentStreamEventName.RUN_STARTED.value,
        AgentStreamEventName.STEP_COMPLETED.value,
        AgentStreamEventName.RUN_COMPLETED.value,
    ]
    assert events[0]["data"]["run_id"] == "run_1"
    assert events[1]["data"]["step"]["action_result"]["status"] == "运输中"
    assert events[2]["data"]["result"]["confirmation"] is None


@pytest.mark.asyncio
async def test_payment_support_agent_stream_endpoint(agent_client) -> None:
    response = await agent_client.post(
        "/v1/agent/payment/support/stream",
        json={"question": "支付失败怎么办？", "max_steps": 3},
    )

    assert response.status_code == 200
    events = _parse_sse_events(response.text)

    assert events[0]["event"] == AgentStreamEventName.RUN_STARTED.value
    assert events[1]["data"]["step"]["tool"] == "search_payment_knowledge"
    assert events[2]["data"]["result"]["answer"] == "请检查余额、限额和支付渠道状态。"


@pytest.mark.asyncio
async def test_agent_chat_controller_wraps_router_dto() -> None:
    response = await agent_controller.agent_chat(
        AgentChatReqSchema(question="订单 1001 到哪了？", max_steps=3),
        FakeAgentRouterService(),
    )

    assert _response_payload(response)["data"]["route"] == "order"
    assert (
        _response_payload(response)["data"]["result"]["answer"]
        == "订单 1001 正在运输中。"
    )


@pytest.mark.asyncio
async def test_agent_router_service_routes_order_question_without_llm() -> None:
    llm_client = FakeLLMClient([])
    order_agent_service = RecordingOrderAgentService()
    payment_agent_service = RecordingPaymentAgentService()
    service = AgentRouterService(
        llm_client=llm_client,
        order_agent_service=order_agent_service,
        payment_agent_service=payment_agent_service,
        audit_service=FakeAgentAuditService(),
        action_store=FakeAgentActionStore(),
    )

    result = await service.chat(user_id=999, question="订单 1001 到哪了？", max_steps=3)

    assert result.route == "order"
    assert result.result.answer == "订单 Agent 已处理。"
    assert order_agent_service.calls[0]["user_id"] == 999
    assert payment_agent_service.calls == []
    assert llm_client.calls == []


@pytest.mark.asyncio
async def test_agent_router_service_streams_route_and_agent_events() -> None:
    llm_client = FakeLLMClient([])
    order_agent_service = RecordingOrderAgentService()
    payment_agent_service = RecordingPaymentAgentService()
    service = AgentRouterService(
        llm_client=llm_client,
        order_agent_service=order_agent_service,
        payment_agent_service=payment_agent_service,
        audit_service=FakeAgentAuditService(),
        action_store=FakeAgentActionStore(),
    )

    events = [
        event
        async for event in service.stream_chat(
            user_id=999,
            question="订单 1001 到哪了？",
            max_steps=3,
        )
    ]

    assert events[0].event is AgentStreamEventName.ROUTE
    assert events[0].data["route"] == "order"
    assert events[1].event is AgentStreamEventName.RUN_COMPLETED
    assert events[1].data["route"] == "order"
    assert events[1].result is not None
    assert events[1].result.answer == "订单 Agent 已处理。"
    assert order_agent_service.calls[0]["user_id"] == 999
    assert payment_agent_service.calls == []


@pytest.mark.asyncio
async def test_agent_router_service_falls_back_to_llm_for_ambiguous_question() -> None:
    llm_client = FakeLLMClient([AgentRouterActionModel(route=AgentRoute.ORDER)])
    order_agent_service = RecordingOrderAgentService()
    payment_agent_service = RecordingPaymentAgentService()
    service = AgentRouterService(
        llm_client=llm_client,
        order_agent_service=order_agent_service,
        payment_agent_service=payment_agent_service,
        audit_service=FakeAgentAuditService(),
        action_store=FakeAgentActionStore(),
    )

    result = await service.chat(user_id=999, question="我买的东西怎么还没到")

    assert result.route == "order"
    assert result.result.answer == "订单 Agent 已处理。"
    assert len(llm_client.calls) == 1
    assert payment_agent_service.calls == []


@pytest.mark.asyncio
async def test_agent_router_service_routes_payment_question_without_llm() -> None:
    llm_client = FakeLLMClient([])
    order_agent_service = RecordingOrderAgentService()
    payment_agent_service = RecordingPaymentAgentService()
    service = AgentRouterService(
        llm_client=llm_client,
        order_agent_service=order_agent_service,
        payment_agent_service=payment_agent_service,
        audit_service=FakeAgentAuditService(),
        action_store=FakeAgentActionStore(),
    )

    result = await service.chat(user_id=999, question="支付失败怎么办？", max_steps=3)

    assert result.route == "payment"
    assert result.result.answer == "支付 Agent 已处理。"
    assert order_agent_service.calls == []
    assert payment_agent_service.calls[0]["user_id"] == 999
    assert llm_client.calls == []


@pytest.mark.asyncio
async def test_agent_router_service_returns_unsupported_without_calling_agents() -> (
    None
):
    llm_client = FakeLLMClient([])
    order_agent_service = RecordingOrderAgentService()
    payment_agent_service = RecordingPaymentAgentService()
    service = AgentRouterService(
        llm_client=llm_client,
        order_agent_service=order_agent_service,
        payment_agent_service=payment_agent_service,
        audit_service=FakeAgentAuditService(),
        action_store=FakeAgentActionStore(),
    )

    result = await service.chat(user_id=999, question="帮我写一首诗")

    assert result.route == "unsupported"
    assert "当前仅支持订单" in result.result.answer
    assert order_agent_service.calls == []
    assert payment_agent_service.calls == []
    assert llm_client.calls == []


@pytest.mark.asyncio
async def test_agent_router_service_bypasses_llm_for_confirmation() -> None:
    llm_client = FakeLLMClient([])
    order_agent_service = RecordingOrderAgentService()
    payment_agent_service = RecordingPaymentAgentService()
    action_store = FakeAgentActionStore()
    action_store.pending["confirmation-token"] = {
        "route": "order",
        "action": "submit_invoice_request",
        "user_id": 999,
    }
    service = AgentRouterService(
        llm_client=llm_client,
        order_agent_service=order_agent_service,
        payment_agent_service=payment_agent_service,
        audit_service=FakeAgentAuditService(),
        action_store=action_store,
    )

    result = await service.chat(
        user_id=999,
        question="确认提交",
        confirmation_token="confirmation-token",
        idempotency_key="invoice-request-1001",
    )

    assert result.route == "order"
    assert llm_client.calls == []
    assert order_agent_service.calls[0]["confirmation_token"] == "confirmation-token"
    assert payment_agent_service.calls == []


@pytest.mark.asyncio
async def test_agent_router_service_routes_confirmation_by_pending_action_route() -> None:
    llm_client = FakeLLMClient([])
    order_agent_service = RecordingOrderAgentService()
    payment_agent_service = RecordingPaymentAgentService()
    action_store = FakeAgentActionStore()
    action_store.pending["payment-confirmation-token"] = {
        "route": "payment",
        "action": "submit_payment",
        "user_id": 999,
    }
    service = AgentRouterService(
        llm_client=llm_client,
        order_agent_service=order_agent_service,
        payment_agent_service=payment_agent_service,
        audit_service=FakeAgentAuditService(),
        action_store=action_store,
    )

    result = await service.chat(
        user_id=999,
        question="确认支付",
        confirmation_token="payment-confirmation-token",
        idempotency_key="payment-request-1001",
    )

    assert result.route == "payment"
    assert llm_client.calls == []
    assert order_agent_service.calls == []
    assert payment_agent_service.calls[0]["confirmation_token"] == "payment-confirmation-token"


@pytest.mark.asyncio
async def test_order_support_agent_controller_wraps_service_dto() -> None:
    response = await agent_controller.order_support_agent(
        AgentOrderSupportReqSchema(question="订单 1001 到哪了？", max_steps=3),
        FakeOrderAgentService(),
    )

    assert _response_payload(response)["data"]["answer"] == "订单 1001 正在运输中。"
    assert _response_payload(response)["data"]["steps"][0]["tool"] == "get_order_status"


@pytest.mark.asyncio
async def test_payment_support_agent_controller_wraps_service_dto() -> None:
    response = await agent_controller.payment_support_agent(
        AgentPaymentSupportReqSchema(question="支付失败怎么办？", max_steps=3),
        FakePaymentAgentService(),
    )

    assert (
        _response_payload(response)["data"]["answer"]
        == "请检查余额、限额和支付渠道状态。"
    )
    assert (
        _response_payload(response)["data"]["steps"][0]["tool"]
        == "search_payment_knowledge"
    )


@pytest.mark.asyncio
async def test_order_agent_service_runs_react_with_tools() -> None:
    llm_client = FakeLLMClient(
        actions=[
            LLMActionModel(
                type="tool_call", tool="get_order_status", args={"order_id": "1001"}
            ),
            LLMActionModel(
                type="final", answer="订单 1001 由顺丰速运承运，预计明天 18:00 前送达。"
            ),
        ]
    )
    service = _new_order_agent_service(llm_client)

    result = await service.answer_order_support_question(
        user_id=999, question="订单 1001 到哪了？", max_steps=3
    )

    assert result.status == "completed"
    assert result.answer == "订单 1001 由顺丰速运承运，预计明天 18:00 前送达。"
    assert len(result.steps) == 2
    assert result.steps[0].tool == "get_order_status"
    assert result.steps[0].action_result == {
        "ok": True,
        "order_id": "1001",
        "status": "运输中",
        "carrier": "顺丰速运",
        "tracking_no": "SF10010001",
        "eta": "明天 18:00 前",
    }
    assert len(llm_client.calls) == 2
    assert llm_client.calls[0]["response_model"] is LLMActionModel


@pytest.mark.asyncio
async def test_order_agent_service_streams_react_events() -> None:
    llm_client = FakeLLMClient(
        actions=[
            LLMActionModel(
                type="tool_call", tool="get_order_status", args={"order_id": "1001"}
            ),
            LLMActionModel(
                type="final", answer="订单 1001 由顺丰速运承运，预计明天 18:00 前送达。"
            ),
        ]
    )
    service = _new_order_agent_service(llm_client)

    events = [
        event
        async for event in service.stream_order_support_question(
            user_id=999,
            question="订单 1001 到哪了？",
            max_steps=3,
        )
    ]

    assert [event.event for event in events] == [
        AgentStreamEventName.RUN_STARTED,
        AgentStreamEventName.STEP_COMPLETED,
        AgentStreamEventName.STEP_COMPLETED,
        AgentStreamEventName.RUN_COMPLETED,
    ]
    assert events[1].data["step"]["tool"] == "get_order_status"
    assert events[-1].result is not None
    assert (
        events[-1].result.answer == "订单 1001 由顺丰速运承运，预计明天 18:00 前送达。"
    )


@pytest.mark.asyncio
async def test_payment_agent_service_runs_react_with_tools() -> None:
    llm_client = FakeLLMClient(
        actions=[
            LLMActionModel(
                type="tool_call",
                tool="search_payment_knowledge",
                args={"query": "支付失败怎么办？", "top_k": 2},
            ),
            LLMActionModel(
                type="final", answer="请检查余额、银行卡限额、渠道状态和订单是否过期。"
            ),
        ]
    )
    service = _new_payment_agent_service(llm_client)

    result = await service.answer_payment_support_question(
        user_id=999, question="支付失败怎么办？", max_steps=3
    )

    action_result = result.steps[0].action_result
    assert result.status == "completed"
    assert result.answer == "请检查余额、银行卡限额、渠道状态和订单是否过期。"
    assert result.steps[0].tool == "search_payment_knowledge"
    assert action_result["ok"] is True
    assert action_result["query"] == "支付失败怎么办？"
    assert action_result["total"] == 1
    assert action_result["matches"][0]["evidence_id"] == "ev_001"
    assert action_result["matches"][0]["chunk_id"] == 1001
    assert action_result["matches"][0]["title"] == "付款失败常见原因"
    assert len(llm_client.calls) == 2
    assert llm_client.calls[0]["response_model"] is LLMActionModel


@pytest.mark.asyncio
async def test_payment_agent_service_calculates_payment_total() -> None:
    llm_client = FakeLLMClient(
        actions=[
            LLMActionModel(
                type="tool_call",
                tool="calculate_payment_total",
                args={
                    "item_amount_cents": 12990,
                    "shipping_fee_cents": 1200,
                    "discount_cents": 3000,
                },
            ),
            LLMActionModel(type="final", answer="应付金额为 111.90 元。"),
        ]
    )
    service = _new_payment_agent_service(llm_client)

    result = await service.answer_payment_support_question(
        user_id=999,
        question="商品 129.90 元，加 12 元运费，减 30 元优惠，需要付多少？",
        max_steps=3,
    )

    assert result.steps[0].action_result == {
        "ok": True,
        "item_amount_cents": 12990,
        "shipping_fee_cents": 1200,
        "discount_cents": 3000,
        "payable_amount_cents": 11190,
        "payable_amount_yuan": "111.90",
    }


@pytest.mark.asyncio
async def test_payment_agent_service_rejects_confirmation_token() -> None:
    service = _new_payment_agent_service(FakeLLMClient([]))

    with pytest.raises(AppException) as exc_info:
        await service.answer_payment_support_question(
            user_id=999,
            question="确认支付",
            confirmation_token="confirmation-token",
            idempotency_key="payment-request-1001",
        )

    assert exc_info.value.error == errors.BadRequest


@pytest.mark.asyncio
async def test_order_agent_service_records_unknown_order_action_result() -> None:
    llm_client = FakeLLMClient(
        actions=[
            LLMActionModel(
                type="tool_call", tool="get_order_status", args={"order_id": "404"}
            ),
            LLMActionModel(type="final", answer="没有查询到订单 404。"),
        ]
    )
    service = _new_order_agent_service(llm_client)

    result = await service.answer_order_support_question(
        user_id=999, question="订单 404 到哪了？", max_steps=3
    )

    assert result.status == "completed"
    assert result.steps[0].action_result == {
        "ok": False,
        "error": "not_found",
        "order_id": "404",
    }


@pytest.mark.asyncio
async def test_order_agent_service_hides_order_owned_by_another_user() -> None:
    llm_client = FakeLLMClient(
        actions=[
            LLMActionModel(
                type="tool_call", tool="get_order_status", args={"order_id": "1001"}
            ),
            LLMActionModel(type="final", answer="没有查询到订单 1001。"),
        ]
    )
    service = _new_order_agent_service(llm_client)

    result = await service.answer_order_support_question(
        user_id=1000, question="订单 1001 到哪了？", max_steps=3
    )

    assert result.steps[0].action_result == {
        "ok": False,
        "error": "not_found",
        "order_id": "1001",
    }


@pytest.mark.asyncio
async def test_order_agent_service_requires_confirmation_before_invoice_request(
    monkeypatch,
) -> None:
    action_store = FakeAgentActionStore()
    llm_client = FakeLLMClient(
        actions=[
            LLMActionModel(
                type="tool_call",
                tool="prepare_invoice_request",
                args={
                    "order_id": "1001",
                    "invoice_title": "个人",
                    "email": "buyer@example.com",
                },
            ),
            LLMActionModel(type="final", answer="请确认是否提交该开票申请。"),
        ]
    )
    service = _new_order_agent_service(llm_client, action_store=action_store)
    monkeypatch.setattr(context, "get_trace_id", lambda: "trace_invoice_test")

    prepared = await service.answer_order_support_question(
        user_id=999,
        question="帮我给订单 1001 开发票",
        max_steps=3,
    )

    assert prepared.status == "completed"
    assert prepared.answer == "请确认是否提交该开票申请。"
    assert prepared.steps[0].tool == "prepare_invoice_request"
    assert prepared.steps[0].action_result["status"] == "confirmation_required"
    assert prepared.confirmation is not None
    assert prepared.confirmation.action == "submit_invoice_request"
    assert len(action_store.pending) == 1
    pending_action = action_store.pending[prepared.confirmation.token]
    assert pending_action["route"] == "order"
    assert pending_action["action"] == "submit_invoice_request"

    confirmed = await service.answer_order_support_question(
        user_id=999,
        question="确认提交",
        confirmation_token=prepared.confirmation.token,
        idempotency_key="invoice-request-1001",
    )
    replayed = await service.answer_order_support_question(
        user_id=999,
        question="确认提交",
        confirmation_token=prepared.confirmation.token,
        idempotency_key="invoice-request-1001",
    )

    assert confirmed.answer == "开票申请已提交，开具完成后会通知用户"
    assert confirmed.steps[0].tool == "confirm_invoice_request"
    assert confirmed.steps[0].action_result["order_id"] == "1001"
    assert confirmed.steps[0].action_result["invoice_title"] == "个人"
    assert confirmed.steps[0].action_result["email"] == "buyer@example.com"
    assert confirmed.steps[0].action_result["status"] == "queued"
    assert confirmed.steps[0].action_result["trace_id"] == "trace_invoice_test"
    assert confirmed.steps[0].action_result["task_id"].startswith("task_")
    assert (
        replayed.steps[0].action_result["task_id"]
        == confirmed.steps[0].action_result["task_id"]
    )
    assert action_store.pending == {}

    with pytest.raises(AppException) as exc_info:
        await service.answer_order_support_question(
            user_id=999,
            question="再次确认提交",
            confirmation_token=prepared.confirmation.token,
            idempotency_key="different-idempotency-key",
        )

    assert exc_info.value.error == errors.BadRequest


@pytest.mark.asyncio
async def test_order_service_rejects_confirmation_from_another_user() -> None:
    action_store = FakeAgentActionStore()
    order_service = _new_order_service(action_store)
    confirmation = await order_service.prepare_invoice_request(
        user_id=999,
        order_id="1001",
        invoice_title="个人",
        tax_no=None,
        email=None,
    )

    with pytest.raises(AppException) as exc_info:
        await order_service.confirm_invoice_request(
            user_id=1000,
            confirmation_token=confirmation.token,
            idempotency_key="another-user-key",
        )

    assert exc_info.value.error == errors.Forbidden


@pytest.mark.asyncio
async def test_order_agent_service_searches_mock_rag_knowledge() -> None:
    llm_client = FakeLLMClient(
        actions=[
            LLMActionModel(
                type="tool_call",
                tool="search_order_knowledge",
                args={"query": "电子发票多久能开好？", "top_k": 2},
            ),
            LLMActionModel(
                type="final",
                answer="电子发票通常会在提交申请后的 1-3 个工作日内完成。",
            ),
        ]
    )
    service = _new_order_agent_service(llm_client)

    result = await service.answer_order_support_question(
        user_id=999, question="电子发票多久能开好？", max_steps=3
    )

    action_result = result.steps[0].action_result
    assert result.status == "completed"
    assert result.steps[0].tool == "search_order_knowledge"
    assert action_result["ok"] is True
    assert action_result["query"] == "电子发票多久能开好？"
    assert action_result["total"] == 1
    assert action_result["matches"][0]["evidence_id"] == "ev_001"
    assert action_result["matches"][0]["chunk_id"] == 1001
    assert action_result["matches"][0]["source_uri"] == "order/help/invoice"
    assert "1-3 个工作日" in action_result["matches"][0]["content"]


@pytest.mark.asyncio
async def test_order_agent_service_calculates_refund_amount() -> None:
    llm_client = FakeLLMClient(
        actions=[
            LLMActionModel(
                type="tool_call",
                tool="calculate_refund_amount",
                args={
                    "unit_price_cents": 12990,
                    "quantity": 2,
                    "refundable_shipping_fee_cents": 1200,
                    "discount_deduction_cents": 3000,
                },
            ),
            LLMActionModel(type="final", answer="预计可退款 241.80 元。"),
        ]
    )
    service = _new_order_agent_service(llm_client)

    result = await service.answer_order_support_question(
        user_id=999,
        question="两件单价 129.90 元的商品，加上 12 元可退运费并扣回 30 元优惠，能退多少钱？",
        max_steps=3,
    )

    action_result = result.steps[0].action_result
    assert result.status == "completed"
    assert result.steps[0].tool == "calculate_refund_amount"
    assert action_result == {
        "ok": True,
        "unit_price_cents": 12990,
        "quantity": 2,
        "item_subtotal_cents": 25980,
        "refundable_shipping_fee_cents": 1200,
        "discount_deduction_cents": 3000,
        "refund_amount_cents": 24180,
        "refund_amount_yuan": "241.80",
    }


@pytest.mark.asyncio
async def test_order_agent_service_rejects_excessive_refund_discount() -> None:
    llm_client = FakeLLMClient(
        actions=[
            LLMActionModel(
                type="tool_call",
                tool="calculate_refund_amount",
                args={
                    "unit_price_cents": 1000,
                    "quantity": 1,
                    "discount_deduction_cents": 1100,
                },
            ),
            LLMActionModel(
                type="final", answer="优惠扣减金额超过可退金额，无法计算退款。"
            ),
        ]
    )
    service = _new_order_agent_service(llm_client)

    result = await service.answer_order_support_question(
        user_id=999,
        question="商品 10 元，退款时扣回 11 元优惠，能退多少？",
        max_steps=3,
    )

    assert result.steps[0].action_result == {
        "ok": False,
        "error": "discount_deduction_cents cannot exceed gross refund amount",
    }


@pytest.mark.asyncio
async def test_order_agent_service_rejects_decimal_refund_amount_input() -> None:
    llm_client = FakeLLMClient(
        actions=[
            LLMActionModel(
                type="tool_call",
                tool="calculate_refund_amount",
                args={"unit_price_cents": 1000.5, "quantity": 1},
            ),
            LLMActionModel(type="final", answer="金额参数必须使用整数分。"),
        ]
    )
    service = _new_order_agent_service(llm_client)

    result = await service.answer_order_support_question(
        user_id=999, question="按 1000.5 分计算退款", max_steps=3
    )

    assert result.steps[0].action_result == {
        "ok": False,
        "error": "unit_price_cents must be an integer",
    }
