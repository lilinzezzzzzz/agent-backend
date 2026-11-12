"""Agent 会话用例入口，数据库事务由 storage 模块统一持有。"""

from __future__ import annotations

from functools import cache

from internal.schemas.agent import (
    AgentConversationContextDTO,
    AgentRunStartDTO,
)
from internal.services.agents.storage import (
    DatabaseAgentStorageBackend,
    new_database_agent_storage_backend,
)


class AgentConversationService:
    """Agent 会话存储用例服务。"""

    def __init__(self, *, storage_backend: DatabaseAgentStorageBackend):
        self._storage_backend = storage_backend

    async def start_run(self, **kwargs) -> AgentRunStartDTO:
        """创建或复用会话，并创建 running run。"""
        return await self._storage_backend.start_run(**kwargs)

    async def load_context(self, **kwargs) -> AgentConversationContextDTO:
        """读取会话上下文窗口。"""
        return await self._storage_backend.load_context(**kwargs)

    async def complete_run(self, **kwargs) -> None:
        """标记 run 完成并写入最终消息与步骤。"""
        await self._storage_backend.complete_run(**kwargs)

    async def fail_run(self, **kwargs) -> None:
        """标记 run 失败。"""
        await self._storage_backend.fail_run(**kwargs)


@cache
def new_agent_conversation_service() -> AgentConversationService:
    """依赖注入：获取 AgentConversationService 单例。"""
    return AgentConversationService(
        storage_backend=new_database_agent_storage_backend(),
    )
