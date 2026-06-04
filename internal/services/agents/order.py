from __future__ import annotations

from internal.agents.order import OrderAgentBuilder
from internal.core import AppException, errors
from internal.infra.llm import AgentLLMClient, new_default_llm_client
from internal.services.dto.agent import AgentRunDTO
from internal.services.order import OrderService, new_order_service
from pkg.logger import logger


class OrderAgentService:
    def __init__(self, *, llm_client: AgentLLMClient, order_service: OrderService):
        self._llm_client = llm_client
        self._order_service = order_service

    async def answer_order_support_question(
        self,
        *,
        user_id: int,
        question: str,
        max_steps: int = 4,
        confirmation_token: str | None = None,
        idempotency_key: str | None = None,
    ) -> AgentRunDTO:
        """使用 ReActAgent 回答订单支持问题。"""
        try:
            if confirmation_token is not None:
                if idempotency_key is None:
                    raise AppException(errors.BadRequest, message="确认动作缺少幂等键")
                confirmed = await self._order_service.confirm_invoice_request(
                    user_id=user_id,
                    confirmation_token=confirmation_token,
                    idempotency_key=idempotency_key,
                )
                return AgentRunDTO.from_confirmed_invoice(confirmed)

            agent = OrderAgentBuilder(
                llm_client=self._llm_client,
                order_service=self._order_service,
                user_id=user_id,
                max_steps=max_steps,
            ).build()
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
        _order_agent_service = OrderAgentService(
            llm_client=new_default_llm_client(),
            order_service=new_order_service(),
        )
    return _order_agent_service
