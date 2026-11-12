from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from httpx import ASGITransport, AsyncClient
from starlette.responses import JSONResponse, StreamingResponse

from internal.core import AppException, errors
from internal.middlewares.exceptions import request_exception_handler
from internal.middlewares.recorder import ASGIRecordMiddleware
from internal.schemas import BaseResponse
from pkg.api_response import ResponsePayload, success_response


@pytest.fixture
def app() -> FastAPI:
    app = FastAPI()
    app.add_exception_handler(AppException, request_exception_handler)
    app.add_exception_handler(RequestValidationError, request_exception_handler)
    app.add_middleware(ASGIRecordMiddleware)

    @app.get("/success", response_model=BaseResponse[dict[str, bool]])
    async def success() -> ResponsePayload:
        return success_response({"ok": True})

    @app.get("/business-error")
    async def business_error() -> None:
        raise AppException(errors.Forbidden)

    @app.get("/validation")
    async def validation(value: int) -> dict[str, int]:
        return {"value": value}

    @app.get("/unexpected-error")
    async def unexpected_error() -> None:
        raise RuntimeError("test failure")

    @app.get("/stream")
    async def stream() -> StreamingResponse:
        async def events() -> AsyncIterator[str]:
            yield "data: first\n\n"
            yield "data: second\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.get("/explicit-cache")
    async def explicit_cache(policy: str) -> JSONResponse:
        return JSONResponse(
            {"ok": True},
            headers={
                "cache-control": policy,
                "x-content-type-options": "invalid",
            },
        )

    return app


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "status_code", "code"),
    [
        ("/success", 200, 20000),
        ("/business-error", 200, errors.Forbidden.code),
        ("/validation?value=invalid", 200, errors.BadRequest.code),
        ("/unexpected-error", 200, errors.InternalServerError.code),
        ("/missing", 404, None),
    ],
)
async def test_recorder_adds_default_headers_to_json_responses(
    app: FastAPI,
    path: str,
    status_code: int,
    code: int | None,
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(path)

    assert response.status_code == status_code
    assert response.headers["content-type"] == "application/json"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "traceparent" in response.headers
    assert float(response.headers["x-process-time"]) >= 0
    if code is not None:
        assert response.json()["code"] == code


@pytest.mark.asyncio
async def test_recorder_adds_default_headers_to_sse_response(app: FastAPI) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/stream")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.text == "data: first\n\ndata: second\n\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["public, max-age=60", "no-cache"])
async def test_recorder_preserves_explicit_cache_policy_and_enforces_nosniff(
    app: FastAPI,
    policy: str,
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/explicit-cache", params={"policy": policy})

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert response.headers.get_list("cache-control") == [policy]
    assert response.headers.get_list("x-content-type-options") == ["nosniff"]
