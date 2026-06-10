from __future__ import annotations

import pytest

from internal.agents.router import AgentRoute
from internal.core import AppException, errors
from internal.services.agents.confirmation import AgentConfirmationResolver


class FakeAgentConfirmationStore:
    def __init__(self, payloads: dict[str, dict[str, object]]):
        self._payloads = payloads

    async def get_pending_action(self, *, token: str) -> dict[str, object] | None:
        return self._payloads.get(token)


@pytest.mark.asyncio
async def test_agent_confirmation_resolver_reads_route_from_pending_action() -> None:
    resolver = AgentConfirmationResolver(
        action_store=FakeAgentConfirmationStore(
            {
                "token-payment": {
                    "route": "payment",
                    "action": "submit_payment",
                    "user_id": 999,
                    "session_id": "session_1",
                }
            }
        )
    )

    context = await resolver.resolve(
        user_id=999,
        confirmation_token="token-payment",
    )

    assert context.route is AgentRoute.PAYMENT
    assert context.action == "submit_payment"
    assert context.session_id == "session_1"


@pytest.mark.asyncio
async def test_agent_confirmation_resolver_rejects_token_from_another_user() -> None:
    resolver = AgentConfirmationResolver(
        action_store=FakeAgentConfirmationStore(
            {
                "token-order": {
                    "route": "order",
                    "action": "submit_invoice_request",
                    "user_id": 999,
                }
            }
        )
    )

    with pytest.raises(AppException) as exc_info:
        await resolver.resolve(user_id=1000, confirmation_token="token-order")

    assert exc_info.value.error == errors.Forbidden


@pytest.mark.asyncio
async def test_agent_confirmation_resolver_rejects_pending_action_without_route() -> None:
    resolver = AgentConfirmationResolver(
        action_store=FakeAgentConfirmationStore(
            {"token-without-route": {"action": "submit_invoice_request", "user_id": 999}}
        )
    )

    with pytest.raises(AppException) as exc_info:
        await resolver.resolve(user_id=999, confirmation_token="token-without-route")

    assert exc_info.value.error == errors.BadRequest
