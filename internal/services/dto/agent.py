from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from internal.schemas.agent import (
    AgentOrderSupportRespSchema,
    AgentStepSchema,
    JsonValue,
)
from pkg.agents import AgentFinal, AgentRunResult, AgentToolCall


@dataclass(frozen=True, slots=True)
class AgentStepDTO:
    """Agent 单步执行记录 DTO。"""

    index: int
    status: str
    decision_type: str
    tool: str | None = None
    args: dict[str, JsonValue] = field(default_factory=dict)
    observation: JsonValue = None
    error: str | None = None
    elapsed_ms: float = 0

    def to_schema(self) -> AgentStepSchema:
        """转换为 API 响应 schema。"""
        return AgentStepSchema(
            index=self.index,
            status=self.status,
            decision_type=self.decision_type,
            tool=self.tool,
            args=self.args,
            observation=self.observation,
            error=self.error,
            elapsed_ms=self.elapsed_ms,
        )


@dataclass(frozen=True, slots=True)
class AgentRunDTO:
    """Agent 运行结果 DTO。"""

    run_id: str
    status: str
    answer: str | None
    steps: Sequence[AgentStepDTO]

    @classmethod
    def from_agent_result(cls, result: AgentRunResult) -> AgentRunDTO:
        """从 ReActAgent 运行结果构造业务 DTO。"""
        return cls(
            run_id=result.run_id,
            status=result.status.value,
            answer=result.final_answer,
            steps=[
                AgentStepDTO(
                    index=step.index,
                    status=step.status.value,
                    decision_type="final"
                    if isinstance(step.decision, AgentFinal)
                    else "tool_call",
                    tool=step.decision.tool
                    if isinstance(step.decision, AgentToolCall)
                    else None,
                    args=to_json_object(step.decision.args)
                    if isinstance(step.decision, AgentToolCall)
                    else {},
                    observation=to_json_value(step.observation),
                    error=step.error,
                    elapsed_ms=step.elapsed_ms,
                )
                for step in result.steps
            ],
        )

    def to_schema(self) -> AgentOrderSupportRespSchema:
        """转换为 API 响应 schema。"""
        return AgentOrderSupportRespSchema(
            run_id=self.run_id,
            status=self.status,
            answer=self.answer,
            steps=[step.to_schema() for step in self.steps],
        )


def to_json_value(value: object) -> JsonValue:
    """把任意工具结果压缩为可响应的 JSON 值。"""
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Mapping):
        return {str(key): to_json_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [to_json_value(item) for item in value]
    return str(value)


def to_json_object(value: object) -> dict[str, JsonValue]:
    """把任意 mapping 压缩为 JSON object；非 mapping 返回空对象。"""
    if not isinstance(value, Mapping):
        return {}
    return {str(key): to_json_value(item) for key, item in value.items()}
