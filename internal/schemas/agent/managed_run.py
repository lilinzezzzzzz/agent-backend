"""受管理运行请求/响应、持久化状态与控制 DTO。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field

from internal.schemas import ForbidExtraModel
from internal.schemas.agent.chat import (
    AgentRunRespSchema,
    AgentRunResultDTO,
)
from pkg.agents import (
    AgentCheckpoint,
)


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
