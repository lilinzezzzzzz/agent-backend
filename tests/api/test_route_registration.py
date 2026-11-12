"""验证移除 demo 后的应用路由注册。"""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from internal.app import register_router


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/v1/public/logger-span/viewer"),
        ("GET", "/v1/public/logger-span/traces/019da8cd058b76ed8a4a52141c1c6b38"),
        ("GET", "/v1/logger-span/traces/019da8cd058b76ed8a4a52141c1c6b38"),
        ("POST", "/v1/logger-span/simulate"),
    ],
)
async def test_removed_span_demo_routes_return_not_found(
    method: str,
    path: str,
) -> None:
    app = FastAPI()
    register_router(app)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.request(method, path)

    assert response.status_code == 404


def test_registered_openapi_excludes_span_demo() -> None:
    app = FastAPI()
    register_router(app)
    schema = app.openapi()

    assert "/v1/auth/login" in schema["paths"]
    assert not any(path.startswith("/v1/public/test/") for path in schema["paths"])
    assert not any("/logger-span" in path for path in schema["paths"])
