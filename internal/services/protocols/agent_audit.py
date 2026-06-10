from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol

from internal.schemas.agent import JsonValue
from internal.services.dto.agent import AgentRunResultDTO


class AgentAuditWriter(Protocol):
    """Agent 审计写入协议，便于业务 Service 测试替换。"""

    async def record_agent_run(
        self,
        *,
        agent_name: str,
        user_id: int,
        trace_id: str | None,
        user_input: str,
        max_steps: int,
        result: AgentRunResultDTO,
        started_at: datetime,
        ended_at: datetime,
        llm_calls: Sequence[Mapping[str, JsonValue]],
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> bool: ...
