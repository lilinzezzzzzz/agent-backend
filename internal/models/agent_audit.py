from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from pkg.database.base import ModelMixin
from pkg.database.types import JSONType


class AgentAudit(ModelMixin):
    """Agent 运行审计表。"""

    __tablename__ = "agent_audit"

    run_id: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="Agent 运行 ID"
    )
    agent_name: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="Agent 名称"
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False, comment="用户 ID"
    )
    trace_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="请求 trace_id"
    )
    user_input: Mapped[str] = mapped_column(
        Text, nullable=False, comment="脱敏后的用户输入"
    )
    max_steps: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="最大执行步数"
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, comment="运行状态")
    final_answer: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="脱敏后的最终回答"
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, comment="运行开始时间 UTC"
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), nullable=True, comment="运行结束时间 UTC"
    )
    elapsed_ms: Mapped[float] = mapped_column(
        Float, nullable=False, default=0, comment="运行耗时毫秒"
    )
    steps: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONType(), nullable=False, default=list, comment="脱敏后的步骤记录"
    )
    llm_calls: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONType(),
        nullable=False,
        default=list,
        comment="脱敏后的 LLM 调用记录",
    )
    audit_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata",
        JSONType(),
        nullable=True,
        comment="审计扩展元数据",
    )

    __table_args__ = (
        UniqueConstraint("agent_name", "run_id", name="uq_agent_audit_agent_run"),
        Index("idx_agent_audit_user_created", "user_id", "created_at"),
        Index("idx_agent_audit_trace_id", "trace_id"),
        Index("idx_agent_audit_agent_status", "agent_name", "status"),
    )
