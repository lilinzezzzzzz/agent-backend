"""业务 Agent 定义注册表。

恢复只允许加载与 checkpoint 中 `definition_version` 兼容的 Builder；版本缺失或未知时
必须拒绝恢复，不能静默切换到新的 prompt、工具集合或序列化规则。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from internal.agents.order import OrderAgentBuilder
from internal.agents.payment import PaymentAgentBuilder
from internal.core import AppException, errors
from internal.infra.llm import OpenAIResponsesClient
from internal.services.order import OrderService
from internal.services.rag import RagService
from pkg.agents import ReActAgent, StructuredTool

AGENT_DEFINITION_VERSION = "v1"
"""当前 Builder、prompt、工具 schema/语义和序列化规则的兼容版本。"""

ORDER_SUPPORT_AGENT = "order_support"
PAYMENT_SUPPORT_AGENT = "payment_support"
AGENT_CHAT_ROUTER = "agent_chat"

SUPPORTED_AGENT_NAMES = frozenset({ORDER_SUPPORT_AGENT, PAYMENT_SUPPORT_AGENT})
"""可以恢复执行的业务 Agent；`agent_chat` 只负责路由。"""


class AgentDefinitionRegistry:
    """按显式 `definition_version` 组装业务 Agent 与工具集合。"""

    def __init__(
        self,
        *,
        llm_client: OpenAIResponsesClient,
        order_service: OrderService,
        rag_service: RagService,
        definition_version: str = AGENT_DEFINITION_VERSION,
    ):
        self._llm_client = llm_client
        self._order_service = order_service
        self._rag_service = rag_service
        self._definition_version = definition_version

    @property
    def definition_version(self) -> str:
        """当前注册表支持的 definition_version。"""
        return self._definition_version

    def supports(self, definition_version: str) -> bool:
        """判断给定版本是否与当前 Builder 兼容。"""
        return definition_version == self._definition_version

    def require_supported(self, definition_version: str) -> None:
        """版本不兼容时抛出明确错误，拒绝静默降级。"""
        if not self.supports(definition_version):
            raise AppException(
                errors.AgentDefinitionIncompatible,
                message=(
                    f"当前仅支持 definition_version={self._definition_version}，"
                    f"checkpoint 为 {definition_version}"
                ),
            )

    def build_agent(
        self,
        *,
        agent_name: str,
        definition_version: str,
        user_id: UUID,
        max_steps: int,
        session_context: Mapping[str, Any] | None,
    ) -> ReActAgent:
        """按已冻结的版本与上下文组装可执行 Agent。"""
        self.require_supported(definition_version)
        if agent_name == ORDER_SUPPORT_AGENT:
            return OrderAgentBuilder(
                llm_client=self._llm_client,
                order_service=self._order_service,
                rag_service=self._rag_service,
                user_id=user_id,
                max_steps=max_steps,
                session_context=session_context,
                definition_version=definition_version,
            ).build()
        if agent_name == PAYMENT_SUPPORT_AGENT:
            return PaymentAgentBuilder(
                llm_client=self._llm_client,
                rag_service=self._rag_service,
                user_id=user_id,
                max_steps=max_steps,
                session_context=session_context,
                definition_version=definition_version,
            ).build()
        raise AppException(
            errors.AgentDefinitionIncompatible,
            message=f"未知的业务 Agent: {agent_name}",
        )

    def resolve_tools(
        self, *, agent_name: str, definition_version: str, user_id: UUID
    ) -> dict[str, StructuredTool]:
        """解析指定 Agent 的工具集合，用于恢复前的重放策略校验。"""
        self.require_supported(definition_version)
        if agent_name == ORDER_SUPPORT_AGENT:
            tools = OrderAgentBuilder(
                llm_client=self._llm_client,
                order_service=self._order_service,
                rag_service=self._rag_service,
                user_id=user_id,
                max_steps=1,
            ).build_tools()
        elif agent_name == PAYMENT_SUPPORT_AGENT:
            tools = PaymentAgentBuilder(
                llm_client=self._llm_client,
                rag_service=self._rag_service,
                user_id=user_id,
                max_steps=1,
            ).build_tools()
        else:
            raise AppException(
                errors.AgentDefinitionIncompatible,
                message=f"未知的业务 Agent: {agent_name}",
            )
        return {tool.name: tool for tool in tools}


__all__ = [
    "AGENT_CHAT_ROUTER",
    "AGENT_DEFINITION_VERSION",
    "ORDER_SUPPORT_AGENT",
    "PAYMENT_SUPPORT_AGENT",
    "SUPPORTED_AGENT_NAMES",
    "AgentDefinitionRegistry",
]
