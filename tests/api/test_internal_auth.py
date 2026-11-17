from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request
from starlette.types import Scope

from internal.controllers import internal as internal_controller
from internal.core import AppException, errors
from internal.dependencies import auth as internal_auth_dependencies
from internal.middlewares.exceptions import request_exception_handler
from internal.utils import signature as signature_module
from internal.utils.signature import get_signature_auth_handler
from pkg.security import SignatureAuthHandler


def _request(path: str = "/v1/internal/test") -> Request:
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
async def test_internal_signature_dependency_accepts_valid_headers() -> None:
    handler = MagicMock(spec=SignatureAuthHandler)
    handler.verify.return_value = True

    await internal_auth_dependencies.require_internal_signature(
        _request(),
        handler,
        x_signature="signature",
        x_timestamp="1710000000",
        x_nonce="nonce",
    )

    handler.verify.assert_called_once_with(
        x_signature="signature",
        x_timestamp="1710000000",
        x_nonce="nonce",
    )


@pytest.mark.asyncio
async def test_internal_signature_preserves_invalid_signature_error() -> None:
    handler = MagicMock(spec=SignatureAuthHandler)
    handler.verify.return_value = False

    with pytest.raises(AppException) as exc_info:
        await internal_auth_dependencies.require_internal_signature(_request(), handler)

    assert exc_info.value.error is errors.InvalidSignature
    assert exc_info.value.message == "Signature authentication failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("valid", [True, False])
async def test_internal_signature_uses_overridden_handler(
    monkeypatch: pytest.MonkeyPatch,
    valid: bool,
) -> None:
    monkeypatch.setattr(signature_module, "_signature_auth_handler", None)
    handler = MagicMock(spec=SignatureAuthHandler)
    handler.verify.return_value = valid
    app = FastAPI()
    app.add_exception_handler(AppException, request_exception_handler)
    app.dependency_overrides[get_signature_auth_handler] = lambda: handler

    @app.get(
        "/v1/internal/test",
        dependencies=[internal_auth_dependencies.InternalSignatureDependency],
    )
    async def endpoint() -> dict[str, bool]:
        return {"ok": True}

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/v1/internal/test",
            headers={
                "X-Signature": "signature",
                "X-Timestamp": "1710000000",
                "X-Nonce": "nonce",
            },
        )

    assert response.status_code == 200
    if valid:
        assert response.json() == {"ok": True}
    else:
        assert response.json()["code"] == errors.InvalidSignature.code
        assert (
            response.json()["message"]
            == "签名验证失败: Signature authentication failed"
        )
    handler.verify.assert_called_once_with(
        x_signature="signature",
        x_timestamp="1710000000",
        x_nonce="nonce",
    )


def test_internal_router_documents_optional_signature_headers() -> None:
    app = FastAPI()
    app.include_router(internal_controller.router)

    operation = app.openapi()["paths"]["/v1/internal/rag/external-runs"]["post"]
    headers = {
        parameter["name"]: parameter
        for parameter in operation["parameters"]
        if parameter["in"] == "header"
    }

    assert set(headers) == {"X-Signature", "X-Timestamp", "X-Nonce"}
    assert all(parameter["required"] is False for parameter in headers.values())
