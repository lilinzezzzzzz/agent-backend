from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from internal.schemas.agent import (
    AgentActionConfirmationSchema,
    AgentChatRespSchema,
    AgentOrderSupportRespSchema,
    AgentStepSchema,
    JsonValue,
)
from internal.services.dto.order import AgentActionConfirmationDTO, InvoiceRequestDTO
from pkg.agents import AgentFinal, AgentRunResult, AgentToolCall
from pkg.toolkit.string import uuid6_unique_str_id


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
    confirmation: AgentActionConfirmationDTO | None = None

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
            confirmation=_extract_confirmation(result),
        )

    def to_schema(self) -> AgentOrderSupportRespSchema:
        """转换为 API 响应 schema。"""
        return AgentOrderSupportRespSchema(
            run_id=self.run_id,
            status=self.status,
            answer=self.answer,
            steps=[step.to_schema() for step in self.steps],
            confirmation=AgentActionConfirmationSchema(
                token=self.confirmation.token,
                action=self.confirmation.action,
                summary=self.confirmation.summary,
                expires_in_seconds=self.confirmation.expires_in_seconds,
            )
            if self.confirmation is not None
            else None,
        )

    @classmethod
    def from_confirmed_invoice(cls, result: InvoiceRequestDTO) -> AgentRunDTO:
        """构造确定性确认执行结果。"""
        return cls(
            run_id=uuid6_unique_str_id(),
            status="completed",
            answer=result.message,
            steps=[
                AgentStepDTO(
                    index=0,
                    status="completed",
                    decision_type="tool_call",
                    tool="confirm_invoice_request",
                    args={},
                    observation=to_json_value(result.to_tool_result()),
                )
            ],
        )

    @classmethod
    def final(cls, *, answer: str) -> AgentRunDTO:
        """构造无需运行专业 Agent 的最终回答。"""
        return cls(
            run_id=uuid6_unique_str_id(),
            status="completed",
            answer=answer,
            steps=[],
        )


@dataclass(frozen=True, slots=True)
class AgentChatDTO:
    """统一 Agent Router 结果。"""

    route: str
    result: AgentRunDTO

    def to_schema(self) -> AgentChatRespSchema:
        """转换为统一 Agent Router 响应 schema。"""
        return AgentChatRespSchema(route=self.route, result=self.result.to_schema())


def _extract_confirmation(result: AgentRunResult) -> AgentActionConfirmationDTO | None:
    for step in reversed(result.steps):
        observation = step.observation
        if not isinstance(observation, Mapping):
            continue
        confirmation = observation.get("confirmation")
        if not isinstance(confirmation, Mapping):
            continue
        token = confirmation.get("token")
        action = confirmation.get("action")
        summary = confirmation.get("summary")
        expires_in_seconds = confirmation.get("expires_in_seconds")
        if (
            isinstance(token, str)
            and isinstance(action, str)
            and isinstance(summary, str)
            and isinstance(expires_in_seconds, int)
        ):
            return AgentActionConfirmationDTO(
                token=token,
                action=action,
                summary=summary,
                expires_in_seconds=expires_in_seconds,
            )
    return None


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
