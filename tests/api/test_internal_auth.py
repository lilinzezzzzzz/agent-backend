from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from starlette.requests import Request
from starlette.types import Scope

from internal.controllers import internal as internal_controller
from internal.core import AppException, errors
from internal.dependencies import auth as internal_auth_dependencies


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
async def test_internal_signature_dependency_accepts_valid_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verify = MagicMock(return_value=True)
    monkeypatch.setattr(
        internal_auth_dependencies,
        "signature_auth_handler",
        SimpleNamespace(verify=verify),
    )

    await internal_auth_dependencies.require_internal_signature(
        _request(),
        x_signature="signature",
        x_timestamp="1710000000",
        x_nonce="nonce",
    )

    verify.assert_called_once_with(
        x_signature="signature",
        x_timestamp="1710000000",
        x_nonce="nonce",
    )


@pytest.mark.asyncio
async def test_internal_signature_dependency_preserves_invalid_signature_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        internal_auth_dependencies,
        "signature_auth_handler",
        SimpleNamespace(verify=MagicMock(return_value=False)),
    )

    with pytest.raises(AppException) as exc_info:
        await internal_auth_dependencies.require_internal_signature(_request())

    assert exc_info.value.error is errors.InvalidSignature
    assert exc_info.value.message == "Signature authentication failed"


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
