from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pkg.agents import StructuredTool


def build_get_supported_payment_methods_tool() -> StructuredTool:
    """创建支付方式查询工具。"""
    return StructuredTool(
        name="get_supported_payment_methods",
        description="查询当前示例支持的支付方式和使用边界。",
        parameters_schema={
            "type": "object",
            "properties": {},
        },
        handler=_get_supported_payment_methods,
    )


def build_search_payment_knowledge_tool() -> StructuredTool:
    """创建支付知识检索工具。"""
    return StructuredTool(
        name="search_payment_knowledge",
        description="检索支付失败、扣款异常、账单、分期和支付安全相关知识。",
        parameters_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "需要检索的支付业务问题",
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
        handler=_search_payment_knowledge,
    )


def build_calculate_payment_total_tool() -> StructuredTool:
    """创建支付金额计算工具。"""
    return StructuredTool(
        name="calculate_payment_total",
        description="精确计算应付金额；所有金额参数均使用整数分，避免浮点和大模型计算误差。",
        parameters_schema={
            "type": "object",
            "properties": {
                "item_amount_cents": {
                    "type": "integer",
                    "description": "商品实付小计，单位为分",
                    "minimum": 0,
                },
                "shipping_fee_cents": {
                    "type": "integer",
                    "description": "运费，单位为分，默认 0",
                    "minimum": 0,
                },
                "discount_cents": {
                    "type": "integer",
                    "description": "支付前优惠抵扣，单位为分，默认 0",
                    "minimum": 0,
                },
            },
            "required": ["item_amount_cents"],
        },
        handler=_calculate_payment_total,
    )


def _get_supported_payment_methods(_: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "methods": [
            {"code": "wechat_pay", "name": "微信支付", "status": "available"},
            {"code": "alipay", "name": "支付宝", "status": "available"},
            {"code": "bank_card", "name": "银行卡", "status": "available"},
        ],
        "side_effects_supported": False,
        "note": "当前 Agent 只提供支付咨询，不发起真实支付、扣款或退款动作。",
    }


def _search_payment_knowledge(args: Mapping[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if not query:
        return {"ok": False, "error": "query is required"}

    try:
        top_k = int(args.get("top_k") or 3)
    except (TypeError, ValueError):
        return {"ok": False, "error": "top_k must be an integer"}
    top_k = max(1, min(top_k, 5))

    # 模拟支付知识库检索：真实业务应替换为支付规则配置、支付网关状态和向量检索。
    knowledge_chunks: list[dict[str, Any]] = [
        {
            "id": "payment_failed_common_causes",
            "title": "付款失败常见原因",
            "content": "付款失败通常与余额不足、银行卡限额、渠道风控、网络超时或收银台订单过期有关。",
            "source": "支付帮助中心/付款失败",
            "keywords": ("支付失败", "付款失败", "付不了", "限额", "余额不足", "订单过期"),
        },
        {
            "id": "payment_duplicate_charge",
            "title": "重复扣款处理",
            "content": "如果出现重复扣款，系统通常会在支付渠道对账后自动原路退回，多数渠道会在 1-3 个工作日内完成。",
            "source": "支付帮助中心/扣款异常",
            "keywords": ("重复扣", "扣款", "多扣", "重复付款", "原路退回"),
        },
        {
            "id": "payment_installment_rules",
            "title": "分期支付规则",
            "content": "分期支付是否可用取决于订单金额、支付渠道、用户账户状态和渠道实时风控结果。",
            "source": "支付帮助中心/分期",
            "keywords": ("分期", "账单", "信用卡", "手续费", "期数"),
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


def _calculate_payment_total(args: Mapping[str, Any]) -> dict[str, Any]:
    item_amount_cents, error = _read_integer_arg(args, "item_amount_cents")
    if error:
        return {"ok": False, "error": error}
    shipping_fee_cents, error = _read_integer_arg(
        args, "shipping_fee_cents", default=0
    )
    if error:
        return {"ok": False, "error": error}
    discount_cents, error = _read_integer_arg(args, "discount_cents", default=0)
    if error:
        return {"ok": False, "error": error}

    gross_amount_cents = item_amount_cents + shipping_fee_cents
    if discount_cents > gross_amount_cents:
        return {
            "ok": False,
            "error": "discount_cents cannot exceed gross payment amount",
        }

    payable_amount_cents = gross_amount_cents - discount_cents
    return {
        "ok": True,
        "item_amount_cents": item_amount_cents,
        "shipping_fee_cents": shipping_fee_cents,
        "discount_cents": discount_cents,
        "payable_amount_cents": payable_amount_cents,
        "payable_amount_yuan": f"{payable_amount_cents / 100:.2f}",
    }


def _read_integer_arg(
    args: Mapping[str, Any], name: str, *, default: int | None = None
) -> tuple[int, str | None]:
    value = args.get(name, default)
    if value is None:
        return 0, f"{name} is required"
    if not isinstance(value, int) or isinstance(value, bool):
        return 0, f"{name} must be an integer"
    if value < 0:
        return 0, f"{name} must be non-negative"
    return value, None
