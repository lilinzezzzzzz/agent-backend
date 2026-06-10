from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from internal.infra.llm import OpenAIClient

AGENT_ROUTER_SYSTEM_PROMPT = """你是业务 Agent Router，只负责识别用户问题应交给哪个业务域。
你必须只输出约定的结构化结果，不回答用户问题。

路由规则：
- order：订单状态、物流、签收、退货、售后、退款金额、发票、开票相关问题。
- payment：支付方式、付款失败、扣款、重复扣款、支付状态、支付安全、账单、分期相关问题。
- unsupported：不属于上述订单或支付业务域的所有问题。

无法确定时选择 unsupported，禁止猜测或扩展当前支持范围。
"""


class AgentRoute(StrEnum):
    """统一 Agent Router 支持的业务域。"""

    ORDER = "order"
    PAYMENT = "payment"
    UNSUPPORTED = "unsupported"


class AgentRouterActionModel(BaseModel):
    """LLM 返回的 Agent Router 路由动作。"""

    route: AgentRoute = Field(..., description="目标业务域")


class LLMAgentRouter:
    """使用结构化 LLM 输出识别业务域。"""

    def __init__(self, *, llm_client: OpenAIClient):
        self._llm_client = llm_client

    async def route(self, *, question: str) -> AgentRoute:
        """识别问题所属业务域。"""
        action = await self._llm_client.chat_completion_structured(
            messages=[
                {"role": "system", "content": AGENT_ROUTER_SYSTEM_PROMPT},
                {"role": "user", "content": question},
            ],
            response_model=AgentRouterActionModel,
            temperature=0,
            max_tokens=64,
            thinking=False,
        )
        return action.route


class RuleBasedAgentRouter:
    """使用确定性规则识别高置信业务域。"""

    _ORDER_KEYWORDS = (
        "订单",
        "物流",
        "快递",
        "签收",
        "退货",
        "售后",
        "退款金额",
        "退多少钱",
        "发票",
        "开票",
        "运单",
        "配送",
    )
    _PAYMENT_KEYWORDS = (
        "支付",
        "付款",
        "付不了",
        "扣款",
        "重复扣",
        "收银台",
        "支付方式",
        "银行卡",
        "信用卡",
        "微信支付",
        "支付宝",
        "余额",
        "分期",
        "账单",
        "支付状态",
    )
    _PAYMENT_STRONG_KEYWORDS = (
        "支付",
        "付款",
        "扣款",
        "重复扣",
        "收银台",
        "银行卡",
        "信用卡",
        "微信支付",
        "支付宝",
        "分期",
    )
    _NON_BUSINESS_KEYWORDS = (
        "写一首诗",
        "写诗",
        "讲个笑话",
        "天气",
        "新闻",
        "翻译",
        "代码",
        "编程",
        "数据库",
        "系统设计",
        "架构",
        "面试",
    )

    async def route(self, *, question: str) -> AgentRoute | None:
        """识别高置信业务域；无法确定时返回 None 交给 LLM。"""
        normalized = question.strip().lower()
        if not normalized:
            return AgentRoute.UNSUPPORTED

        if self._contains_any(normalized, self._NON_BUSINESS_KEYWORDS):
            return AgentRoute.UNSUPPORTED

        order_score = self._score(normalized, self._ORDER_KEYWORDS)
        payment_score = self._score(normalized, self._PAYMENT_KEYWORDS)
        if order_score == 0 and payment_score == 0:
            return None
        if payment_score > 0 and order_score == 0:
            return AgentRoute.PAYMENT
        if order_score > 0 and payment_score == 0:
            return AgentRoute.ORDER
        if self._contains_any(normalized, self._PAYMENT_STRONG_KEYWORDS):
            return AgentRoute.PAYMENT
        return None

    def _score(self, question: str, keywords: tuple[str, ...]) -> int:
        return sum(keyword in question for keyword in keywords)

    def _contains_any(self, question: str, keywords: tuple[str, ...]) -> bool:
        return any(keyword in question for keyword in keywords)


class HybridAgentRouter:
    """规则优先、LLM 兜底的业务域 Router。"""

    def __init__(self, *, llm_client: OpenAIClient):
        self._rule_router = RuleBasedAgentRouter()
        self._llm_router = LLMAgentRouter(llm_client=llm_client)

    async def route(self, *, question: str) -> AgentRoute:
        """先用确定性规则识别，高置信不足时再调用 LLM。"""
        rule_route = await self._rule_router.route(question=question)
        if rule_route is not None:
            return rule_route
        return await self._llm_router.route(question=question)
