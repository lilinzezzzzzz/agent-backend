from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from internal.cache import new_agent_action_cache
from internal.config import settings
from internal.core import AppException, errors
from internal.services.dto.order import (
    AgentActionConfirmationDTO,
    InvoiceRequestDTO,
    OrderStatusDTO,
)
from pkg.toolkit import context
from pkg.toolkit.string import uuid6_unique_str_id

INVOICE_REQUEST_ACTION = "submit_invoice_request"


class AgentActionStore(Protocol):
    """OrderService 使用的 Agent 动作状态存储协议。"""

    async def save_pending_action(
        self, *, token: str, payload: dict[str, object], expires_in_seconds: int
    ) -> bool: ...

    async def get_pending_action(self, *, token: str) -> dict[str, object] | None: ...

    async def delete_pending_action(self, *, token: str) -> int: ...

    async def save_confirmation_result(
        self, *, token: str, payload: dict[str, object], expires_in_seconds: int
    ) -> bool: ...

    async def get_confirmation_result(
        self, *, token: str
    ) -> dict[str, object] | None: ...

    async def save_idempotency_result(
        self,
        *,
        user_id: int,
        idempotency_key: str,
        payload: dict[str, object],
        expires_in_seconds: int,
    ) -> bool: ...

    async def get_idempotency_result(
        self, *, user_id: int, idempotency_key: str
    ) -> dict[str, object] | None: ...

    async def acquire_confirmation_lock(self, *, token: str) -> str: ...

    async def release_confirmation_lock(
        self, *, token: str, identifier: str
    ) -> bool: ...


class OrderService:
    """提供订单查询和需要显式确认的订单写操作。"""

    def __init__(
        self,
        *,
        action_store: AgentActionStore,
        confirmation_seconds: int,
        idempotency_seconds: int,
    ):
        self._action_store = action_store
        self._confirmation_seconds = confirmation_seconds
        self._idempotency_seconds = idempotency_seconds

    async def get_order_status(
        self, *, user_id: int, order_id: str
    ) -> OrderStatusDTO | None:
        """查询用户有权访问的订单状态。"""
        order = _ORDER_FIXTURES.get(order_id)
        if order is None or order["owner_user_id"] != user_id:
            return None
        return OrderStatusDTO(
            order_id=order_id,
            status=str(order["status"]),
            carrier=str(order["carrier"]),
            tracking_no=str(order["tracking_no"]),
            eta=str(order["eta"]),
        )

    async def prepare_invoice_request(
        self,
        *,
        user_id: int,
        order_id: str,
        invoice_title: str,
        tax_no: str | None,
        email: str | None,
    ) -> AgentActionConfirmationDTO:
        """保存待确认开票动作，不执行真实业务提交。

        这里的 Redis pending action 是可丢弃的临时副作用，用于冻结 LLM 提取出的动作摘要。
        真正开票受理必须走 confirm_invoice_request。
        """
        invoice_title = invoice_title.strip()
        tax_no = tax_no.strip() if tax_no else None
        email = email.strip() if email else None
        if not invoice_title or len(invoice_title) > 120:
            raise AppException(
                errors.BadRequest, message="发票抬头不能为空且不能超过 120 个字符"
            )
        if tax_no is not None and len(tax_no) > 64:
            raise AppException(errors.BadRequest, message="企业税号不能超过 64 个字符")
        if email is not None and (len(email) > 254 or "@" not in email):
            raise AppException(errors.BadRequest, message="电子邮箱格式无效")
        if await self.get_order_status(user_id=user_id, order_id=order_id) is None:
            raise AppException(errors.NotFound, message="订单不存在或无权访问")

        token = uuid6_unique_str_id()
        payload: dict[str, object] = {
            "action": INVOICE_REQUEST_ACTION,
            "user_id": user_id,
            "order_id": order_id,
            "invoice_title": invoice_title,
            "tax_no": tax_no,
            "email": email,
        }
        saved = await self._action_store.save_pending_action(
            token=token,
            payload=payload,
            expires_in_seconds=self._confirmation_seconds,
        )
        if not saved:
            raise AppException(
                errors.ServiceUnavailable, message="无法保存待确认的开票申请"
            )

        return AgentActionConfirmationDTO(
            token=token,
            action=INVOICE_REQUEST_ACTION,
            summary=_build_invoice_confirmation_summary(
                order_id=order_id,
                invoice_title=invoice_title,
                tax_no=tax_no,
                email=email,
            ),
            expires_in_seconds=self._confirmation_seconds,
        )

    async def confirm_invoice_request(
        self,
        *,
        user_id: int,
        confirmation_token: str,
        idempotency_key: str,
    ) -> InvoiceRequestDTO:
        """确认并幂等提交开票申请。

        confirmation_token 是服务端 pending action 的索引和锁粒度；idempotency_key
        只表达客户端确认请求的重试身份。确认阶段不信任 LLM 或客户端重传的业务参数，
        必须读取服务端保存的 pending action 后执行真实业务副作用。
        """
        if not confirmation_token or len(confirmation_token) > 128:
            raise AppException(errors.BadRequest, message="确认 token 无效")
        if not 8 <= len(idempotency_key) <= 128:
            raise AppException(
                errors.BadRequest, message="幂等键长度必须在 8 到 128 个字符之间"
            )
        lock_identifier = await self._action_store.acquire_confirmation_lock(
            token=confirmation_token,
        )
        try:
            # 先检查 token 级确认结果，保证同一个待确认动作不能换 idempotency_key
            # 再执行一次；_invoice_request_from_payload 会校验 key 是否一致。
            confirmation_result = await self._action_store.get_confirmation_result(
                token=confirmation_token
            )
            if confirmation_result is not None:
                return _invoice_request_from_payload(
                    confirmation_result,
                    expected_user_id=user_id,
                    expected_confirmation_token=confirmation_token,
                    expected_idempotency_key=idempotency_key,
                )

            # 再检查客户端重试键，覆盖同一次确认请求的网络重试或网关重放。
            existing = await self._action_store.get_idempotency_result(
                user_id=user_id,
                idempotency_key=idempotency_key,
            )
            if existing is not None:
                return _invoice_request_from_payload(
                    existing,
                    expected_user_id=user_id,
                    expected_confirmation_token=confirmation_token,
                    expected_idempotency_key=idempotency_key,
                )

            pending = await self._action_store.get_pending_action(
                token=confirmation_token
            )
            if pending is None:
                raise AppException(
                    errors.BadRequest, message="确认 token 无效、已过期或已使用"
                )
            # pending action 是副作用动作的唯一可信来源，用户和动作类型也在这里校验。
            _validate_pending_invoice_action(pending=pending, user_id=user_id)

            order_id = _read_required_str(pending, "order_id")
            if await self.get_order_status(user_id=user_id, order_id=order_id) is None:
                raise AppException(errors.Forbidden, message="订单不存在或无权访问")

            result = InvoiceRequestDTO(
                confirmation_token=confirmation_token,
                order_id=order_id,
                invoice_title=_read_required_str(pending, "invoice_title"),
                tax_no=_read_optional_str(pending, "tax_no"),
                email=_read_optional_str(pending, "email"),
                trace_id=context.get_trace_id(),
                task_id=f"task_{uuid6_unique_str_id()}",
            )
            result_payload: dict[str, object] = {
                "user_id": user_id,
                "idempotency_key": idempotency_key,
                "confirmation_token": result.confirmation_token,
                **result.to_action_result(),
            }
            saved = await self._action_store.save_confirmation_result(
                token=confirmation_token,
                payload=result_payload,
                expires_in_seconds=self._idempotency_seconds,
            )
            if not saved:
                raise AppException(
                    errors.ServiceUnavailable, message="无法保存开票申请确认结果"
                )
            saved = await self._action_store.save_idempotency_result(
                user_id=user_id,
                idempotency_key=idempotency_key,
                payload=result_payload,
                expires_in_seconds=self._idempotency_seconds,
            )
            if not saved:
                raise AppException(
                    errors.ServiceUnavailable, message="无法保存开票申请幂等结果"
                )
            await self._action_store.delete_pending_action(token=confirmation_token)
            return result
        finally:
            await self._action_store.release_confirmation_lock(
                token=confirmation_token,
                identifier=lock_identifier,
            )


def _validate_pending_invoice_action(
    *, pending: Mapping[str, object], user_id: int
) -> None:
    if pending.get("action") != INVOICE_REQUEST_ACTION:
        raise AppException(errors.BadRequest, message="确认 token 对应的动作不受支持")
    if pending.get("user_id") != user_id:
        raise AppException(errors.Forbidden, message="确认 token 不属于当前用户")


def _build_invoice_confirmation_summary(
    *,
    order_id: str,
    invoice_title: str,
    tax_no: str | None,
    email: str | None,
) -> str:
    parts = [f"为订单 {order_id} 提交开票申请", f"抬头：{invoice_title}"]
    if tax_no:
        parts.append(f"税号：{tax_no}")
    if email:
        parts.append(f"接收邮箱：{email}")
    return "；".join(parts)


def _invoice_request_from_payload(
    payload: Mapping[str, object],
    *,
    expected_user_id: int,
    expected_confirmation_token: str,
    expected_idempotency_key: str,
) -> InvoiceRequestDTO:
    if payload.get("user_id") != expected_user_id:
        raise AppException(errors.Forbidden, message="确认结果不属于当前用户")
    confirmation_token = _read_required_str(payload, "confirmation_token")
    if confirmation_token != expected_confirmation_token:
        raise AppException(errors.BadRequest, message="幂等键已用于其他确认动作")
    if _read_required_str(payload, "idempotency_key") != expected_idempotency_key:
        raise AppException(errors.BadRequest, message="确认动作已使用其他幂等键执行")
    return InvoiceRequestDTO(
        confirmation_token=confirmation_token,
        order_id=_read_required_str(payload, "order_id"),
        invoice_title=_read_required_str(payload, "invoice_title"),
        tax_no=_read_optional_str(payload, "tax_no"),
        email=_read_optional_str(payload, "email"),
        trace_id=_read_required_str(payload, "trace_id"),
        task_id=_read_required_str(payload, "task_id"),
        status=_read_required_str(payload, "status"),
        message=_read_required_str(payload, "message"),
    )


def _read_required_str(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise AppException(errors.InternalServerError, message=f"无效的动作字段: {key}")
    return value


def _read_optional_str(payload: Mapping[str, object], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise AppException(errors.InternalServerError, message=f"无效的动作字段: {key}")
    return value or None


_ORDER_FIXTURES: dict[str, dict[str, object]] = {
    "1001": {
        "owner_user_id": 999,
        "status": "运输中",
        "carrier": "顺丰速运",
        "tracking_no": "SF10010001",
        "eta": "明天 18:00 前",
    },
    "1002": {
        "owner_user_id": 999,
        "status": "已签收",
        "carrier": "京东物流",
        "tracking_no": "JD10020002",
        "eta": "已于今天 10:24 签收",
    },
}


_order_service: OrderService | None = None


def new_order_service() -> OrderService:
    """依赖注入：获取 OrderService 单例。"""
    global _order_service
    if _order_service is None:
        _order_service = OrderService(
            action_store=new_agent_action_cache(),
            confirmation_seconds=settings.AGENT_ACTION_CONFIRMATION_SECONDS,
            idempotency_seconds=settings.AGENT_ACTION_IDEMPOTENCY_SECONDS,
        )
    return _order_service
