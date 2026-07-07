from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class CeleryTaskStatus(StrEnum):
    """Celery 逻辑任务状态。"""

    SUBMITTING = "SUBMITTING"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    CANCELLING = "CANCELLING"
    ORPHANED = "ORPHANED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    @property
    def is_terminal(self) -> bool:
        return self in {
            CeleryTaskStatus.SUCCEEDED,
            CeleryTaskStatus.FAILED,
            CeleryTaskStatus.CANCELLED,
        }


class CeleryTaskErrorCode(StrEnum):
    """Celery 逻辑任务稳定错误码。"""

    BROKER_PUBLISH_FAILED = "BROKER_PUBLISH_FAILED"
    PUBLISH_CONFIRMATION_TIMEOUT = "PUBLISH_CONFIRMATION_TIMEOUT"
    QUEUE_START_TIMEOUT = "QUEUE_START_TIMEOUT"
    WORKER_LOST_OR_TIMEOUT = "WORKER_LOST_OR_TIMEOUT"
    CANCEL_DEADLINE_EXCEEDED = "CANCEL_DEADLINE_EXCEEDED"
    TASK_EXECUTION_FAILED = "TASK_EXECUTION_FAILED"


class IdempotentSumCreateReqSchema(BaseModel):
    """创建幂等加法 demo 任务的请求。"""

    idempotency_key: str = Field(min_length=1, max_length=256)
    x: int
    y: int


class CeleryTaskDispatchRespSchema(BaseModel):
    """任务登记结果。"""

    record_id: int
    task_id: str
    status: CeleryTaskStatus
    created: bool


class CeleryTaskDetailSchema(BaseModel):
    """逻辑任务详情。"""

    record_id: int
    task_id: str
    task_name: str
    queue: str
    status: CeleryTaskStatus
    cancel_allowed: bool
    trace_id: str
    attempt_count: int
    queued_deadline_at: datetime | None = None
    hard_deadline_at: datetime | None = None
    fence_expires_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_code: CeleryTaskErrorCode | None = None
    error_summary: str | None = None
    created_at: datetime
    updated_at: datetime


class CeleryTaskCancelRespSchema(BaseModel):
    """任务取消命令结果。"""

    record_id: int
    task_id: str
    status: CeleryTaskStatus


@dataclass(frozen=True, slots=True)
class CeleryTaskDispatchDTO:
    """任务登记结果 DTO。"""

    record_id: int
    status: CeleryTaskStatus
    created: bool

    @property
    def task_id(self) -> str:
        return f"task_{self.record_id}"

    def to_schema(self) -> CeleryTaskDispatchRespSchema:
        return CeleryTaskDispatchRespSchema(
            record_id=self.record_id,
            task_id=self.task_id,
            status=self.status,
            created=self.created,
        )


@dataclass(frozen=True, slots=True)
class CeleryTaskDetailDTO:
    """逻辑任务详情 DTO。"""

    record_id: int
    task_name: str
    queue: str
    status: CeleryTaskStatus
    cancel_allowed: bool
    trace_id: str
    attempt_count: int
    queued_deadline_at: datetime | None
    hard_deadline_at: datetime | None
    fence_expires_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    error_code: CeleryTaskErrorCode | None
    error_summary: str | None
    created_at: datetime
    updated_at: datetime

    @property
    def task_id(self) -> str:
        return f"task_{self.record_id}"

    def to_schema(self) -> CeleryTaskDetailSchema:
        return CeleryTaskDetailSchema(
            record_id=self.record_id,
            task_id=self.task_id,
            task_name=self.task_name,
            queue=self.queue,
            status=self.status,
            cancel_allowed=self.cancel_allowed,
            trace_id=self.trace_id,
            attempt_count=self.attempt_count,
            queued_deadline_at=self.queued_deadline_at,
            hard_deadline_at=self.hard_deadline_at,
            fence_expires_at=self.fence_expires_at,
            started_at=self.started_at,
            finished_at=self.finished_at,
            error_code=self.error_code,
            error_summary=self.error_summary,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


@dataclass(frozen=True, slots=True)
class CeleryTaskCancelDTO:
    """任务取消命令 DTO。"""

    record_id: int
    status: CeleryTaskStatus

    @property
    def task_id(self) -> str:
        return f"task_{self.record_id}"

    def to_schema(self) -> CeleryTaskCancelRespSchema:
        return CeleryTaskCancelRespSchema(
            record_id=self.record_id,
            task_id=self.task_id,
            status=self.status,
        )
