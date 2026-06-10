from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from internal.schemas.agent import (
    AgentActionConfirmationSchema,
    AgentChatRespSchema,
    AgentRunRespSchema,
    AgentStepSchema,
    JsonValue,
)
from internal.services.dto.order import AgentActionConfirmationDTO, InvoiceRequestDTO
from pkg.agents import AgentFinal, AgentRunResult, AgentStepRecord, AgentToolCall
from pkg.ids import uuid6_unique_str_id


@dataclass(frozen=True, slots=True)
class AgentStepDTO:
    """Agent 单步执行记录 DTO。"""

    index: int
    status: str
    action_type: str
    tool: str | None = None
    args: dict[str, JsonValue] = field(default_factory=dict)
    action_result: JsonValue = None
    error: str | None = None
    elapsed_ms: float = 0

    @classmethod
    def from_step_record(cls, step: AgentStepRecord) -> AgentStepDTO:
        """从通用 Agent step record 构造业务 DTO。"""
        return cls(
            index=step.index,
            status=step.status.value,
            action_type=(
                "final" if isinstance(step.action, AgentFinal) else "tool_call"
            ),
            tool=(step.action.tool if isinstance(step.action, AgentToolCall) else None),
            args=(
                to_json_object(step.action.args)
                if isinstance(step.action, AgentToolCall)
                else {}
            ),
            action_result=to_json_value(step.action_result),
            error=step.error,
            elapsed_ms=step.elapsed_ms,
        )

    def to_schema(self) -> AgentStepSchema:
        """转换为 API 响应 schema。"""
        return AgentStepSchema(
            index=self.index,
            status=self.status,
            action_type=self.action_type,
            tool=self.tool,
            args=self.args,
            action_result=self.action_result,
            error=self.error,
            elapsed_ms=self.elapsed_ms,
        )


@dataclass(frozen=True, slots=True)
class AgentMessageDTO:
    """Agent 会话上下文消息 DTO。"""

    role: str
    content: str


@dataclass(frozen=True, slots=True)
class AgentConversationContextDTO:
    """Agent 会话上下文 DTO。"""

    session_id: str
    rolling_summary: str | None = None
    recent_messages: Sequence[AgentMessageDTO] = field(default_factory=tuple)
    working_state: dict[str, JsonValue] = field(default_factory=dict)
    truncated: bool = False

    def to_prompt_context(self) -> dict[str, JsonValue]:
        """转换为 LLM prompt 可安全序列化的上下文字段。"""
        return {
            "session_id": self.session_id,
            "rolling_summary": self.rolling_summary,
            "recent_messages": [
                {"role": message.role, "content": message.content}
                for message in self.recent_messages
            ],
            "working_state": self.working_state,
            "truncated": self.truncated,
        }


@dataclass(frozen=True, slots=True)
class AgentRunStartDTO:
    """Agent run 启动结果 DTO。"""

    session_id: str
    run_id: str
    user_message_id: str


@dataclass(frozen=True, slots=True)
class AgentRunResultDTO:
    """Agent 运行结果 DTO。"""

    run_id: str
    status: str
    answer: str | None
    steps: Sequence[AgentStepDTO]
    session_id: str | None = None
    confirmation: AgentActionConfirmationDTO | None = None
    audit_metadata: dict[str, JsonValue] | None = None

    @classmethod
    def from_agent_result(
        cls, result: AgentRunResult, *, session_id: str | None = None
    ) -> AgentRunResultDTO:
        """从 ReActAgent 运行结果构造业务 DTO。"""
        return cls(
            run_id=result.run_id,
            status=result.status.value,
            answer=result.final_answer,
            steps=[AgentStepDTO.from_step_record(step) for step in result.steps],
            session_id=session_id,
            confirmation=_extract_confirmation(result),
        )

    def to_schema(self) -> AgentRunRespSchema:
        """转换为 API 响应 schema。"""
        return AgentRunRespSchema(
            session_id=self.session_id or "",
            run_id=self.run_id,
            status=self.status,
            answer=self.answer,
            steps=[step.to_schema() for step in self.steps],
            confirmation=(
                AgentActionConfirmationSchema(
                    token=self.confirmation.token,
                    action=self.confirmation.action,
                    summary=self.confirmation.summary,
                    expires_in_seconds=self.confirmation.expires_in_seconds,
                )
                if self.confirmation is not None
                else None
            ),
        )

    @classmethod
    def from_confirmed_invoice(
        cls,
        result: InvoiceRequestDTO,
        *,
        run_id: str | None = None,
        session_id: str | None = None,
    ) -> AgentRunResultDTO:
        """构造确定性确认执行结果。"""
        return cls(
            run_id=run_id or uuid6_unique_str_id(),
            status="completed",
            answer=result.message,
            steps=[
                AgentStepDTO(
                    index=0,
                    status="completed",
                    action_type="tool_call",
                    tool="confirm_invoice_request",
                    args={},
                    action_result=to_json_value(result.to_action_result()),
                )
            ],
            session_id=session_id,
        )

    @classmethod
    def final(
        cls,
        *,
        answer: str,
        run_id: str | None = None,
        session_id: str | None = None,
    ) -> AgentRunResultDTO:
        """构造无需运行专业 Agent 的最终回答。"""
        return cls(
            run_id=run_id or uuid6_unique_str_id(),
            status="completed",
            answer=answer,
            steps=[],
            session_id=session_id,
        )


@dataclass(frozen=True, slots=True)
class AgentChatDTO:
    """统一 Agent Router 结果。"""

    route: str
    result: AgentRunResultDTO

    def to_schema(self) -> AgentChatRespSchema:
        """转换为统一 Agent Router 响应 schema。"""
        return AgentChatRespSchema(route=self.route, result=self.result.to_schema())


class AgentStreamEventName(StrEnum):
    """Agent SSE 事件名。"""

    ROUTE = "route"
    RUN_STARTED = "run_started"
    STEP_COMPLETED = "step_completed"
    RUN_COMPLETED = "run_completed"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class AgentStreamEventDTO:
    """Service 输出给 Controller 的 Agent 流式事件。"""

    event: AgentStreamEventName
    data: dict[str, JsonValue] = field(default_factory=dict)
    result: AgentRunResultDTO | None = field(default=None, compare=False, repr=False)

    @classmethod
    def route(cls, *, route: str) -> AgentStreamEventDTO:
        """构造路由选择事件。"""
        return cls(event=AgentStreamEventName.ROUTE, data={"route": route})

    @classmethod
    def run_started(
        cls,
        *,
        run_id: str,
        status: str = "running",
        route: str | None = None,
    ) -> AgentStreamEventDTO:
        """构造 Agent 运行开始事件。"""
        return cls(
            event=AgentStreamEventName.RUN_STARTED,
            data=_with_optional_fields(
                {"run_id": run_id, "status": status},
                route=route,
            ),
        )

    @classmethod
    def step_completed(
        cls,
        *,
        run_id: str,
        step: AgentStepDTO,
        route: str | None = None,
    ) -> AgentStreamEventDTO:
        """构造单步完成事件。"""
        return cls(
            event=AgentStreamEventName.STEP_COMPLETED,
            data=_with_optional_fields(
                {
                    "run_id": run_id,
                    "step": to_json_object(step.to_schema().model_dump(mode="json")),
                },
                route=route,
            ),
        )

    @classmethod
    def run_completed(
        cls,
        *,
        result: AgentRunResultDTO,
        route: str | None = None,
    ) -> AgentStreamEventDTO:
        """构造 Agent 运行完成事件。"""
        return cls(
            event=AgentStreamEventName.RUN_COMPLETED,
            data=_with_optional_fields(
                {
                    "run_id": result.run_id,
                    "status": result.status,
                    "result": to_json_object(
                        result.to_schema().model_dump(mode="json")
                    ),
                },
                route=route,
            ),
            result=result,
        )

    @classmethod
    def error(
        cls,
        *,
        code: int,
        message: str,
        run_id: str | None = None,
        route: str | None = None,
    ) -> AgentStreamEventDTO:
        """构造 Agent 流式错误事件。"""
        return cls(
            event=AgentStreamEventName.ERROR,
            data=_with_optional_fields(
                {"code": code, "message": message},
                run_id=run_id,
                route=route,
            ),
        )

    def with_route(self, route: str) -> AgentStreamEventDTO:
        """返回带业务路由字段的新事件。"""
        data = dict(self.data)
        data["route"] = route
        return AgentStreamEventDTO(event=self.event, data=data, result=self.result)


def _extract_confirmation(result: AgentRunResult) -> AgentActionConfirmationDTO | None:
    for step in reversed(result.steps):
        action_result = step.action_result
        if not isinstance(action_result, Mapping):
            continue
        confirmation = action_result.get("confirmation")
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
    """把任意动作结果压缩为可响应的 JSON 值。"""
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


def _with_optional_fields(
    data: dict[str, JsonValue], **fields: str | None
) -> dict[str, JsonValue]:
    for key, value in fields.items():
        if value is not None:
            data[key] = value
    return data
