"""应用 ORM 模型注册入口，供 Alembic 收集完整的表结构。"""

from internal.models.agent_audit import AgentAudit
from internal.models.agent_conversation import (
    AgentMessage,
    AgentRun,
    AgentRunAttempt,
    AgentRunCheckpoint,
    AgentRunStep,
    AgentSession,
)
from internal.models.celery_task import CeleryTaskRecord
from internal.models.external_identity import ExternalIdentity
from internal.models.scoped_operation_lock import ScopedOperationLock
from internal.models.user import User
from pkg.database.base import Base

__all__ = [
    "Base",
    "AgentAudit",
    "AgentMessage",
    "AgentRun",
    "AgentRunAttempt",
    "AgentRunCheckpoint",
    "AgentRunStep",
    "AgentSession",
    "CeleryTaskRecord",
    "ExternalIdentity",
    "ScopedOperationLock",
    "User",
]
