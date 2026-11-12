"""应用元信息与文档路由装配，不启动 lifespan 或访问外部服务。"""

from types import SimpleNamespace

import pytest
from fastapi import APIRouter
from httpx import ASGITransport, AsyncClient

import internal.app as app_module
from internal.config import AppSettings


@pytest.fixture
def application_settings(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    settings = SimpleNamespace(
        debug=False,
        app=AppSettings(
            title="Agent API test",
            version="2.3.4",
            docs_enabled=True,
            redoc_enabled=True,
            openapi_enabled=True,
            docs_path="/docs",
            redoc_path="/redoc",
            openapi_path="/openapi.json",
        ),
        otel=SimpleNamespace(
            tracing_enabled=False,
            operation_spans_enabled=False,
            metrics_enabled=False,
            logs_enabled=False,
            auto_configure=False,
        ),
    )
    monkeypatch.setattr(app_module, "settings", settings)
    monkeypatch.setattr(app_module, "register_middleware", lambda app: None)
    return settings


@pytest.mark.asyncio
@pytest.mark.parametrize("debug", [False, True])
@pytest.mark.parametrize(
    "docs_enabled,redoc_enabled,openapi_enabled",
    [
        (True, True, True),
        (True, False, True),
        (False, True, True),
        (False, False, True),
        (False, False, False),
    ],
)
async def test_documentation_routes_are_controlled_independently_of_debug(
    application_settings: SimpleNamespace,
    debug: bool,
    docs_enabled: bool,
    redoc_enabled: bool,
    openapi_enabled: bool,
) -> None:
    application_settings.debug = debug
    application_settings.app = AppSettings.model_validate(
        {
            **application_settings.app.model_dump(),
            "docs_enabled": docs_enabled,
            "redoc_enabled": redoc_enabled,
            "openapi_enabled": openapi_enabled,
        }
    )
    app = app_module.create_app()
    assert app.debug is debug

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        for path, enabled in (
            ("/docs", docs_enabled),
            ("/docs/oauth2-redirect", docs_enabled),
            ("/redoc", redoc_enabled),
            ("/openapi.json", openapi_enabled),
        ):
            response = await client.get(path)
            assert response.status_code == (200 if enabled else 404), path
            if path == "/openapi.json" and enabled:
                schema = response.json()
                assert schema["info"]["title"] == "Agent API test"
                assert schema["info"]["version"] == "2.3.4"
                assert "/v1/auth/login" in schema["paths"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "docs_path",
    ["/reference/swagger", "/reference/swagger/", "/"],
)
async def test_custom_documentation_paths_replace_all_default_routes(
    application_settings: SimpleNamespace,
    docs_path: str,
) -> None:
    application_settings.app = AppSettings.model_validate(
        {
            **application_settings.app.model_dump(),
            "docs_path": docs_path,
            "redoc_path": "/reference/redoc",
            "openapi_path": "/reference/schema.json",
        }
    )
    app = app_module.create_app()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        for path in (docs_path, "/reference/redoc"):
            response = await client.get(path)
            assert response.status_code == 200
            assert "/reference/schema.json" in response.text
            assert "Agent API test" in response.text

        callback_path = f"{docs_path.rstrip('/')}/oauth2-redirect"
        swagger = await client.get(docs_path)
        assert callback_path in swagger.text
        callback = await client.get(callback_path)
        assert callback.status_code == 200
        schema = await client.get("/reference/schema.json")
        assert schema.status_code == 200
        assert schema.json()["info"]["version"] == "2.3.4"

        for path in ("/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"):
            response = await client.get(path)
            assert response.status_code == 404, path


@pytest.mark.parametrize("field", ["docs_path", "redoc_path", "openapi_path"])
@pytest.mark.parametrize(
    "path",
    ["/v1/auth/login", "/v1/auth/login/", "/v1/agent/runs/example"],
)
def test_documentation_routes_cannot_shadow_registered_business_routes(
    application_settings: SimpleNamespace,
    field: str,
    path: str,
) -> None:
    application_settings.app = AppSettings.model_validate(
        {**application_settings.app.model_dump(), field: path}
    )
    with pytest.raises(ValueError, match="conflicts with an application route"):
        app_module.create_app()


def test_swagger_callback_cannot_shadow_a_business_route(
    application_settings: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router = APIRouter()

    @router.get("/docs/oauth2-redirect")
    async def business_route() -> dict[str, bool]:
        return {"ok": True}

    monkeypatch.setattr(
        app_module,
        "register_router",
        lambda app: app.include_router(router),
    )
    with pytest.raises(ValueError, match="/docs/oauth2-redirect.*conflicts"):
        app_module.create_app()


def test_disabled_documentation_paths_do_not_reserve_business_routes(
    application_settings: SimpleNamespace,
) -> None:
    application_settings.app = AppSettings.model_validate(
        {
            **application_settings.app.model_dump(),
            "docs_enabled": False,
            "redoc_enabled": False,
            "openapi_enabled": False,
            "docs_path": "/v1/auth/login",
            "redoc_path": "/v1/auth/login",
            "openapi_path": "/v1/auth/login",
        }
    )
    app = app_module.create_app()
    assert "/v1/auth/login" in app.openapi()["paths"]
