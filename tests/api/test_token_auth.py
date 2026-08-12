from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request
from starlette.types import Scope

from internal.controllers import api as api_controller
from internal.core import AppException, errors
from internal.dependencies.auth import require_authenticated_user
from pkg import request_context as context

TEST_USER_ID = UUID("00000000-0000-7000-8000-000000000123")


def _request(path: str = "/v1/test") -> Request:
    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("test", 80),
    }
    return Request(scope)


@pytest.mark.asyncio
@pytest.mark.parametrize("authorization", ["token-123", "Bearer token-123"])
async def test_token_dependency_accepts_bare_and_bearer_tokens(
    authorization: str,
) -> None:
    verify_token = AsyncMock(return_value={"id": TEST_USER_ID.hex})
    auth_service = SimpleNamespace(verify_token=verify_token)
    context.set_user_id.reset_mock()

    principal = await require_authenticated_user(
        _request(),
        auth_service,
        authorization=authorization,
    )

    assert principal.user_id == TEST_USER_ID
    assert principal.token == "token-123"
    verify_token.assert_awaited_once_with("token-123")
    context.set_user_id.assert_called_once_with(TEST_USER_ID)


@pytest.mark.asyncio
@pytest.mark.parametrize("authorization", [None, "", "Bearer "])
async def test_token_dependency_rejects_missing_token(
    authorization: str | None,
) -> None:
    verify_token = AsyncMock()
    auth_service = SimpleNamespace(verify_token=verify_token)

    with pytest.raises(AppException) as exc_info:
        await require_authenticated_user(
            _request(),
            auth_service,
            authorization=authorization,
        )

    assert exc_info.value.error is errors.Unauthorized
    verify_token.assert_not_awaited()


@pytest.mark.asyncio
async def test_token_dependency_rejects_invalid_user_id() -> None:
    auth_service = SimpleNamespace(
        verify_token=AsyncMock(return_value={"id": "123"}),
    )

    with pytest.raises(AppException) as exc_info:
        await require_authenticated_user(
            _request(),
            auth_service,
            authorization="token-123",
        )

    assert exc_info.value.error is errors.Unauthorized


def test_api_router_documents_token_only_for_protected_routes() -> None:
    app = FastAPI()
    app.include_router(api_controller.router)
    openapi = app.openapi()["paths"]

    login_parameters = openapi["/v1/auth/login"]["post"].get("parameters", [])
    me_parameters = openapi["/v1/auth/me"]["get"]["parameters"]
    rag_parameters = openapi["/v1/rag/answer"]["post"]["parameters"]

    assert all(parameter["name"] != "Authorization" for parameter in login_parameters)
    assert any(parameter["name"] == "Authorization" for parameter in me_parameters)
    assert any(parameter["name"] == "Authorization" for parameter in rag_parameters)


@pytest.mark.asyncio
async def test_unknown_api_path_returns_not_found_without_authentication() -> None:
    app = FastAPI()
    app.include_router(api_controller.router)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/v1/not-found")

    assert response.status_code == 404
