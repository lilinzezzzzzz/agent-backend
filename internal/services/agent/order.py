from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from internal.core import AppException, errors
from internal.infra.llm import AgentLLMClient, new_default_llm_client
from internal.services.dto.agent import AgentRunDTO
from internal.utils.agents import LLMReactDecisionMaker
from pkg.agents import (
    AgentDecisionMaker,
    ReActAgent,
    StructuredTool,
)
from pkg.logger import logger

ORDER_SUPPORT_SYSTEM_PROMPT = (
    "你是订单售后支持 Agent。你必须只输出结构化 JSON。"
    "如果需要订单状态或退货政策，先返回 tool_call；"
    "当 observation 足够回答用户时，返回 final。"
    "不要编造订单状态；工具返回 not_found 时如实说明。"
)


class OrderAgentService:
    def __init__(self, *, llm_client: AgentLLMClient):
        self._llm_client = llm_client

    async def answer_order_support_question(
        self, *, question: str, max_steps: int = 4
    ) -> AgentRunDTO:
        """使用 ReActAgent 回答订单支持问题。"""
        agent = ReActAgent(
            decision_maker=self._build_decision_maker(),
            tools=self._build_tools(),
            max_steps=max_steps,
        )
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

    def _build_decision_maker(self) -> AgentDecisionMaker:
        return LLMReactDecisionMaker(
            llm_client=self._llm_client,
            system_prompt=ORDER_SUPPORT_SYSTEM_PROMPT,
            extra_completion_kwargs={"thinking": False},
        )

    @staticmethod
    def _build_tools() -> list[StructuredTool]:
        return [
            StructuredTool(
                name="get_order_status",
                description="按订单 ID 查询订单物流和签收状态。",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "order_id": {"type": "string", "description": "订单 ID"}
                    },
                    "required": ["order_id"],
                },
                handler=_get_order_status,
            ),
            StructuredTool(
                name="get_return_policy",
                description="查询订单售后退货规则。",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "category": {
                            "type": "string",
                            "description": "商品类目，可选；未知时传 general",
                        }
                    },
                },
                handler=_get_return_policy,
            ),
        ]


def _get_order_status(args: Mapping[str, Any]) -> dict[str, Any]:
    order_id = str(args.get("order_id") or "").strip()
    if not order_id:
        return {"ok": False, "error": "order_id is required"}

    # 模拟数据库查询：真实业务应替换为订单 DAO 或订单 Service 查询。
    order_fixtures: dict[str, dict[str, str]] = {
        "1001": {
            "status": "运输中",
            "carrier": "顺丰速运",
            "tracking_no": "SF10010001",
            "eta": "明天 18:00 前",
        },
        "1002": {
            "status": "已签收",
            "carrier": "京东物流",
            "tracking_no": "JD10020002",
            "eta": "已于今天 10:24 签收",
        },
    }
    order = order_fixtures.get(order_id)
    if order is None:
        return {"ok": False, "error": "not_found", "order_id": order_id}

    return {"ok": True, "order_id": order_id, **order}


def _get_return_policy(args: Mapping[str, Any]) -> dict[str, Any]:
    category = str(args.get("category") or "general").strip() or "general"
    # 模拟售后政策查询：真实业务应替换为售后规则配置、配置中心或售后 Service 查询。
    return_policy = {
        "window": "签收后 7 天内可申请无理由退货",
        "condition": "商品需保持完好，不影响二次销售",
        "shipping_fee": "质量问题由商家承担运费，非质量问题由用户承担运费",
    }
    return {"ok": True, "category": category, **return_policy}


_order_agent_service: OrderAgentService | None = None


def new_order_agent_service() -> OrderAgentService:
    """依赖注入：获取 OrderAgentService 单例。"""
    global _order_agent_service
    if _order_agent_service is None:
        _order_agent_service = OrderAgentService(llm_client=new_default_llm_client())
    return _order_agent_service
