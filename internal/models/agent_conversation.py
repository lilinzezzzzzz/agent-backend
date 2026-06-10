from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from pkg.database.base import ModelMixin
from pkg.database.types import JSONType


class AgentSession(ModelMixin):
    """Agent 用户会话表。"""

    __tablename__ = "agent_session"

    session_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, comment="对外会话 ID")
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="用户 ID")
    entrypoint: Mapped[str] = mapped_column(String(32), nullable=False, comment="创建会话的入口")
    status: Mapped[str] = mapped_column(String(32), nullable=False, comment="会话状态")
    title: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="会话标题")
    rolling_summary: Mapped[str | None] = mapped_column(Text, nullable=True, comment="滚动摘要")
    working_state: Mapped[dict[str, Any] | None] = mapped_column(JSONType(), nullable=True, comment="工作状态")
    recent_token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="最近上下文 token 数")
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="消息数")
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), nullable=True, comment="最后消息时间 UTC"
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True, comment="过期时间 UTC")

    __table_args__ = (
        UniqueConstraint("session_id", name="uq_agent_session_session_id"),
        Index("idx_agent_session_user_status_updated", "user_id", "status", "updated_at"),
        Index("idx_agent_session_user_last_message", "user_id", "last_message_at"),
    )


class AgentMessage(ModelMixin):
    """Agent 会话消息表。"""

    __tablename__ = "agent_message"

    message_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, comment="对外消息 ID")
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="对外会话 ID")
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="关联运行 ID")
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="用户 ID")
    role: Mapped[str] = mapped_column(String(16), nullable=False, comment="消息角色")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="消息内容")
    content_summary: Mapped[str | None] = mapped_column(Text, nullable=True, comment="消息摘要")
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="token 数")
    message_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata", JSONType(), nullable=True, comment="消息元数据"
    )

    __table_args__ = (
        UniqueConstraint("message_id", name="uq_agent_message_message_id"),
        Index("idx_agent_message_session_created", "session_id", "created_at", "id"),
        Index("idx_agent_message_user_created", "user_id", "created_at", "id"),
        Index("idx_agent_message_run_id", "run_id"),
    )


class AgentRun(ModelMixin):
    """Agent 单次运行表。"""

    __tablename__ = "agent_run"

    run_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, comment="对外运行 ID")
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="对外会话 ID")
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="用户 ID")
    entrypoint: Mapped[str] = mapped_column(String(32), nullable=False, comment="请求入口")
    agent_name: Mapped[str] = mapped_column(String(64), nullable=False, comment="Agent 名称")
    route: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="路由结果")
    status: Mapped[str] = mapped_column(String(32), nullable=False, comment="运行状态")
    max_steps: Mapped[int] = mapped_column(Integer, nullable=False, comment="最大执行步数")
    trace_id: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="请求 trace_id")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False, comment="开始时间 UTC")
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True, comment="结束时间 UTC")
    elapsed_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0, comment="耗时毫秒")
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="错误码")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True, comment="错误信息")
    run_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata", JSONType(), nullable=True, comment="运行元数据"
    )

    __table_args__ = (
        UniqueConstraint("run_id", name="uq_agent_run_run_id"),
        Index("idx_agent_run_session_created", "session_id", "created_at", "id"),
        Index("idx_agent_run_user_created", "user_id", "created_at", "id"),
        Index("idx_agent_run_agent_status", "agent_name", "status"),
        Index("idx_agent_run_trace_id", "trace_id"),
    )


class AgentRunStep(ModelMixin):
    """Agent 运行步骤表。"""

    __tablename__ = "agent_run_step"

    run_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="对外运行 ID")
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="对外会话 ID")
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="用户 ID")
    step_index: Mapped[int] = mapped_column(Integer, nullable=False, comment="步骤序号")
    status: Mapped[str] = mapped_column(String(32), nullable=False, comment="步骤状态")
    action_type: Mapped[str] = mapped_column(String(32), nullable=False, comment="动作类型")
    tool: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="工具名")
    args: Mapped[dict[str, Any] | None] = mapped_column(JSONType(), nullable=True, comment="工具参数")
    action_result: Mapped[Any | None] = mapped_column(JSONType(), nullable=True, comment="动作结果")
    artifact_id: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="工具产物 ID")
    error: Mapped[str | None] = mapped_column(Text, nullable=True, comment="错误信息")
    elapsed_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0, comment="步骤耗时毫秒")

    __table_args__ = (
        UniqueConstraint("run_id", "step_index", name="uq_agent_run_step_run_index"),
        Index("idx_agent_run_step_session_created", "session_id", "created_at", "id"),
        Index("idx_agent_run_step_user_created", "user_id", "created_at", "id"),
        Index("idx_agent_run_step_tool", "tool"),
    )
