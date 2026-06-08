from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from internal.services.rag import RagService
from pkg.agents import StructuredTool
from pkg.vectors.contracts import RetrievalMode


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


def build_search_payment_knowledge_tool(
    *, rag_service: RagService, user_id: int
) -> StructuredTool:
    """创建支付知识检索工具。"""

    async def search_payment_knowledge(args: Mapping[str, Any]) -> dict[str, Any]:
        return await _search_payment_knowledge(
            args,
            rag_service=rag_service,
            user_id=user_id,
        )

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
                "kb_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "可选知识库 ID 列表；最终范围仍由服务端 allowed scope 求交得到",
                },
            },
            "required": ["query"],
        },
        handler=search_payment_knowledge,
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


async def _search_payment_knowledge(
    args: Mapping[str, Any],
    *,
    rag_service: RagService,
    user_id: int,
) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if not query:
        return {"ok": False, "error": "query is required"}

    try:
        top_k = int(args.get("top_k") or 3)
    except (TypeError, ValueError):
        return {"ok": False, "error": "top_k must be an integer"}
    top_k = max(1, min(top_k, 5))

    kb_ids, error = _read_kb_ids(args)
    if error:
        return {"ok": False, "error": error}
    retrieval = await rag_service.retrieve(
        user_id=user_id,
        query=query,
        requested_domain="payment",
        requested_kb_ids=kb_ids,
        top_k=max(top_k, 5),
        final_k=top_k,
        retrieval_mode=RetrievalMode.HYBRID,
    )
    return retrieval.to_action_result()


def _read_kb_ids(args: Mapping[str, Any]) -> tuple[list[int] | None, str | None]:
    raw_value = args.get("kb_ids")
    if raw_value is None:
        return None, None
    if not isinstance(raw_value, list):
        return None, "kb_ids must be an array of integers"
    kb_ids: list[int] = []
    for item in raw_value:
        if not isinstance(item, int) or isinstance(item, bool) or item <= 0:
            return None, "kb_ids must be positive integers"
        kb_ids.append(item)
    return kb_ids, None


def _calculate_payment_total(args: Mapping[str, Any]) -> dict[str, Any]:
    item_amount_cents, error = _read_integer_arg(args, "item_amount_cents")
    if error:
        return {"ok": False, "error": error}
    shipping_fee_cents, error = _read_integer_arg(args, "shipping_fee_cents", default=0)
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
