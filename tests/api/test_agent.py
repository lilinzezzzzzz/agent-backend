from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from internal.agents import LLMDecisionModel
from internal.agents.order import ORDER_SUPPORT_SYSTEM_PROMPT, OrderAgentBuilder
from internal.controllers.api import agent as agent_controller
from internal.schemas.agent import AgentOrderSupportReqSchema
from internal.services.agents import OrderAgentService
from internal.services.dto.agent import AgentRunDTO, AgentStepDTO
from pkg.toolkit import context


class FakeOrderAgentService:
    async def answer_order_support_question(
        self, *, question: str, max_steps: int = 4
    ) -> AgentRunDTO:
        assert question == "订单 1001 到哪了？"
        assert max_steps == 3
        return AgentRunDTO(
            run_id="run_1",
            status="completed",
            answer="订单 1001 正在运输中。",
            steps=[
                AgentStepDTO(
                    index=0,
                    status="completed",
                    decision_type="tool_call",
                    tool="get_order_status",
                    args={"order_id": "1001"},
                    observation={"ok": True, "status": "运输中"},
                    elapsed_ms=1.2,
                )
            ],
        )


class FakeLLMClient:
    def __init__(self, decisions: list[LLMDecisionModel]):
        self._decisions = decisions
        self.calls: list[dict[str, Any]] = []

    async def chat_completion_structured(self, **kwargs: Any) -> LLMDecisionModel:
        self.calls.append(kwargs)
        return self._decisions.pop(0)


def _response_payload(response) -> dict[str, Any]:
    return response.model_dump(mode="json")


def test_order_agent_system_prompt_names_all_registered_tools() -> None:
    builder = OrderAgentBuilder(llm_client=FakeLLMClient([]), max_steps=3)
    registered_tools = builder.build_tools()

    assert [tool.name for tool in registered_tools] == [
        "get_order_status",
        "get_return_policy",
        "search_order_knowledge",
        "calculate_refund_amount",
        "submit_invoice_request",
    ]
    for tool in registered_tools:
        assert f"`{tool.name}`" in ORDER_SUPPORT_SYSTEM_PROMPT


def test_order_agent_system_prompt_defines_when_tools_are_optional() -> None:
    prompt = ORDER_SUPPORT_SYSTEM_PROMPT

    assert "必须先调用对应工具" in prompt
    assert "才可以不调用工具并直接返回 final" in prompt
    assert "每轮只能返回一个决策" in prompt
    assert "缺少工具必填参数时" in prompt
    assert "仅当用户明确要求提交开票申请时" in prompt
    assert "不得伪造成功结果" in prompt


@pytest_asyncio.fixture
async def agent_client():
    app = FastAPI()
    app.include_router(agent_controller.router, prefix="/v1")
    app.dependency_overrides[agent_controller.new_order_agent_service] = lambda: (
        FakeOrderAgentService()
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
            "run_id": "run_1",
            "status": "completed",
            "answer": "订单 1001 正在运输中。",
            "steps": [
                {
                    "index": 0,
                    "status": "completed",
                    "decision_type": "tool_call",
                    "tool": "get_order_status",
                    "args": {"order_id": "1001"},
                    "observation": {"ok": True, "status": "运输中"},
                    "error": None,
                    "elapsed_ms": 1.2,
                }
            ],
        },
    }


@pytest.mark.asyncio
async def test_order_support_agent_controller_wraps_service_dto() -> None:
    response = await agent_controller.order_support_agent(
        AgentOrderSupportReqSchema(question="订单 1001 到哪了？", max_steps=3),
        FakeOrderAgentService(),
    )

    assert _response_payload(response)["data"]["answer"] == "订单 1001 正在运输中。"
    assert _response_payload(response)["data"]["steps"][0]["tool"] == "get_order_status"


@pytest.mark.asyncio
async def test_order_agent_service_runs_react_with_tools() -> None:
    llm_client = FakeLLMClient(
        decisions=[
            LLMDecisionModel(
                type="tool_call", tool="get_order_status", args={"order_id": "1001"}
            ),
            LLMDecisionModel(
                type="final", answer="订单 1001 由顺丰速运承运，预计明天 18:00 前送达。"
            ),
        ]
    )
    service = OrderAgentService(llm_client=llm_client)

    result = await service.answer_order_support_question(
        question="订单 1001 到哪了？", max_steps=3
    )

    assert result.status == "completed"
    assert result.answer == "订单 1001 由顺丰速运承运，预计明天 18:00 前送达。"
    assert len(result.steps) == 2
    assert result.steps[0].tool == "get_order_status"
    assert result.steps[0].observation == {
        "ok": True,
        "order_id": "1001",
        "status": "运输中",
        "carrier": "顺丰速运",
        "tracking_no": "SF10010001",
        "eta": "明天 18:00 前",
    }
    assert len(llm_client.calls) == 2
    assert llm_client.calls[0]["response_model"] is LLMDecisionModel


@pytest.mark.asyncio
async def test_order_agent_service_records_unknown_order_observation() -> None:
    llm_client = FakeLLMClient(
        decisions=[
            LLMDecisionModel(
                type="tool_call", tool="get_order_status", args={"order_id": "404"}
            ),
            LLMDecisionModel(type="final", answer="没有查询到订单 404。"),
        ]
    )
    service = OrderAgentService(llm_client=llm_client)

    result = await service.answer_order_support_question(
        question="订单 404 到哪了？", max_steps=3
    )

    assert result.status == "completed"
    assert result.steps[0].observation == {
        "ok": False,
        "error": "not_found",
        "order_id": "404",
    }


@pytest.mark.asyncio
async def test_order_agent_service_queues_invoice_request(monkeypatch) -> None:
    llm_client = FakeLLMClient(
        decisions=[
            LLMDecisionModel(
                type="tool_call",
                tool="submit_invoice_request",
                args={
                    "order_id": "1001",
                    "invoice_title": "个人",
                    "email": "buyer@example.com",
                },
            ),
            LLMDecisionModel(type="final", answer="开票申请已提交，完成后会通知你。"),
        ]
    )
    service = OrderAgentService(llm_client=llm_client)
    monkeypatch.setattr(context, "get_trace_id", lambda: "trace_invoice_test")

    result = await service.answer_order_support_question(
        question="帮我给订单 1001 开发票", max_steps=3
    )

    assert result.status == "completed"
    assert result.answer == "开票申请已提交，完成后会通知你。"
    assert result.steps[0].tool == "submit_invoice_request"
    assert result.steps[0].observation["ok"] is True
    assert result.steps[0].observation["order_id"] == "1001"
    assert result.steps[0].observation["invoice_title"] == "个人"
    assert result.steps[0].observation["email"] == "buyer@example.com"
    assert result.steps[0].observation["status"] == "queued"
    assert result.steps[0].observation["trace_id"] == "trace_invoice_test"
    assert result.steps[0].observation["task_id"].startswith("task_")


@pytest.mark.asyncio
async def test_order_agent_service_searches_mock_rag_knowledge() -> None:
    llm_client = FakeLLMClient(
        decisions=[
            LLMDecisionModel(
                type="tool_call",
                tool="search_order_knowledge",
                args={"query": "电子发票多久能开好？", "top_k": 2},
            ),
            LLMDecisionModel(
                type="final",
                answer="电子发票通常会在提交申请后的 1-3 个工作日内完成。",
            ),
        ]
    )
    service = OrderAgentService(llm_client=llm_client)

    result = await service.answer_order_support_question(
        question="电子发票多久能开好？", max_steps=3
    )

    observation = result.steps[0].observation
    assert result.status == "completed"
    assert result.steps[0].tool == "search_order_knowledge"
    assert observation["ok"] is True
    assert observation["query"] == "电子发票多久能开好？"
    assert observation["total"] == 1
    assert observation["matches"][0]["id"] == "order_invoice_guide"
    assert observation["matches"][0]["source"] == "订单帮助中心/发票服务"
    assert "1-3 个工作日" in observation["matches"][0]["content"]


@pytest.mark.asyncio
async def test_order_agent_service_calculates_refund_amount() -> None:
    llm_client = FakeLLMClient(
        decisions=[
            LLMDecisionModel(
                type="tool_call",
                tool="calculate_refund_amount",
                args={
                    "unit_price_cents": 12990,
                    "quantity": 2,
                    "refundable_shipping_fee_cents": 1200,
                    "discount_deduction_cents": 3000,
                },
            ),
            LLMDecisionModel(type="final", answer="预计可退款 241.80 元。"),
        ]
    )
    service = OrderAgentService(llm_client=llm_client)

    result = await service.answer_order_support_question(
        question="两件单价 129.90 元的商品，加上 12 元可退运费并扣回 30 元优惠，能退多少钱？",
        max_steps=3,
    )

    observation = result.steps[0].observation
    assert result.status == "completed"
    assert result.steps[0].tool == "calculate_refund_amount"
    assert observation == {
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
        decisions=[
            LLMDecisionModel(
                type="tool_call",
                tool="calculate_refund_amount",
                args={
                    "unit_price_cents": 1000,
                    "quantity": 1,
                    "discount_deduction_cents": 1100,
                },
            ),
            LLMDecisionModel(
                type="final", answer="优惠扣减金额超过可退金额，无法计算退款。"
            ),
        ]
    )
    service = OrderAgentService(llm_client=llm_client)

    result = await service.answer_order_support_question(
        question="商品 10 元，退款时扣回 11 元优惠，能退多少？",
        max_steps=3,
    )

    assert result.steps[0].observation == {
        "ok": False,
        "error": "discount_deduction_cents cannot exceed gross refund amount",
    }


@pytest.mark.asyncio
async def test_order_agent_service_rejects_decimal_refund_amount_input() -> None:
    llm_client = FakeLLMClient(
        decisions=[
            LLMDecisionModel(
                type="tool_call",
                tool="calculate_refund_amount",
                args={"unit_price_cents": 1000.5, "quantity": 1},
            ),
            LLMDecisionModel(type="final", answer="金额参数必须使用整数分。"),
        ]
    )
    service = OrderAgentService(llm_client=llm_client)

    result = await service.answer_order_support_question(
        question="按 1000.5 分计算退款", max_steps=3
    )

    assert result.steps[0].observation == {
        "ok": False,
        "error": "unit_price_cents must be an integer",
    }
