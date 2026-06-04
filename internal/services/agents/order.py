from __future__ import annotations

from internal.agents.order import OrderAgentBuilder
from internal.core import AppException, errors
from internal.infra.llm import AgentLLMClient, new_default_llm_client
from internal.services.dto.agent import AgentRunDTO
from pkg.logger import logger


class OrderAgentService:
    def __init__(self, *, llm_client: AgentLLMClient):
        self._llm_client = llm_client

    async def answer_order_support_question(
        self, *, question: str, max_steps: int = 4
    ) -> AgentRunDTO:
        """使用 ReActAgent 回答订单支持问题。"""
        agent = OrderAgentBuilder(
            llm_client=self._llm_client,
            max_steps=max_steps,
        ).build()
        try:
            result = await agent.run(user_input=question)
        except AppException:
            raise
        except Exception as exc:
            logger.warning(f"Order support agent failed: {type(exc).__name__}")
            raise AppException(
                errors.ServiceUnavailable, message="订单支持 Agent 暂不可用"
            ) from exc
        return AgentRunDTO.from_agent_result(result)


_order_agent_service: OrderAgentService | None = None


def new_order_agent_service() -> OrderAgentService:
    """依赖注入：获取 OrderAgentService 单例。"""
    global _order_agent_service
    if _order_agent_service is None:
        _order_agent_service = OrderAgentService(llm_client=new_default_llm_client())
    return _order_agent_service
