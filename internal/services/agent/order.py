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

ORDER_SUPPORT_SYSTEM_PROMPT = """你是订单售后支持 Agent，负责帮助用户查询订单事实、理解规则、
检索帮助知识、精确计算退款金额和提交开票申请。你必须只输出约定的结构化 JSON 决策。

## 每轮决策流程
1. 结合用户问题、可用工具和 previous_steps 判断当前缺少什么信息。
2. 需要外部事实、业务规则、知识检索、精确计算或执行业务动作时，返回 tool_call。
3. 每轮只能返回一个决策：一个 tool_call，或者一个 final。
4. observation 足够回答用户时，返回 final；不要重复调用已经得到有效结果的工具。

## 工具路由
- 查询订单物流和签收状态：使用 `get_order_status`。
- 查询标准售后退货规则：使用 `get_return_policy`。
- 检索订单规则、处理流程和帮助知识：使用 `search_order_knowledge`。
- 精确计算退款金额、运费和优惠扣减：使用 `calculate_refund_amount`。
- 用户明确要求提交开票申请：使用 `submit_invoice_request`。

## 强制约束
- 凡涉及订单事实、业务规则、知识库内容、精确金额计算或开票申请，必须先调用对应工具，
  不能根据模型自身知识直接回答。
- 缺少工具必填参数时，返回 final 向用户询问缺失信息；禁止猜测或编造参数。
- 仅当用户明确要求提交开票申请时，才能调用 `submit_invoice_request`；
  用户只是咨询发票规则或开票流程时，不得提交申请。
- 涉及退款金额、运费和优惠扣减时，必须使用 `calculate_refund_amount`，不能自行计算；
  传入该工具的金额单位必须是整数分。
- 回答规则、流程和帮助类问题时，优先依据 `search_order_knowledge` 返回的知识片段。
- 工具返回 error 或 not_found 时必须如实说明，不得伪造成功结果。
- `submit_invoice_request` 返回 queued 只表示申请已提交，不代表发票已经开具完成。

## 可直接回答的场景
仅当用户问候、感谢、要求解释已有回答，或问题不依赖订单事实和业务规则时，
才可以不调用工具并直接返回 final。

## 最终回答要求
- 使用简洁、清晰的中文回答。
- 只使用用户已提供的信息和 observation 中的事实，不得编造订单状态、金额或处理结果。
- 如果任务状态为 queued，明确告知用户任务已提交、仍在处理中。
"""


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
                name="search_order_knowledge",
                description="检索订单、物流、售后和发票相关知识，返回可用于回答用户的知识片段和来源。",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "需要检索的订单业务问题",
                        },
                        "top_k": {
                            "type": "integer",
                            "description": "最多返回的知识片段数量，默认 3",
                            "minimum": 1,
                            "maximum": 5,
                        },
                    },
                    "required": ["query"],
                },
                handler=_search_order_knowledge,
            ),
            StructuredTool(
                name="calculate_refund_amount",
                description="精确计算商品退款金额；所有金额参数均使用整数分，避免浮点和大模型计算误差。",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "unit_price_cents": {
                            "type": "integer",
                            "description": "单件商品实付金额，单位为分",
                            "minimum": 0,
                        },
                        "quantity": {
                            "type": "integer",
                            "description": "退款商品数量",
                            "minimum": 1,
                        },
                        "refundable_shipping_fee_cents": {
                            "type": "integer",
                            "description": "可退运费，单位为分，默认 0",
                            "minimum": 0,
                        },
                        "discount_deduction_cents": {
                            "type": "integer",
                            "description": "退款时需要扣回的优惠金额，单位为分，默认 0",
                            "minimum": 0,
                        },
                    },
                    "required": ["unit_price_cents", "quantity"],
                },
                handler=_calculate_refund_amount,
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


def _search_order_knowledge(args: Mapping[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if not query:
        return {"ok": False, "error": "query is required"}

    try:
        top_k = int(args.get("top_k") or 3)
    except (TypeError, ValueError):
        return {"ok": False, "error": "top_k must be an integer"}
    top_k = max(1, min(top_k, 5))

    # 模拟 RAG 知识库检索：真实业务应替换为文档切片、Embedding 和向量数据库检索。
    knowledge_chunks: list[dict[str, Any]] = [
        {
            "id": "order_invoice_guide",
            "title": "电子发票开具与通知",
            "content": (
                "开票申请提交后通常会在 1-3 个工作日内完成。电子发票开具完成后，"
                "系统会通过站内信通知用户，并发送到申请时填写的邮箱。"
            ),
            "source": "订单帮助中心/发票服务",
            "keywords": ("发票", "开票", "电子发票", "税号", "抬头", "邮箱"),
        },
        {
            "id": "order_logistics_exception",
            "title": "物流长时间未更新处理方式",
            "content": (
                "物流轨迹超过 48 小时未更新时，可以提交物流核查。核查期间订单状态"
                "不会自动变更，处理结果会通过站内信通知用户。"
            ),
            "source": "订单帮助中心/物流服务",
            "keywords": ("物流", "轨迹", "未更新", "运输", "快递", "核查"),
        },
        {
            "id": "order_return_process",
            "title": "订单退货处理流程",
            "content": (
                "用户提交退货申请后，需要按页面提示寄回商品。仓库验收通过后进入退款流程，"
                "实际到账时间取决于原支付渠道。"
            ),
            "source": "订单帮助中心/售后服务",
            "keywords": ("退货", "售后", "退款", "寄回", "验收", "到账"),
        },
    ]

    matches = [
        {
            "id": chunk["id"],
            "title": chunk["title"],
            "content": chunk["content"],
            "source": chunk["source"],
            "score": score,
        }
        for chunk in knowledge_chunks
        if (score := sum(keyword in query for keyword in chunk["keywords"])) > 0
    ]
    matches.sort(key=lambda item: item["score"], reverse=True)
    matches = matches[:top_k]

    return {
        "ok": True,
        "query": query,
        "matches": matches,
        "total": len(matches),
    }


def _calculate_refund_amount(args: Mapping[str, Any]) -> dict[str, Any]:
    unit_price_cents, error = _read_integer_arg(args, "unit_price_cents")
    if error:
        return {"ok": False, "error": error}
    quantity, error = _read_integer_arg(args, "quantity")
    if error:
        return {"ok": False, "error": error}
    refundable_shipping_fee_cents, error = _read_integer_arg(
        args, "refundable_shipping_fee_cents", default=0
    )
    if error:
        return {"ok": False, "error": error}
    discount_deduction_cents, error = _read_integer_arg(
        args, "discount_deduction_cents", default=0
    )
    if error:
        return {"ok": False, "error": error}

    if unit_price_cents < 0:
        return {
            "ok": False,
            "error": "unit_price_cents must be greater than or equal to 0",
        }
    if quantity < 1:
        return {"ok": False, "error": "quantity must be greater than 0"}
    if refundable_shipping_fee_cents < 0:
        return {
            "ok": False,
            "error": "refundable_shipping_fee_cents must be greater than or equal to 0",
        }
    if discount_deduction_cents < 0:
        return {
            "ok": False,
            "error": "discount_deduction_cents must be greater than or equal to 0",
        }

    item_subtotal_cents = unit_price_cents * quantity
    gross_refund_cents = item_subtotal_cents + refundable_shipping_fee_cents
    if discount_deduction_cents > gross_refund_cents:
        return {
            "ok": False,
            "error": "discount_deduction_cents cannot exceed gross refund amount",
        }

    refund_amount_cents = gross_refund_cents - discount_deduction_cents
    refund_yuan, refund_remaining_cents = divmod(refund_amount_cents, 100)
    return {
        "ok": True,
        "unit_price_cents": unit_price_cents,
        "quantity": quantity,
        "item_subtotal_cents": item_subtotal_cents,
        "refundable_shipping_fee_cents": refundable_shipping_fee_cents,
        "discount_deduction_cents": discount_deduction_cents,
        "refund_amount_cents": refund_amount_cents,
        "refund_amount_yuan": f"{refund_yuan}.{refund_remaining_cents:02d}",
    }


def _read_integer_arg(
    args: Mapping[str, Any], name: str, *, default: int | None = None
) -> tuple[int, str | None]:
    if name not in args:
        if default is None:
            return 0, f"{name} is required"
        return default, None

    value = args[name]
    if isinstance(value, bool) or not isinstance(value, int):
        return 0, f"{name} must be an integer"
    return value, None


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
