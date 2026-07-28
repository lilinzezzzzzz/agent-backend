from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from pkg.database.base import ModelMixin


class CeleryTaskRecord(ModelMixin):
    """PostgreSQL 中的 Celery 逻辑任务事实记录。"""

    __tablename__ = "celery_task_record"

    task_name: Mapped[str] = mapped_column(String(255), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    scope: Mapped[str] = mapped_column(String(128), nullable=False)
    queue: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    cancel_allowed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    execution_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    queued_deadline_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), nullable=True
    )
    hard_deadline_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), nullable=True
    )
    fence_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(String(512), nullable=True)
    __table_args__ = (
        UniqueConstraint(
            "scope",
            "task_name",
            "idempotency_key_hash",
            name="uq_celery_task_record_idempotency",
        ),
        CheckConstraint(
            "status IN ('SUBMITTING', 'QUEUED', 'RUNNING', 'CANCELLING', "
            "'ORPHANED', 'SUCCEEDED', 'FAILED', 'CANCELLED')",
            name="ck_celery_task_record_status",
        ),
        CheckConstraint(
            "attempt_count >= 0", name="ck_celery_task_record_attempt_count"
        ),
        Index(
            "idx_celery_task_record_scope_trace",
            "scope",
            "trace_id",
            text("created_at DESC"),
            text("id DESC"),
        ),
        Index(
            "idx_celery_task_record_scope_status",
            "scope",
            "status",
            "created_at",
            "id",
        ),
        Index(
            "idx_celery_task_record_stale_submitting",
            "updated_at",
            "id",
            postgresql_where=text("status = 'SUBMITTING'"),
        ),
        Index(
            "idx_celery_task_record_queued_deadline",
            "queued_deadline_at",
            "id",
            postgresql_where=text("status = 'QUEUED'"),
        ),
        Index(
            "idx_celery_task_record_execution_deadline",
            "hard_deadline_at",
            "id",
            postgresql_where=text("status IN ('RUNNING', 'CANCELLING')"),
        ),
        Index(
            "idx_celery_task_record_orphan_fence",
            "fence_expires_at",
            "id",
            postgresql_where=text("status = 'ORPHANED'"),
        ),
    )

    @property
    def task_id(self) -> str:
        """返回该记录对应的 Celery task_id。"""
        return f"task_{self.id}"
