from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class CeleryTaskStatus(StrEnum):
    """Celery 逻辑任务状态。"""

    PENDING_PUBLISH = "PENDING_PUBLISH"
    PUBLISHED = "PUBLISHED"
    PUBLISH_FAILED = "PUBLISH_FAILED"
    ORPHANED = "ORPHANED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    NEEDS_RECONCILIATION = "NEEDS_RECONCILIATION"
    CANCELLED = "CANCELLED"


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
    status: CeleryTaskStatus
    trace_id: str
    error_type: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


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
    status: CeleryTaskStatus
    trace_id: str
    error_type: str | None
    error_message: str | None
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
            status=self.status,
            trace_id=self.trace_id,
            error_type=self.error_type,
            error_message=self.error_message,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )
