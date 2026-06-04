from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OrderStatusDTO:
    """用户可访问的订单状态。"""

    order_id: str
    status: str
    carrier: str
    tracking_no: str
    eta: str

    def to_tool_result(self) -> dict[str, object]:
        """转换为 Agent 工具 observation。"""
        return {
            "ok": True,
            "order_id": self.order_id,
            "status": self.status,
            "carrier": self.carrier,
            "tracking_no": self.tracking_no,
            "eta": self.eta,
        }


@dataclass(frozen=True, slots=True)
class AgentActionConfirmationDTO:
    """需要用户显式确认的服务端可信动作。"""

    token: str
    action: str
    summary: str
    expires_in_seconds: int

    def to_tool_result(self) -> dict[str, object]:
        """转换为 Agent 工具 observation。"""
        return {
            "token": self.token,
            "action": self.action,
            "summary": self.summary,
            "expires_in_seconds": self.expires_in_seconds,
        }


@dataclass(frozen=True, slots=True)
class InvoiceRequestDTO:
    """已受理的开票申请。"""

    confirmation_token: str
    order_id: str
    invoice_title: str
    tax_no: str | None
    email: str | None
    trace_id: str
    task_id: str
    status: str = "queued"
    message: str = "开票申请已提交，开具完成后会通知用户"

    def to_tool_result(self) -> dict[str, object]:
        """转换为 Agent 执行 observation。"""
        return {
            "ok": True,
            "order_id": self.order_id,
            "invoice_title": self.invoice_title,
            "tax_no": self.tax_no,
            "email": self.email,
            "trace_id": self.trace_id,
            "task_id": self.task_id,
            "status": self.status,
            "message": self.message,
        }
