from internal.agents import LLMReactDecisionMaker
from internal.agents.payment.prompt import PAYMENT_SUPPORT_SYSTEM_PROMPT
from internal.agents.payment.tools import (
    build_calculate_payment_total_tool,
    build_get_supported_payment_methods_tool,
    build_search_payment_knowledge_tool,
)
from internal.infra.llm import AgentLLMClient
from pkg.agents import ReActAgent, StructuredTool


class PaymentAgentBuilder:
    """组装支付支持 ReActAgent 及其业务工具。"""

    def __init__(
        self,
        *,
        llm_client: AgentLLMClient,
        max_steps: int,
    ):
        self._llm_client = llm_client
        self._max_steps = max_steps

    def build(self) -> ReActAgent:
        """创建可运行的支付支持 ReActAgent。"""
        return ReActAgent(
            decision_maker=LLMReactDecisionMaker(
                llm_client=self._llm_client,
                system_prompt=PAYMENT_SUPPORT_SYSTEM_PROMPT,
                extra_completion_kwargs={"thinking": False},
            ),
            tools=self.build_tools(),
            max_steps=self._max_steps,
            capture_tool_errors=False,
        )

    def build_tools(self) -> list[StructuredTool]:
        """组装支付支持 Agent 可调用的全部工具。"""
        return [
            build_get_supported_payment_methods_tool(),
            build_search_payment_knowledge_tool(),
            build_calculate_payment_total_tool(),
        ]
