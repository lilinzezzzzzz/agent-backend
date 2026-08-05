from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from internal.core import AppException
from internal.services import auth as auth_module
from internal.services.auth import AuthService
from pkg.third_party_auth.base import ThirdPartyUserInfo

pytestmark = pytest.mark.asyncio


class FakeWeChatStrategy:
    def __init__(self, *, returned_open_id: str = "openid-1") -> None:
        self.returned_open_id = returned_open_id
        self.close = AsyncMock()

    async def get_access_token(self, code: str) -> dict[str, str]:
        assert code == "wechat-code"
        return {"access_token": "ephemeral-token", "openid": "openid-1"}

    async def get_user_info(
        self, access_token: str, open_id: str
    ) -> ThirdPartyUserInfo:
        assert access_token == "ephemeral-token"
        assert open_id == "openid-1"
        return ThirdPartyUserInfo(
            open_id=self.returned_open_id,
            union_id="union-1",
            nickname="wechat user",
            avatar="https://example.com/avatar.png",
        )


def _patch_wechat_dependencies(monkeypatch, strategy: FakeWeChatStrategy) -> None:
    monkeypatch.setattr(
        auth_module,
        "settings",
        SimpleNamespace(
            WECHAT_APP_ID="app-id",
            WECHAT_APP_SECRET=SecretStr("app-secret"),
            WECHAT_GRANT_TYPE="authorization_code",
        ),
    )
    monkeypatch.setattr(
        auth_module,
        "WeChatAuthStrategy",
        lambda *, config: strategy,
    )


async def test_wechat_login_maps_identity_without_persisting_provider_token(
    monkeypatch,
) -> None:
    strategy = FakeWeChatStrategy()
    _patch_wechat_dependencies(monkeypatch, strategy)
    auth_cache = SimpleNamespace(save_user_session=AsyncMock())
    user = SimpleNamespace(
        id=1001,
        username="wechat user",
        phone="",
    )
    user_service = SimpleNamespace(
        get_or_create_user_by_external_identity=AsyncMock(return_value=user)
    )
    service = AuthService(auth_cache=auth_cache, user_service=user_service)
    monkeypatch.setattr(service, "generate_token", lambda: "tk_login")

    result = await service.wechat_login(code="wechat-code")

    assert result.token == "tk_login"
    user_service.get_or_create_user_by_external_identity.assert_awaited_once_with(
        provider="wechat",
        connection_key="wechat_default",
        subject="openid-1",
        union_id="union-1",
        nickname="wechat user",
        avatar="https://example.com/avatar.png",
    )
    strategy.close.assert_awaited_once_with()


async def test_wechat_login_rejects_mismatched_subject(monkeypatch) -> None:
    strategy = FakeWeChatStrategy(returned_open_id="different-openid")
    _patch_wechat_dependencies(monkeypatch, strategy)
    user_service = SimpleNamespace(get_or_create_user_by_external_identity=AsyncMock())
    service = AuthService(
        auth_cache=SimpleNamespace(save_user_session=AsyncMock()),
        user_service=user_service,
    )

    with pytest.raises(AppException) as exc_info:
        await service.wechat_login(code="wechat-code")

    assert exc_info.value.message == "微信身份校验失败"
    user_service.get_or_create_user_by_external_identity.assert_not_awaited()
    strategy.close.assert_awaited_once_with()
