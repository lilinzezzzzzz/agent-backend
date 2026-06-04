from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from internal.infra.llm import AgentLLMClient

AGENT_ROUTER_SYSTEM_PROMPT = """你是业务 Agent Router，只负责识别用户问题应交给哪个业务域。
你必须只输出约定的结构化结果，不回答用户问题。

路由规则：
- order：订单状态、物流、签收、退货、售后、退款金额、发票、开票相关问题。
- unsupported：不属于上述订单业务域的所有问题。

无法确定时选择 unsupported，禁止猜测或扩展当前支持范围。
"""


class AgentRoute(StrEnum):
    """统一 Agent Router 支持的业务域。"""

    ORDER = "order"
    UNSUPPORTED = "unsupported"


class AgentRouterDecisionModel(BaseModel):
    """LLM 返回的 Agent Router 决策。"""

    route: AgentRoute = Field(..., description="目标业务域")


class LLMAgentRouter:
    """使用结构化 LLM 输出识别业务域。"""

    def __init__(self, *, llm_client: AgentLLMClient):
        self._llm_client = llm_client

    async def route(self, *, question: str) -> AgentRoute:
        """识别问题所属业务域。"""
        decision = await self._llm_client.chat_completion_structured(
            messages=[
                {"role": "system", "content": AGENT_ROUTER_SYSTEM_PROMPT},
                {"role": "user", "content": question},
            ],
            response_model=AgentRouterDecisionModel,
            temperature=0,
            max_tokens=64,
            thinking=False,
        )
        return decision.route
