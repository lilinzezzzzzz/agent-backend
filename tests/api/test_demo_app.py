"""实验接口由独立 app 装配，常规业务 app 不暴露它们。"""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from examples.api import app as demo_module
from examples.api.routes import router
from internal.app import register_exception, register_router

DEMO_PATHS = (
    "/v1/public/test/test_validation_error",
    "/v1/public/test/test_raise_exception",
    "/v1/public/test/test_raise_app_exception",
    "/v1/public/test/test_contextvars_on_asyncio_task",
    "/v1/public/test/test/sse-stream",
    "/v1/public/test/chat/sse-stream/timeout",
)


def test_only_demo_app_registers_experimental_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    register_router(app)
    assert not set(DEMO_PATHS).intersection(app.openapi()["paths"])
    assert "/v1/auth/login" in app.openapi()["paths"]

    monkeypatch.setattr(demo_module, "create_app", lambda: app)
    demo = demo_module.create_demo_app()

    assert demo is app
    assert set(DEMO_PATHS).issubset(demo.openapi()["paths"])
    assert "/v1/auth/login" in demo.openapi()["paths"]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", DEMO_PATHS)
async def test_normal_app_does_not_serve_experimental_routes(path: str) -> None:
    app = FastAPI()
    register_router(app)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        method = "POST" if path == DEMO_PATHS[0] else "GET"
        response = await client.request(method, path)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_demo_validation_uses_existing_response_envelope() -> None:
    app = FastAPI()
    app.include_router(router, prefix="/v1/public")
    register_exception(app)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        valid = await client.post(
            DEMO_PATHS[0],
            json={"name": "demo", "age": 20, "email": "demo@example.com"},
        )
        invalid = await client.post(
            DEMO_PATHS[0],
            json={"name": "x", "age": -1, "email": "invalid"},
        )
        failure = await client.get(DEMO_PATHS[2])
    assert valid.status_code == 200
    assert valid.json()["code"] == 20000
    assert valid.json()["data"]["name"] == "demo"
    assert invalid.status_code == 200
    assert invalid.json()["code"] == 40000
    assert failure.status_code == 200
    assert failure.json()["code"] == 50000
