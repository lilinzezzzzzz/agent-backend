from __future__ import annotations

from functools import cache

from internal.infra.database import get_read_session, get_session
from internal.models.agent_audit import AgentAudit
from pkg.database.dao import BaseDao


class AgentAuditDao(BaseDao[AgentAudit]):
    """Agent 运行审计 DAO。"""

    _model_cls: type[AgentAudit] = AgentAudit

    async def get_by_run_id(self, run_id: str) -> AgentAudit | None:
        """按 run_id 查询审计记录。"""
        statement = self.select_stmt().where(self.model_cls.run_id == run_id)
        return await self.fetch_first(statement)


@cache
def new_agent_audit_dao() -> AgentAuditDao:
    """依赖注入：获取 AgentAuditDao 单例。"""
    return AgentAuditDao(
        session_provider=get_session,
        read_session_provider=get_read_session,
    )
