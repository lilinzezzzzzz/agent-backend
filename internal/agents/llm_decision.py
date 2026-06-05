from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, Field

from internal.infra.llm import AgentLLMClient
from pkg.agents import (
    AgentDecisionContext,
    AgentFinal,
    AgentToolCall,
    parse_agent_decision,
)
from pkg.toolkit.json import orjson_dumps


class LLMDecisionModel(BaseModel):
    """LLM 返回的结构化 Agent 动作。"""

    type: str = Field(..., description="动作类型，只能是 tool_call 或 final")
    tool: str | None = Field(None, description="type=tool_call 时要调用的工具名")
    args: dict[str, Any] = Field(default_factory=dict, description="工具调用参数")
    answer: str | None = Field(None, description="type=final 时的最终回答")
    call_id: str | None = Field(None, description="可选工具调用 ID")


class LLMReactDecisionMaker:
    """基于 LLM 结构化输出的通用 ReAct decision maker。"""

    def __init__(
        self,
        *,
        llm_client: AgentLLMClient,
        system_prompt: str,
        max_tokens: int | None = 800,
        temperature: float | None = 0,
        extra_completion_kwargs: Mapping[str, Any] | None = None,
    ):
        self._llm_client = llm_client
        self._system_prompt = system_prompt
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._extra_completion_kwargs = dict(extra_completion_kwargs or {})

    async def decide_next(
        self, context: AgentDecisionContext
    ) -> AgentToolCall | AgentFinal:
        """调用 LLM，并把结构化输出转换为下一步 Agent 动作。"""
        action = await self._llm_client.chat_completion_structured(
            messages=self._build_decision_messages(context=context),
            response_model=LLMDecisionModel,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
            **self._extra_completion_kwargs,
        )
        return parse_agent_decision(action.model_dump(exclude_none=True))

    def _build_decision_messages(
        self, *, context: AgentDecisionContext
    ) -> list[dict[str, Any]]:
        return [
            {
                "role": "system",
                "content": self._system_prompt,
            },
            {
                "role": "user",
                "content": orjson_dumps(
                    {
                        "question": context.user_input,
                        "available_tools": [
                            {
                                "name": tool.name,
                                "description": tool.description,
                                "parameters_schema": tool.parameters_schema,
                            }
                            for tool in context.tools
                        ],
                        "previous_steps": [
                            {
                                "index": step.index,
                                "action": _serialize_action(step.action),
                                "action_result": step.action_result,
                                "error": step.error,
                            }
                            for step in context.state.steps
                        ],
                        "output_contract": {
                            "tool_call": {
                                "type": "tool_call",
                                "tool": "tool_name",
                                "args": {},
                            },
                            "final": {"type": "final", "answer": "给用户的中文回答"},
                        },
                    }
                ),
            },
        ]


def _serialize_action(action: AgentToolCall | AgentFinal) -> dict[str, Any]:
    if isinstance(action, AgentFinal):
        return {"type": "final", "answer": action.answer}
    return {"type": "tool_call", "tool": action.tool, "args": action.args}
