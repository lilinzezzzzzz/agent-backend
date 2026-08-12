from __future__ import annotations

import pytest
from uuid import UUID

from internal.agents.router import AgentRoute
from internal.core import AppException, errors
from internal.services.agents.confirmation import resolve_agent_confirmation_context

TEST_USER_ID = UUID("00000000-0000-7000-8000-000000000999")
OTHER_USER_ID = UUID("00000000-0000-7000-8000-000000001000")


class FakeAgentConfirmationStore:
    def __init__(self, payloads: dict[str, dict[str, object]]):
        self._payloads = payloads

    async def get_pending_action(self, *, token: str) -> dict[str, object] | None:
        return self._payloads.get(token)


@pytest.mark.asyncio
async def test_resolve_agent_confirmation_context_reads_route_from_pending_action() -> (
    None
):
    context = await resolve_agent_confirmation_context(
        action_store=FakeAgentConfirmationStore(
            {
                "token-payment": {
                    "route": "payment",
                    "action": "submit_payment",
                    "user_id": TEST_USER_ID.hex,
                    "session_id": "session_1",
                }
            }
        ),
        user_id=TEST_USER_ID,
        confirmation_token="token-payment",
    )

    assert context.route is AgentRoute.PAYMENT
    assert context.action == "submit_payment"
    assert context.session_id == "session_1"


@pytest.mark.asyncio
async def test_resolve_agent_confirmation_context_rejects_token_from_another_user() -> (
    None
):
    action_store = FakeAgentConfirmationStore(
        {
            "token-order": {
                "route": "order",
                "action": "submit_invoice_request",
                "user_id": TEST_USER_ID.hex,
            }
        }
    )

    with pytest.raises(AppException) as exc_info:
        await resolve_agent_confirmation_context(
            action_store=action_store,
            user_id=OTHER_USER_ID,
            confirmation_token="token-order",
        )

    assert exc_info.value.error == errors.Forbidden


@pytest.mark.asyncio
async def test_resolve_agent_confirmation_context_rejects_pending_action_without_route() -> (
    None
):
    action_store = FakeAgentConfirmationStore(
        {
            "token-without-route": {
                "action": "submit_invoice_request",
                "user_id": TEST_USER_ID.hex,
            }
        }
    )

    with pytest.raises(AppException) as exc_info:
        await resolve_agent_confirmation_context(
            action_store=action_store,
            user_id=TEST_USER_ID,
            confirmation_token="token-without-route",
        )

    assert exc_info.value.error == errors.BadRequest
