"""initial schema

Revision ID: 20261104_0001
Revises: base
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261104_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建接入 Alembic 时的完整 PostgreSQL 表结构。"""
    op.create_table(
        "agent_audit",
        sa.Column(
            "run_id",
            sa.String(length=64),
            nullable=False,
            comment="Agent 运行 ID",
        ),
        sa.Column(
            "agent_name",
            sa.String(length=64),
            nullable=False,
            comment="Agent 名称",
        ),
        sa.Column("user_id", sa.Uuid(), nullable=False, comment="用户 ID"),
        sa.Column(
            "trace_id",
            sa.String(length=128),
            nullable=True,
            comment="请求 trace_id",
        ),
        sa.Column("user_input", sa.Text(), nullable=False, comment="脱敏后的用户输入"),
        sa.Column("max_steps", sa.Integer(), nullable=False, comment="最大执行步数"),
        sa.Column("status", sa.String(length=32), nullable=False, comment="运行状态"),
        sa.Column("final_answer", sa.Text(), nullable=True, comment="脱敏后的最终回答"),
        sa.Column(
            "started_at",
            sa.DateTime(),
            nullable=False,
            comment="运行开始时间 UTC",
        ),
        sa.Column("ended_at", sa.DateTime(), nullable=True, comment="运行结束时间 UTC"),
        sa.Column("elapsed_ms", sa.Float(), nullable=False, comment="运行耗时毫秒"),
        sa.Column(
            "steps",
            postgresql.JSONB(none_as_null=False),
            nullable=False,
            comment="脱敏后的步骤记录",
        ),
        sa.Column(
            "llm_calls",
            postgresql.JSONB(none_as_null=False),
            nullable=False,
            comment="脱敏后的 LLM 调用记录",
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(none_as_null=False),
            nullable=True,
            comment="审计扩展元数据",
        ),
        sa.Column("id", sa.Uuid(), autoincrement=False, nullable=False),
        sa.Column("creator_id", sa.Uuid(), nullable=True),
        sa.Column("creator_type", sa.String(length=32), nullable=False),
        sa.Column("updater_id", sa.Uuid(), nullable=True),
        sa.Column("updater_type", sa.String(length=32), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_name", "run_id", name="uq_agent_audit_agent_run"),
    )
    op.create_index(
        "idx_agent_audit_agent_status",
        "agent_audit",
        ["agent_name", "status"],
        unique=False,
    )
    op.create_index(
        "idx_agent_audit_trace_id",
        "agent_audit",
        ["trace_id"],
        unique=False,
    )
    op.create_index(
        "idx_agent_audit_user_created",
        "agent_audit",
        ["user_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "agent_message",
        sa.Column(
            "message_id",
            sa.String(length=64),
            nullable=False,
            comment="对外消息 ID",
        ),
        sa.Column(
            "session_id",
            sa.String(length=64),
            nullable=False,
            comment="对外会话 ID",
        ),
        sa.Column("run_id", sa.String(length=64), nullable=True, comment="关联运行 ID"),
        sa.Column("user_id", sa.Uuid(), nullable=False, comment="用户 ID"),
        sa.Column("role", sa.String(length=16), nullable=False, comment="消息角色"),
        sa.Column("content", sa.Text(), nullable=False, comment="消息内容"),
        sa.Column("content_summary", sa.Text(), nullable=True, comment="消息摘要"),
        sa.Column("token_count", sa.Integer(), nullable=False, comment="token 数"),
        sa.Column(
            "metadata",
            postgresql.JSONB(none_as_null=False),
            nullable=True,
            comment="消息元数据",
        ),
        sa.Column("id", sa.Uuid(), autoincrement=False, nullable=False),
        sa.Column("creator_id", sa.Uuid(), nullable=True),
        sa.Column("creator_type", sa.String(length=32), nullable=False),
        sa.Column("updater_id", sa.Uuid(), nullable=True),
        sa.Column("updater_type", sa.String(length=32), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("message_id", name="uq_agent_message_message_id"),
    )
    op.create_index(
        "idx_agent_message_run_id",
        "agent_message",
        ["run_id"],
        unique=False,
    )
    op.create_index(
        "idx_agent_message_session_created",
        "agent_message",
        ["session_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "idx_agent_message_user_created",
        "agent_message",
        ["user_id", "created_at", "id"],
        unique=False,
    )
    op.create_table(
        "agent_run",
        sa.Column(
            "run_id",
            sa.String(length=64),
            nullable=False,
            comment="对外运行 ID",
        ),
        sa.Column(
            "session_id",
            sa.String(length=64),
            nullable=False,
            comment="对外会话 ID",
        ),
        sa.Column("user_id", sa.Uuid(), nullable=False, comment="用户 ID"),
        sa.Column(
            "entrypoint",
            sa.String(length=32),
            nullable=False,
            comment="请求入口",
        ),
        sa.Column(
            "agent_name",
            sa.String(length=64),
            nullable=False,
            comment="Agent 名称",
        ),
        sa.Column("route", sa.String(length=32), nullable=True, comment="路由结果"),
        sa.Column("status", sa.String(length=32), nullable=False, comment="运行状态"),
        sa.Column("max_steps", sa.Integer(), nullable=False, comment="最大执行步数"),
        sa.Column(
            "trace_id",
            sa.String(length=128),
            nullable=True,
            comment="请求 trace_id",
        ),
        sa.Column("started_at", sa.DateTime(), nullable=False, comment="开始时间 UTC"),
        sa.Column("ended_at", sa.DateTime(), nullable=True, comment="结束时间 UTC"),
        sa.Column("elapsed_ms", sa.Float(), nullable=False, comment="累计执行耗时毫秒"),
        sa.Column("error_code", sa.String(length=64), nullable=True, comment="错误码"),
        sa.Column("error_message", sa.Text(), nullable=True, comment="错误信息"),
        sa.Column(
            "execution_version",
            sa.String(length=32),
            nullable=True,
            comment="受管理执行协议版本；为空表示旧 run，不允许恢复",
        ),
        sa.Column(
            "checkpoint_revision",
            sa.Integer(),
            nullable=True,
            comment="已提交 checkpoint 乐观并发版本",
        ),
        sa.Column(
            "lease_token",
            sa.String(length=64),
            nullable=True,
            comment="当前执行者 fencing token",
        ),
        sa.Column(
            "lease_expires_at",
            sa.DateTime(),
            nullable=True,
            comment="lease 到期时间 UTC",
        ),
        sa.Column(
            "interrupt_requested_at",
            sa.DateTime(),
            nullable=True,
            comment="打断请求时间 UTC",
        ),
        sa.Column(
            "interrupted_at",
            sa.DateTime(),
            nullable=True,
            comment="完成暂停时间 UTC",
        ),
        sa.Column(
            "interrupt_reason",
            sa.String(length=200),
            nullable=True,
            comment="打断原因",
        ),
        sa.Column(
            "create_request_key",
            sa.String(length=128),
            nullable=True,
            comment="创建幂等键",
        ),
        sa.Column(
            "create_request_digest",
            sa.String(length=64),
            nullable=True,
            comment="创建请求摘要",
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(none_as_null=False),
            nullable=True,
            comment="运行元数据",
        ),
        sa.Column("id", sa.Uuid(), autoincrement=False, nullable=False),
        sa.Column("creator_id", sa.Uuid(), nullable=True),
        sa.Column("creator_type", sa.String(length=32), nullable=False),
        sa.Column("updater_id", sa.Uuid(), nullable=True),
        sa.Column("updater_type", sa.String(length=32), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", name="uq_agent_run_run_id"),
        sa.UniqueConstraint(
            "user_id",
            "create_request_key",
            name="uq_agent_run_user_create_key",
        ),
    )
    op.create_index(
        "idx_agent_run_agent_status",
        "agent_run",
        ["agent_name", "status"],
        unique=False,
    )
    op.create_index(
        "idx_agent_run_session_created",
        "agent_run",
        ["session_id", "created_at", "id"],
        unique=False,
    )
    op.create_index("idx_agent_run_trace_id", "agent_run", ["trace_id"], unique=False)
    op.create_index(
        "idx_agent_run_user_created",
        "agent_run",
        ["user_id", "created_at", "id"],
        unique=False,
    )
    op.create_table(
        "agent_run_attempt",
        sa.Column(
            "run_id",
            sa.String(length=64),
            nullable=False,
            comment="对外运行 ID",
        ),
        sa.Column("user_id", sa.Uuid(), nullable=False, comment="用户 ID"),
        sa.Column(
            "attempt_no",
            sa.Integer(),
            nullable=False,
            comment="attempt 序号，从 1 递增",
        ),
        sa.Column(
            "request_key",
            sa.String(length=128),
            nullable=False,
            comment="客户端请求幂等键",
        ),
        sa.Column(
            "request_digest",
            sa.String(length=64),
            nullable=False,
            comment="请求摘要",
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            comment="attempt 结果状态",
        ),
        sa.Column(
            "trace_id",
            sa.String(length=128),
            nullable=True,
            comment="请求 trace_id",
        ),
        sa.Column(
            "started_at",
            sa.DateTime(),
            nullable=False,
            comment="attempt 开始时间 UTC",
        ),
        sa.Column(
            "ended_at",
            sa.DateTime(),
            nullable=True,
            comment="attempt 结束时间 UTC",
        ),
        sa.Column(
            "elapsed_ms",
            sa.Float(),
            nullable=False,
            comment="attempt 实际执行耗时毫秒",
        ),
        sa.Column("id", sa.Uuid(), autoincrement=False, nullable=False),
        sa.Column("creator_id", sa.Uuid(), nullable=True),
        sa.Column("creator_type", sa.String(length=32), nullable=False),
        sa.Column("updater_id", sa.Uuid(), nullable=True),
        sa.Column("updater_type", sa.String(length=32), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "attempt_no", name="uq_agent_run_attempt_no"),
        sa.UniqueConstraint(
            "run_id",
            "request_key",
            name="uq_agent_run_attempt_request_key",
        ),
    )
    op.create_table(
        "agent_run_checkpoint",
        sa.Column(
            "run_id",
            sa.String(length=64),
            nullable=False,
            comment="对外运行 ID",
        ),
        sa.Column(
            "session_id",
            sa.String(length=64),
            nullable=False,
            comment="对外会话 ID",
        ),
        sa.Column("user_id", sa.Uuid(), nullable=False, comment="用户 ID"),
        sa.Column(
            "schema_version",
            sa.Integer(),
            nullable=False,
            comment="checkpoint JSON 格式版本",
        ),
        sa.Column("revision", sa.Integer(), nullable=False, comment="乐观并发版本"),
        sa.Column(
            "phase",
            sa.String(length=32),
            nullable=False,
            comment="checkpoint 执行阶段",
        ),
        sa.Column(
            "next_step_index",
            sa.Integer(),
            nullable=False,
            comment="下一个未提交步骤序号",
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(none_as_null=False),
            nullable=False,
            comment="checkpoint JSON 载荷",
        ),
        sa.Column("id", sa.Uuid(), autoincrement=False, nullable=False),
        sa.Column("creator_id", sa.Uuid(), nullable=True),
        sa.Column("creator_type", sa.String(length=32), nullable=False),
        sa.Column("updater_id", sa.Uuid(), nullable=True),
        sa.Column("updater_type", sa.String(length=32), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", name="uq_agent_run_checkpoint_run_id"),
    )
    op.create_index(
        "idx_agent_run_checkpoint_user_created",
        "agent_run_checkpoint",
        ["user_id", "created_at", "id"],
        unique=False,
    )
    op.create_table(
        "agent_run_step",
        sa.Column(
            "run_id",
            sa.String(length=64),
            nullable=False,
            comment="对外运行 ID",
        ),
        sa.Column(
            "session_id",
            sa.String(length=64),
            nullable=False,
            comment="对外会话 ID",
        ),
        sa.Column("user_id", sa.Uuid(), nullable=False, comment="用户 ID"),
        sa.Column("step_index", sa.Integer(), nullable=False, comment="步骤序号"),
        sa.Column("status", sa.String(length=32), nullable=False, comment="步骤状态"),
        sa.Column(
            "action_type",
            sa.String(length=32),
            nullable=False,
            comment="动作类型",
        ),
        sa.Column("tool", sa.String(length=64), nullable=True, comment="工具名"),
        sa.Column(
            "args",
            postgresql.JSONB(none_as_null=False),
            nullable=True,
            comment="工具参数",
        ),
        sa.Column(
            "action_result",
            postgresql.JSONB(none_as_null=False),
            nullable=True,
            comment="动作结果",
        ),
        sa.Column(
            "artifact_id",
            sa.String(length=64),
            nullable=True,
            comment="工具产物 ID",
        ),
        sa.Column("error", sa.Text(), nullable=True, comment="错误信息"),
        sa.Column("elapsed_ms", sa.Float(), nullable=False, comment="步骤耗时毫秒"),
        sa.Column("id", sa.Uuid(), autoincrement=False, nullable=False),
        sa.Column("creator_id", sa.Uuid(), nullable=True),
        sa.Column("creator_type", sa.String(length=32), nullable=False),
        sa.Column("updater_id", sa.Uuid(), nullable=True),
        sa.Column("updater_type", sa.String(length=32), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "step_index", name="uq_agent_run_step_run_index"),
    )
    op.create_index(
        "idx_agent_run_step_session_created",
        "agent_run_step",
        ["session_id", "created_at", "id"],
        unique=False,
    )
    op.create_index("idx_agent_run_step_tool", "agent_run_step", ["tool"], unique=False)
    op.create_index(
        "idx_agent_run_step_user_created",
        "agent_run_step",
        ["user_id", "created_at", "id"],
        unique=False,
    )
    op.create_table(
        "agent_session",
        sa.Column(
            "session_id",
            sa.String(length=64),
            nullable=False,
            comment="对外会话 ID",
        ),
        sa.Column("user_id", sa.Uuid(), nullable=False, comment="用户 ID"),
        sa.Column(
            "entrypoint",
            sa.String(length=32),
            nullable=False,
            comment="创建会话的入口",
        ),
        sa.Column("status", sa.String(length=32), nullable=False, comment="会话状态"),
        sa.Column("title", sa.String(length=128), nullable=True, comment="会话标题"),
        sa.Column("rolling_summary", sa.Text(), nullable=True, comment="滚动摘要"),
        sa.Column(
            "working_state",
            postgresql.JSONB(none_as_null=False),
            nullable=True,
            comment="工作状态",
        ),
        sa.Column(
            "recent_token_count",
            sa.Integer(),
            nullable=False,
            comment="最近上下文 token 数",
        ),
        sa.Column("message_count", sa.Integer(), nullable=False, comment="消息数"),
        sa.Column(
            "last_message_at",
            sa.DateTime(),
            nullable=True,
            comment="最后消息时间 UTC",
        ),
        sa.Column("expires_at", sa.DateTime(), nullable=True, comment="过期时间 UTC"),
        sa.Column("id", sa.Uuid(), autoincrement=False, nullable=False),
        sa.Column("creator_id", sa.Uuid(), nullable=True),
        sa.Column("creator_type", sa.String(length=32), nullable=False),
        sa.Column("updater_id", sa.Uuid(), nullable=True),
        sa.Column("updater_type", sa.String(length=32), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", name="uq_agent_session_session_id"),
    )
    op.create_index(
        "idx_agent_session_user_last_message",
        "agent_session",
        ["user_id", "last_message_at"],
        unique=False,
    )
    op.create_index(
        "idx_agent_session_user_status_updated",
        "agent_session",
        ["user_id", "status", "updated_at"],
        unique=False,
    )
    op.create_table(
        "celery_task_record",
        sa.Column("task_name", sa.String(length=255), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("scope", sa.String(length=128), nullable=False),
        sa.Column("queue", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "cancel_allowed",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("execution_token", sa.String(length=64), nullable=True),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("queued_deadline_at", sa.DateTime(), nullable=True),
        sa.Column("hard_deadline_at", sa.DateTime(), nullable=True),
        sa.Column("fence_expires_at", sa.DateTime(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_summary", sa.String(length=512), nullable=True),
        sa.Column("id", sa.Uuid(), autoincrement=False, nullable=False),
        sa.Column("creator_id", sa.Uuid(), nullable=True),
        sa.Column("creator_type", sa.String(length=32), nullable=False),
        sa.Column("updater_id", sa.Uuid(), nullable=True),
        sa.Column("updater_type", sa.String(length=32), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('SUBMITTING', 'QUEUED', 'RUNNING', 'CANCELLING', "
            "'ORPHANED', 'SUCCEEDED', 'FAILED', 'CANCELLED')",
            name="ck_celery_task_record_status",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_celery_task_record_attempt_count",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "scope",
            "task_name",
            "idempotency_key_hash",
            name="uq_celery_task_record_idempotency",
        ),
    )
    op.create_index(
        "idx_celery_task_record_execution_deadline",
        "celery_task_record",
        ["hard_deadline_at", "id"],
        unique=False,
        postgresql_where=sa.text("status IN ('RUNNING', 'CANCELLING')"),
    )
    op.create_index(
        "idx_celery_task_record_orphan_fence",
        "celery_task_record",
        ["fence_expires_at", "id"],
        unique=False,
        postgresql_where=sa.text("status = 'ORPHANED'"),
    )
    op.create_index(
        "idx_celery_task_record_queued_deadline",
        "celery_task_record",
        ["queued_deadline_at", "id"],
        unique=False,
        postgresql_where=sa.text("status = 'QUEUED'"),
    )
    op.create_index(
        "idx_celery_task_record_scope_status",
        "celery_task_record",
        ["scope", "status", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "idx_celery_task_record_scope_trace",
        "celery_task_record",
        [
            "scope",
            "trace_id",
            sa.literal_column("created_at DESC"),
            sa.literal_column("id DESC"),
        ],
        unique=False,
    )
    op.create_index(
        "idx_celery_task_record_stale_submitting",
        "celery_task_record",
        ["updated_at", "id"],
        unique=False,
        postgresql_where=sa.text("status = 'SUBMITTING'"),
    )
    op.create_table(
        "external_identity",
        sa.Column(
            "user_id",
            sa.Uuid(),
            nullable=False,
            comment="关联 user.id 的逻辑外键",
        ),
        sa.Column(
            "provider",
            sa.String(length=32),
            nullable=False,
            comment="认证提供方，如 wechat、google、github",
        ),
        sa.Column(
            "connection_key",
            sa.String(length=64),
            nullable=False,
            comment="稳定的认证连接标识，不存储 client secret",
        ),
        sa.Column(
            "subject",
            sa.String(length=256),
            nullable=False,
            comment="认证连接内稳定且大小写敏感的用户标识",
        ),
        sa.Column(
            "email_snapshot",
            sa.String(length=255),
            nullable=True,
            comment="最近一次认证返回的邮箱快照，不作为身份主键",
        ),
        sa.Column(
            "union_id",
            sa.String(length=256),
            nullable=True,
            comment="提供方返回的跨应用标识快照，如微信 UnionID",
        ),
        sa.Column(
            "nickname_snapshot",
            sa.String(length=128),
            nullable=True,
            comment="最近一次认证返回的昵称快照，不作为身份主键",
        ),
        sa.Column(
            "avatar_snapshot",
            sa.String(length=512),
            nullable=True,
            comment="最近一次认证返回的头像 URL 快照",
        ),
        sa.Column(
            "last_authenticated_at",
            sa.DateTime(),
            nullable=False,
            comment="最近认证成功时间，naive UTC",
        ),
        sa.Column("id", sa.Uuid(), autoincrement=False, nullable=False),
        sa.Column("creator_id", sa.Uuid(), nullable=True),
        sa.Column("creator_type", sa.String(length=32), nullable=False),
        sa.Column("updater_id", sa.Uuid(), nullable=True),
        sa.Column("updater_type", sa.String(length=32), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider",
            "connection_key",
            "subject",
            name="uq_external_identity_subject",
        ),
    )
    op.create_index(
        "idx_external_identity_user_provider",
        "external_identity",
        ["user_id", "provider", "deleted_at"],
        unique=False,
    )
    op.create_table(
        "scoped_operation_locks",
        sa.Column(
            "operation_scope",
            sa.String(length=64),
            nullable=False,
            comment="锁作用域",
        ),
        sa.Column(
            "resource_key",
            sa.String(length=128),
            nullable=False,
            comment="scope 内资源键",
        ),
        sa.Column("id", sa.Uuid(), autoincrement=False, nullable=False),
        sa.Column("creator_id", sa.Uuid(), nullable=True),
        sa.Column("creator_type", sa.String(length=32), nullable=False),
        sa.Column("updater_id", sa.Uuid(), nullable=True),
        sa.Column("updater_type", sa.String(length=32), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "operation_scope",
            "resource_key",
            name="uk_scoped_op_lock_key",
        ),
        comment="通用事务互斥锁",
    )
    op.create_table(
        "user",
        sa.Column("username", sa.String(length=64), nullable=False, comment="用户名"),
        sa.Column("account", sa.String(length=64), nullable=False, comment="账号"),
        sa.Column("phone", sa.String(length=11), nullable=False, comment="手机号"),
        sa.Column(
            "password_hash",
            sa.String(length=255),
            nullable=True,
            comment="密码哈希",
        ),
        sa.Column("id", sa.Uuid(), autoincrement=False, nullable=False),
        sa.Column("creator_id", sa.Uuid(), nullable=True),
        sa.Column("creator_type", sa.String(length=32), nullable=False),
        sa.Column("updater_id", sa.Uuid(), nullable=True),
        sa.Column("updater_type", sa.String(length=32), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    """删除全部业务表和数据；仅用于可丢弃测试库的回退演练。"""
    op.drop_table("user")
    op.drop_table("scoped_operation_locks")
    op.drop_index("idx_external_identity_user_provider", table_name="external_identity")
    op.drop_table("external_identity")
    op.drop_index(
        "idx_celery_task_record_stale_submitting",
        table_name="celery_task_record",
        postgresql_where=sa.text("status = 'SUBMITTING'"),
    )
    op.drop_index("idx_celery_task_record_scope_trace", table_name="celery_task_record")
    op.drop_index(
        "idx_celery_task_record_scope_status",
        table_name="celery_task_record",
    )
    op.drop_index(
        "idx_celery_task_record_queued_deadline",
        table_name="celery_task_record",
        postgresql_where=sa.text("status = 'QUEUED'"),
    )
    op.drop_index(
        "idx_celery_task_record_orphan_fence",
        table_name="celery_task_record",
        postgresql_where=sa.text("status = 'ORPHANED'"),
    )
    op.drop_index(
        "idx_celery_task_record_execution_deadline",
        table_name="celery_task_record",
        postgresql_where=sa.text("status IN ('RUNNING', 'CANCELLING')"),
    )
    op.drop_table("celery_task_record")
    op.drop_index("idx_agent_session_user_status_updated", table_name="agent_session")
    op.drop_index("idx_agent_session_user_last_message", table_name="agent_session")
    op.drop_table("agent_session")
    op.drop_index("idx_agent_run_step_user_created", table_name="agent_run_step")
    op.drop_index("idx_agent_run_step_tool", table_name="agent_run_step")
    op.drop_index("idx_agent_run_step_session_created", table_name="agent_run_step")
    op.drop_table("agent_run_step")
    op.drop_index(
        "idx_agent_run_checkpoint_user_created",
        table_name="agent_run_checkpoint",
    )
    op.drop_table("agent_run_checkpoint")
    op.drop_table("agent_run_attempt")
    op.drop_index("idx_agent_run_user_created", table_name="agent_run")
    op.drop_index("idx_agent_run_trace_id", table_name="agent_run")
    op.drop_index("idx_agent_run_session_created", table_name="agent_run")
    op.drop_index("idx_agent_run_agent_status", table_name="agent_run")
    op.drop_table("agent_run")
    op.drop_index("idx_agent_message_user_created", table_name="agent_message")
    op.drop_index("idx_agent_message_session_created", table_name="agent_message")
    op.drop_index("idx_agent_message_run_id", table_name="agent_message")
    op.drop_table("agent_message")
    op.drop_index("idx_agent_audit_user_created", table_name="agent_audit")
    op.drop_index("idx_agent_audit_trace_id", table_name="agent_audit")
    op.drop_index("idx_agent_audit_agent_status", table_name="agent_audit")
    op.drop_table("agent_audit")
