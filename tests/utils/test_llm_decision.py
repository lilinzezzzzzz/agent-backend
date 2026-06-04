from typing import Any

import pytest

from internal.utils.agents import LLMDecisionModel, LLMReactDecisionMaker
from pkg.agents import ReActAgent, StructuredTool
from pkg.toolkit.json import orjson_loads


class FakeLLMClient:
    def __init__(self, decisions: list[LLMDecisionModel]):
        self._decisions = decisions
        self.calls: list[dict[str, Any]] = []

    async def chat_completion_structured(self, **kwargs: Any) -> LLMDecisionModel:
        self.calls.append(kwargs)
        return self._decisions.pop(0)


def build_order_tool() -> StructuredTool:
    return StructuredTool(
        name="get_order_status",
        description="按订单 ID 查询订单物流状态。",
        parameters_schema={
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
        },
        handler=lambda args: {
            "ok": True,
            "order_id": args["order_id"],
            "status": "运输中",
        },
    )


@pytest.mark.asyncio
async def test_llm_react_decision_maker_builds_messages_and_parses_decisions() -> None:
    llm_client = FakeLLMClient(
        decisions=[
            LLMDecisionModel(
                type="tool_call", tool="get_order_status", args={"order_id": "1001"}
            ),
            LLMDecisionModel(type="final", answer="订单 1001 正在运输中。"),
        ]
    )
    decision_maker = LLMReactDecisionMaker(
        llm_client=llm_client,
        system_prompt="你是订单助手。",
        max_tokens=256,
        temperature=0,
        extra_completion_kwargs={"thinking": False},
    )
    agent = ReActAgent(
        decision_maker=decision_maker,
        tools=[build_order_tool()],
        max_steps=2,
    )

    result = await agent.run(user_input="订单 1001 到哪了？", run_id="run_1")

    assert result.final_answer == "订单 1001 正在运输中。"
    assert len(llm_client.calls) == 2
    assert llm_client.calls[0]["response_model"] is LLMDecisionModel
    assert llm_client.calls[0]["max_tokens"] == 256
    assert llm_client.calls[0]["temperature"] == 0
    assert llm_client.calls[0]["thinking"] is False
    assert llm_client.calls[0]["messages"][0] == {
        "role": "system",
        "content": "你是订单助手。",
    }

    first_payload = orjson_loads(llm_client.calls[0]["messages"][1]["content"])
    assert first_payload["question"] == "订单 1001 到哪了？"
    assert first_payload["available_tools"][0]["name"] == "get_order_status"
    assert first_payload["previous_steps"] == []

    second_payload = orjson_loads(llm_client.calls[1]["messages"][1]["content"])
    assert second_payload["previous_steps"] == [
        {
            "index": 0,
            "decision": {
                "type": "tool_call",
                "tool": "get_order_status",
                "args": {"order_id": "1001"},
            },
            "observation": {
                "ok": True,
                "order_id": "1001",
                "status": "运输中",
            },
            "error": None,
        }
    ]
