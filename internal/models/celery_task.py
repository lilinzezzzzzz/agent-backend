from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from pkg.database.base import Base


class CeleryTaskRecord(Base):
    """PostgreSQL 中的 Celery 逻辑任务事实记录。"""

    __tablename__ = "celery_task_record"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    task_name: Mapped[str] = mapped_column(String(255), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    scope: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    execution_timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    idempotency_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "scope",
            "task_name",
            "idempotency_key_hash",
            name="uq_celery_task_record_idempotency",
        ),
        CheckConstraint(
            "status IN ('PENDING_PUBLISH', 'PUBLISHED', 'PUBLISH_FAILED', 'ORPHANED', "
            "'RUNNING', 'SUCCEEDED', 'FAILED', 'NEEDS_RECONCILIATION', 'CANCELLED')",
            name="ck_celery_task_record_status",
        ),
        CheckConstraint(
            "execution_timeout_seconds > 0", name="ck_celery_task_record_timeout"
        ),
        Index(
            "idx_celery_task_record_trace",
            "trace_id",
            text("created_at DESC"),
            text("id DESC"),
        ),
        Index("idx_celery_task_record_status_updated", "status", "updated_at", "id"),
        Index(
            "idx_celery_task_record_expired_running",
            "lease_expires_at",
            "id",
            postgresql_where=text("status = 'RUNNING'"),
        ),
        Index(
            "idx_celery_task_record_stale_published",
            "updated_at",
            "id",
            postgresql_where=text("status = 'PUBLISHED'"),
        ),
        Index(
            "idx_celery_task_record_idempotency_expiry",
            "idempotency_expires_at",
            "id",
            postgresql_where=text(
                "status IN ('PUBLISH_FAILED', 'SUCCEEDED', 'FAILED', 'NEEDS_RECONCILIATION', 'CANCELLED')"
            ),
        ),
    )

    @property
    def task_id(self) -> str:
        """返回该记录对应的 Celery task_id。"""
        return f"task_{self.id}"
