from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from internal.schemas import ForbidExtraModel

from internal.schemas.order import AgentActionConfirmationDTO, InvoiceRequestDTO
from pkg.agents import (
    AgentCheckpoint,
    AgentFinal,
    AgentRunResult,
    AgentStepRecord,
    AgentToolCall,
)
from pkg.ids import uuid7_unique_str_id

type JsonValue = (
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
)


class AgentReqSchema(BaseModel):
    """Agent 通用请求。"""

    session_id: str | None = Field(
        None,
        description="会话 ID；首次普通对话可为空，继续对话时传入服务端返回值",
        min_length=1,
        max_length=64,
    )
    question: str = Field(..., description="用户问题", min_length=1, max_length=500)
    max_steps: int = Field(4, description="Agent 最大执行步数", ge=1, le=8)
    confirmation_token: str | None = Field(
        None,
        description="服务端签发的一次性动作确认 token",
        min_length=1,
        max_length=128,
    )
    idempotency_key: str | None = Field(
        None,
        description="确认副作用动作时必填的客户端幂等键",
        min_length=8,
        max_length=128,
    )

    @model_validator(mode="after")
    def validate_confirmation_fields(self) -> AgentReqSchema:
        """确认 token 与幂等键必须同时提供。"""
        if (self.confirmation_token is None) != (self.idempotency_key is None):
            raise ValueError(
                "confirmation_token and idempotency_key must be provided together"
            )
        return self


class AgentStepSchema(BaseModel):
    """Agent 单步执行记录。"""

    index: int = Field(..., description="步骤序号，从 0 开始")
    status: str = Field(..., description="步骤状态")
    action_type: str = Field(..., description="Agent 动作类型")
    tool: str | None = Field(None, description="被调用的工具名")
    args: dict[str, JsonValue] = Field(default_factory=dict, description="工具调用参数")
    action_result: JsonValue = Field(None, description="动作执行结果")
    error: str | None = Field(None, description="工具或执行错误")
    elapsed_ms: float = Field(..., description="步骤耗时，单位毫秒")


class AgentActionConfirmationSchema(BaseModel):
    """需要客户端显式确认的服务端动作。"""

    token: str = Field(..., description="短期、一次性的服务端确认 token")
    action: str = Field(..., description="待确认动作名")
    summary: str = Field(..., description="展示给用户确认的动作摘要")
    expires_in_seconds: int = Field(..., description="确认 token 有效期，单位秒")


class AgentRunRespSchema(BaseModel):
    """业务 Agent 运行响应。"""

    session_id: str = Field(..., description="本次 Agent 运行所属会话 ID")
    run_id: str = Field(..., description="本次 Agent 运行 ID")
    status: str = Field(..., description="Agent 运行状态")
    answer: str | None = Field(None, description="最终回答；达到步数上限时可能为空")
    steps: list[AgentStepSchema] = Field(
        default_factory=list, description="执行步骤记录"
    )
    confirmation: AgentActionConfirmationSchema | None = Field(
        None, description="存在副作用动作时返回的待确认信息"
    )


class AgentOrderSupportReqSchema(AgentReqSchema):
    """订单支持 Agent 请求。"""


class AgentOrderSupportRespSchema(AgentRunRespSchema):
    """订单支持 Agent 响应。"""


class AgentPaymentSupportReqSchema(AgentReqSchema):
    """支付支持 Agent 请求。"""


class AgentPaymentSupportRespSchema(AgentRunRespSchema):
    """支付支持 Agent 响应。"""


class AgentChatReqSchema(AgentReqSchema):
    """统一 Agent 聊天请求。"""


class AgentChatRespSchema(BaseModel):
    """统一 Agent Router 响应。"""

    route: str = Field(..., description="Router 选择的业务域")
    result: AgentRunRespSchema = Field(..., description="业务 Agent 执行结果")


class AgentRunEntrypoint(StrEnum):
    """受管理 run 支持的入口。"""

    CHAT = "chat"
    ORDER_SUPPORT = "order_support"
    PAYMENT_SUPPORT = "payment_support"


class AgentRunCreateReqSchema(ForbidExtraModel):
    """创建受管理 run 的请求；只创建并冻结上下文，不调用模型。"""

    entrypoint: AgentRunEntrypoint = Field(..., description="业务入口")
    question: str = Field(..., description="用户问题", min_length=1, max_length=500)
    session_id: str | None = Field(
        None,
        description="会话 ID；为空时新建会话",
        min_length=1,
        max_length=64,
    )
    max_steps: int = Field(4, description="本 run 的最大执行步数", ge=1, le=8)
    request_key: str = Field(
        ...,
        description="创建幂等键；同键不同输入返回冲突",
        min_length=8,
        max_length=128,
    )


class AgentRunCreateRespSchema(BaseModel):
    """受管理 run 创建响应。"""

    run_id: str = Field(..., description="运行 ID")
    session_id: str = Field(..., description="会话 ID")
    entrypoint: str = Field(..., description="业务入口")
    status: str = Field(..., description="运行状态；创建后为 ready")
    max_steps: int = Field(..., description="本 run 的最大执行步数")


class AgentRunInterruptReqSchema(ForbidExtraModel):
    """打断请求；只提交控制意图，不承诺响应返回时已暂停。"""

    reason: str | None = Field(
        None, description="有界打断原因", max_length=200, min_length=1
    )


class AgentRunInterruptRespSchema(BaseModel):
    """打断受理响应。"""

    run_id: str = Field(..., description="运行 ID")
    status: str = Field(..., description="服务端当前实际状态")
    accepted: bool = Field(..., description="是否处于打断已受理或已暂停状态")


class AgentRunResumeReqSchema(ForbidExtraModel):
    """恢复请求；不接受 question、max_steps、工具参数或替换上下文。"""

    request_key: str = Field(
        ...,
        description="本次执行尝试的幂等键；重试同一 attempt 必须复用",
        min_length=8,
        max_length=128,
    )


class AgentRunDetailRespSchema(BaseModel):
    """受管理 run 的公开状态视图；不暴露原始 checkpoint。"""

    run_id: str = Field(..., description="运行 ID")
    session_id: str = Field(..., description="会话 ID")
    entrypoint: str = Field(..., description="业务入口")
    agent_name: str = Field(..., description="业务 Agent 名称")
    status: str = Field(..., description="运行状态")
    phase: str | None = Field(None, description="执行现场阶段")
    revision: int = Field(..., description="已提交 checkpoint 版本")
    attempt_no: int = Field(..., description="当前 attempt 序号")
    completed_steps: int = Field(..., description="已提交步骤数")
    max_steps: int = Field(..., description="本 run 的最大执行步数")
    resumable: bool = Field(..., description="是否允许普通恢复")
    execution_version: str | None = Field(
        None, description="受管理执行协议版本；为空表示旧 run 不可恢复"
    )
    route: str | None = Field(None, description="已冻结的业务域")
    started_at: datetime = Field(..., description="首次开始时间 UTC")
    ended_at: datetime | None = Field(None, description="终态结束时间 UTC")
    elapsed_ms: float = Field(..., description="累计实际执行耗时毫秒")
    stop_reason: str | None = Field(None, description="暂停或停止原因")
    answer: str | None = Field(None, description="已生成且本用户可见的最终回答")


class AgentRunResumeRespSchema(BaseModel):
    """恢复执行响应；包含 run 状态和本次结果。"""

    run: AgentRunDetailRespSchema = Field(..., description="运行状态视图")
    result: AgentRunRespSchema = Field(..., description="本次执行结果")


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
            action_type="final" if isinstance(step.action, AgentFinal) else "tool_call",
            tool=step.action.tool if isinstance(step.action, AgentToolCall) else None,
            args=to_json_object(step.action.args)
            if isinstance(step.action, AgentToolCall)
            else {},
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
class AgentRunStateDTO:
    """受管理 run 的持久化状态快照（Service / 存储后端内部使用）。"""

    run_id: str
    session_id: str
    user_id: UUID
    entrypoint: str
    agent_name: str
    status: str
    max_steps: int
    revision: int
    attempt_no: int
    started_at: datetime
    route: str | None = None
    execution_version: str | None = None
    ended_at: datetime | None = None
    elapsed_ms: float = 0
    error_code: str | None = None
    error_message: str | None = None
    interrupt_reason: str | None = None
    create_request_key: str | None = None
    create_request_digest: str | None = None
    checkpoint: AgentCheckpoint | None = None

    @property
    def phase(self) -> str | None:
        """返回 checkpoint 记录的执行阶段。"""
        return self.checkpoint.phase.value if self.checkpoint is not None else None

    @property
    def completed_steps(self) -> int:
        """返回 checkpoint 中已提交的步骤数。"""
        return len(self.checkpoint.steps) if self.checkpoint is not None else 0


@dataclass(frozen=True, slots=True)
class AgentRunControlDTO:
    """受管理 run 的控制状态；执行者用它判断是否还能继续。"""

    status: str
    revision: int
    attempt_no: int
    lease_owned: bool
    interrupt_requested: bool = False


@dataclass(frozen=True, slots=True)
class AgentRunClaimDTO:
    """一次 claim / resume 的执行权结果。"""

    run_id: str
    session_id: str
    status: str
    attempt_no: int
    checkpoint: AgentCheckpoint
    lease_token: str | None = None
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class AgentRunCommitDTO:
    """一次原子 checkpoint / 步骤 / 终态提交的结果。"""

    status: str
    revision: int
    checkpoint: AgentCheckpoint
    paused: bool = False


@dataclass(frozen=True, slots=True)
class AgentRunViewDTO:
    """公开查询返回的受管理 run 视图。"""

    run_id: str
    session_id: str
    entrypoint: str
    agent_name: str
    status: str
    phase: str | None
    revision: int
    attempt_no: int
    completed_steps: int
    max_steps: int
    resumable: bool
    started_at: datetime
    route: str | None = None
    execution_version: str | None = None
    ended_at: datetime | None = None
    elapsed_ms: float = 0
    stop_reason: str | None = None
    answer: str | None = None

    @classmethod
    def from_state(cls, state: AgentRunStateDTO) -> AgentRunViewDTO:
        """从内部状态快照裁剪出公开视图。"""
        return cls(
            run_id=state.run_id,
            session_id=state.session_id,
            entrypoint=state.entrypoint,
            agent_name=state.agent_name,
            status=state.status,
            phase=state.phase,
            revision=state.revision,
            attempt_no=state.attempt_no,
            completed_steps=state.completed_steps,
            max_steps=state.max_steps,
            resumable=(
                state.execution_version is not None
                and state.status in ("ready", "interrupted")
            ),
            started_at=state.started_at,
            route=state.route,
            execution_version=state.execution_version,
            ended_at=state.ended_at,
            elapsed_ms=state.elapsed_ms,
            stop_reason=_resolve_stop_reason(state),
            answer=(
                state.checkpoint.final_answer
                if state.checkpoint is not None
                and state.status in ("completed", "interrupted")
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class AgentRunCreateDTO:
    """受管理 run 创建结果 DTO。"""

    run_id: str
    session_id: str
    entrypoint: str
    status: str
    max_steps: int

    def to_schema(self) -> AgentRunCreateRespSchema:
        """转换为 API 响应 schema。"""
        return AgentRunCreateRespSchema(
            run_id=self.run_id,
            session_id=self.session_id,
            entrypoint=self.entrypoint,
            status=self.status,
            max_steps=self.max_steps,
        )


@dataclass(frozen=True, slots=True)
class AgentRunInterruptDTO:
    """打断受理结果 DTO。"""

    run_id: str
    status: str
    accepted: bool

    def to_schema(self) -> AgentRunInterruptRespSchema:
        """转换为 API 响应 schema。"""
        return AgentRunInterruptRespSchema(
            run_id=self.run_id,
            status=self.status,
            accepted=self.accepted,
        )


@dataclass(frozen=True, slots=True)
class AgentRunResumeDTO:
    """恢复执行结果 DTO。"""

    run: AgentRunViewDTO
    result: AgentRunResultDTO

    def to_schema(self) -> AgentRunResumeRespSchema:
        """转换为 API 响应 schema。"""
        return AgentRunResumeRespSchema(
            run=run_view_to_schema(self.run),
            result=self.result.to_schema(),
        )


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
            run_id=run_id or uuid7_unique_str_id(),
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
            run_id=run_id or uuid7_unique_str_id(),
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
    RUN_RESUMED = "run_resumed"
    STEP_COMPLETED = "step_completed"
    RUN_INTERRUPTED = "run_interrupted"
    RUN_STATUS = "run_status"
    RUN_COMPLETED = "run_completed"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class AgentRunEventContextDTO:
    """受管理运行事件的公共上下文，随每个运行事件一起下发。"""

    run_id: str
    session_id: str
    attempt_no: int
    checkpoint_revision: int | None = None
    route: str | None = None

    def to_fields(self) -> dict[str, JsonValue]:
        """转换为事件 data 中的公共字段。"""
        fields: dict[str, JsonValue] = {
            "run_id": self.run_id,
            "session_id": self.session_id,
            "attempt_no": self.attempt_no,
        }
        if self.route is not None:
            fields["route"] = self.route
        if self.checkpoint_revision is not None:
            fields["checkpoint_revision"] = self.checkpoint_revision
        return fields


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
    def run_resumed(
        cls,
        *,
        run_id: str,
        status: str = "running",
        route: str | None = None,
    ) -> AgentStreamEventDTO:
        """构造 Agent 运行恢复事件。"""
        return cls(
            event=AgentStreamEventName.RUN_RESUMED,
            data=_with_optional_fields(
                {"run_id": run_id, "status": status},
                route=route,
            ),
        )

    @classmethod
    def run_interrupted(
        cls,
        *,
        run_id: str,
        status: str = "interrupted",
        stop_reason: str | None = None,
        route: str | None = None,
    ) -> AgentStreamEventDTO:
        """构造 Agent 运行暂停事件；暂停不使用 run_completed 收尾。"""
        return cls(
            event=AgentStreamEventName.RUN_INTERRUPTED,
            data=_with_optional_fields(
                {"run_id": run_id, "status": status},
                route=route,
                stop_reason=stop_reason,
            ),
        )

    @classmethod
    def run_status(cls, *, data: dict[str, JsonValue]) -> AgentStreamEventDTO:
        """构造运行状态事件；重复 resume 时用它输出当前状态。"""
        return cls(event=AgentStreamEventName.RUN_STATUS, data=data)

    def with_context(self, context: AgentRunEventContextDTO) -> AgentStreamEventDTO:
        """把受管理运行上下文合并进事件 data。"""
        data = dict(self.data)
        data.update(context.to_fields())
        return AgentStreamEventDTO(event=self.event, data=data, result=self.result)

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


def _resolve_stop_reason(state: AgentRunStateDTO) -> str | None:
    """按状态优先级推导公开视图中的停止原因。"""
    if state.status in ("failed", "recovery_required"):
        return state.error_message or state.error_code
    if state.status == "interrupted":
        return state.interrupt_reason or "interrupt_requested"
    return None


def run_view_to_schema(view: AgentRunViewDTO) -> AgentRunDetailRespSchema:
    """把公开 run 视图 DTO 转换为响应 schema。"""
    return AgentRunDetailRespSchema(
        run_id=view.run_id,
        session_id=view.session_id,
        entrypoint=view.entrypoint,
        agent_name=view.agent_name,
        status=view.status,
        phase=view.phase,
        revision=view.revision,
        attempt_no=view.attempt_no,
        completed_steps=view.completed_steps,
        max_steps=view.max_steps,
        resumable=view.resumable,
        execution_version=view.execution_version,
        route=view.route,
        started_at=view.started_at,
        ended_at=view.ended_at,
        elapsed_ms=view.elapsed_ms,
        stop_reason=view.stop_reason,
        answer=view.answer,
    )
