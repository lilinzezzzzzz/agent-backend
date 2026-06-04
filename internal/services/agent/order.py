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
from pkg.toolkit import context
from pkg.toolkit.string import uuid6_unique_str_id

ORDER_SUPPORT_SYSTEM_PROMPT = (
    "你是订单售后支持 Agent。你必须只输出结构化 JSON。"
    "如果需要订单状态、退货政策或提交开票申请，先返回 tool_call；"
    "当 observation 足够回答用户时，返回 final。"
    "不要编造订单状态；工具返回 not_found 时如实说明。"
    "开票工具只表示申请已提交，不能回答发票已经开具完成。"
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
            StructuredTool(
                name="submit_invoice_request",
                description="提交订单开票申请；该工具只负责受理申请并触发异步开票任务，不等待开票完成。",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "order_id": {"type": "string", "description": "订单 ID"},
                        "invoice_title": {
                            "type": "string",
                            "description": "发票抬头；未知时传 personal",
                        },
                        "tax_no": {
                            "type": "string",
                            "description": "企业税号；个人发票可省略",
                        },
                        "email": {
                            "type": "string",
                            "description": "接收电子发票的邮箱；未知时可省略",
                        },
                    },
                    "required": ["order_id"],
                },
                handler=_submit_invoice_request,
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


def _submit_invoice_request(args: Mapping[str, Any]) -> dict[str, Any]:
    order_id = str(args.get("order_id") or "").strip()
    if not order_id:
        return {"ok": False, "error": "order_id is required"}

    order_status = _get_order_status({"order_id": order_id})
    if not order_status.get("ok"):
        return {"ok": False, "error": "order_not_found", "order_id": order_id}

    invoice_title = str(args.get("invoice_title") or "personal").strip() or "personal"
    tax_no = str(args.get("tax_no") or "").strip()
    email = str(args.get("email") or "").strip()
    trace_id = context.get_trace_id()

    # 模拟异步任务入队：真实业务应写入开票申请记录并投递 Celery / MQ 任务。
    return {
        "ok": True,
        "order_id": order_id,
        "invoice_title": invoice_title,
        "tax_no": tax_no or None,
        "email": email or None,
        "trace_id": trace_id,
        "task_id": f"task_{uuid6_unique_str_id()}",
        "status": "queued",
        "message": "开票申请已提交，开具完成后会通知用户",
    }


_order_agent_service: OrderAgentService | None = None


def new_order_agent_service() -> OrderAgentService:
    """依赖注入：获取 OrderAgentService 单例。"""
    global _order_agent_service
    if _order_agent_service is None:
        _order_agent_service = OrderAgentService(llm_client=new_default_llm_client())
    return _order_agent_service
