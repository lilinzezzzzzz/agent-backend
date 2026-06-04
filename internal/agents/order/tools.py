from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from internal.services.order import OrderService
from pkg.agents import StructuredTool


def build_get_order_status_tool(
    *, order_service: OrderService, user_id: int
) -> StructuredTool:
    """创建订单状态查询工具。"""

    async def get_order_status(args: Mapping[str, Any]) -> dict[str, Any]:
        order_id = str(args.get("order_id") or "").strip()
        if not order_id:
            return {"ok": False, "error": "order_id is required"}

        order = await order_service.get_order_status(user_id=user_id, order_id=order_id)
        if order is None:
            return {"ok": False, "error": "not_found", "order_id": order_id}
        return order.to_tool_result()

    return StructuredTool(
        name="get_order_status",
        description="按订单 ID 查询当前用户有权访问的订单物流和签收状态。",
        parameters_schema={
            "type": "object",
            "properties": {"order_id": {"type": "string", "description": "订单 ID"}},
            "required": ["order_id"],
        },
        handler=get_order_status,
    )


def build_get_return_policy_tool() -> StructuredTool:
    """创建退货规则查询工具。"""
    return StructuredTool(
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
    )


def build_search_order_knowledge_tool() -> StructuredTool:
    """创建订单知识检索工具。"""
    return StructuredTool(
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
    )


def build_calculate_refund_amount_tool() -> StructuredTool:
    """创建退款金额计算工具。"""
    return StructuredTool(
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
    )


def build_prepare_invoice_request_tool(
    *, order_service: OrderService, user_id: int
) -> StructuredTool:
    """创建开票申请确认准备工具。"""

    async def prepare_invoice_request(args: Mapping[str, Any]) -> dict[str, Any]:
        order_id = str(args.get("order_id") or "").strip()
        if not order_id:
            return {"ok": False, "error": "order_id is required"}

        order = await order_service.get_order_status(user_id=user_id, order_id=order_id)
        if order is None:
            return {"ok": False, "error": "not_found", "order_id": order_id}

        invoice_title = (
            str(args.get("invoice_title") or "personal").strip() or "personal"
        )
        confirmation = await order_service.prepare_invoice_request(
            user_id=user_id,
            order_id=order_id,
            invoice_title=invoice_title,
            tax_no=str(args.get("tax_no") or "").strip() or None,
            email=str(args.get("email") or "").strip() or None,
        )
        return {
            "ok": True,
            "status": "confirmation_required",
            "confirmation": confirmation.to_tool_result(),
        }

    return StructuredTool(
        name="prepare_invoice_request",
        description="准备订单开票申请并返回服务端确认 token；该工具不会提交开票任务。",
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
        handler=prepare_invoice_request,
    )


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
