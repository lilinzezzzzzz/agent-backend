from collections.abc import Mapping
from typing import Any
from uuid import UUID

from internal.agents.order.prompt import ORDER_SUPPORT_SYSTEM_PROMPT
from internal.agents.order.tools import (
    build_calculate_refund_amount_tool,
    build_get_order_status_tool,
    build_get_return_policy_tool,
    build_prepare_invoice_request_tool,
    build_search_order_knowledge_tool,
)
from internal.infra.llm import OpenAIClient
from internal.services.order import OrderService
from internal.services.rag import RagService
from pkg.agents import LLMReactActionMaker, ReActAgent, StructuredTool


class OrderAgentBuilder:
    """组装订单售后 ReActAgent 及其业务工具。"""

    def __init__(
        self,
        *,
        llm_client: OpenAIClient,
        order_service: OrderService,
        rag_service: RagService,
        user_id: UUID,
        max_steps: int,
        session_context: Mapping[str, Any] | None = None,
    ):
        self._llm_client = llm_client
        self._order_service = order_service
        self._rag_service = rag_service
        self._user_id = user_id
        self._max_steps = max_steps
        self._session_context = dict(session_context or {})

    def build(self) -> ReActAgent:
        """创建可运行的订单售后 ReActAgent。"""
        return ReActAgent(
            action_maker=LLMReactActionMaker(
                llm_client=self._llm_client,
                system_prompt=ORDER_SUPPORT_SYSTEM_PROMPT,
                session_context=self._session_context,
                extra_completion_kwargs={"thinking": False},
            ),
            tools=self.build_tools(),
            max_steps=self._max_steps,
            capture_tool_errors=False,
        )

    def build_tools(self) -> list[StructuredTool]:
        """组装订单售后 Agent 可调用的全部工具。"""
        return [
            build_get_order_status_tool(
                order_service=self._order_service,
                user_id=self._user_id,
            ),
            build_get_return_policy_tool(),
            build_search_order_knowledge_tool(
                rag_service=self._rag_service,
                user_id=self._user_id,
            ),
            build_calculate_refund_amount_tool(),
            build_prepare_invoice_request_tool(
                order_service=self._order_service,
                user_id=self._user_id,
            ),
        ]
