import pytest
from starlette.types import Scope

from internal.middlewares.auth import _AuthContext


def _http_scope(path: str) -> Scope:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("test", 80),
    }


@pytest.mark.parametrize(
    "path",
    [
        "/v1/auth/login",
        "/v1/auth/register",
        "/v1/auth/wechat/login",
        "/v1/public/test/hello-world",
    ],
)
def test_anonymous_paths_are_whitelisted(path: str) -> None:
    assert _AuthContext(_http_scope(path)).is_whitelist() is True


@pytest.mark.parametrize("path", ["/auth/login", "/v1/auth/me", "/v1/user/hello-world"])
def test_non_public_paths_require_authentication(path: str) -> None:
    assert _AuthContext(_http_scope(path)).is_whitelist() is False


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/v1/internal", True),
        ("/v1/internal/rag/external-runs", True),
        ("/v1/internal-tools", False),
    ],
)
def test_only_internal_router_paths_bypass_token_auth(
    path: str,
    expected: bool,
) -> None:
    assert _AuthContext(_http_scope(path)).is_internal_api() is expected
