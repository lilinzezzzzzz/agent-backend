"""OpenTelemetry Span 模拟接口的 schema 与 DTO。"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class SpanStatus(StrEnum):
    """Span 结束状态。"""

    OK = "ok"
    ERROR = "error"


class SpanSimulateReqSchema(BaseModel):
    """Span 模拟请求。"""

    scenario: str = Field(default="default", min_length=1, max_length=64)
    fail_at: str | None = Field(
        default=None,
        max_length=128,
        description=(
            "指定注入模拟异常的步骤。可选值：`validate` / `db.read` / "
            "`llm.intent` / `rag.recall` / `llm.business` / `cache.write`。"
        ),
    )


class SpanNodeSchema(BaseModel):
    """单个 OTel Span 节点。"""

    trace_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    span_id: str = Field(pattern=r"^[0-9a-f]{16}$")
    parent_span_id: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{16}$",
        description="父 Span ID；Trace Root 为 null。",
    )
    span_name: str
    span_kind: str
    status: SpanStatus
    elapsed_ms: float = Field(ge=0.0)
    started_at: datetime
    finished_at: datetime
    error_type: str | None = None


class SpanSimulateRespSchema(BaseModel):
    """完整 Trace 响应。"""

    scenario: str
    trace_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    root_span_id: str = Field(pattern=r"^[0-9a-f]{16}$")
    root_span_name: str
    span_count: int = Field(ge=0)
    elapsed_ms: float = Field(ge=0.0)
    failed_step: str | None = None
    spans: list[SpanNodeSchema]


class SpanSimulateResultSchema(BaseModel):
    """触发 OTel Span 模拟流程后的响应。"""

    trace_id: str = Field(pattern=r"^[0-9a-f]{32}$")


@dataclass(frozen=True, slots=True)
class SpanNodeDTO:
    """Service 内部承载的单个 Span。"""

    trace_id: str
    span_id: str
    parent_span_id: str | None
    span_name: str
    span_kind: str
    status: SpanStatus
    elapsed_ms: float
    started_at: datetime
    finished_at: datetime
    error_type: str | None

    def to_schema(self) -> SpanNodeSchema:
        return SpanNodeSchema(
            trace_id=self.trace_id,
            span_id=self.span_id,
            parent_span_id=self.parent_span_id,
            span_name=self.span_name,
            span_kind=self.span_kind,
            status=self.status,
            elapsed_ms=self.elapsed_ms,
            started_at=self.started_at,
            finished_at=self.finished_at,
            error_type=self.error_type,
        )


@dataclass(frozen=True, slots=True)
class SpanSimulateDTO:
    """Service 层承载的 Span 模拟结果。"""

    trace_id: str

    def to_schema(self) -> SpanSimulateResultSchema:
        return SpanSimulateResultSchema(trace_id=self.trace_id)


@dataclass(frozen=True, slots=True)
class SpanTraceDTO:
    """Service 层承载的完整 mock Trace。"""

    scenario: str
    trace_id: str
    root_span_id: str
    root_span_name: str
    elapsed_ms: float
    failed_step: str | None
    spans: tuple[SpanNodeDTO, ...]

    def to_schema(self) -> SpanSimulateRespSchema:
        return SpanSimulateRespSchema(
            scenario=self.scenario,
            trace_id=self.trace_id,
            root_span_id=self.root_span_id,
            root_span_name=self.root_span_name,
            span_count=len(self.spans),
            elapsed_ms=self.elapsed_ms,
            failed_step=self.failed_step,
            spans=[node.to_schema() for node in self.spans],
        )
