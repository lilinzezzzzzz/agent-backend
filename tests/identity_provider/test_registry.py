import pytest

from pkg.identity_provider import (
    IdentityProviderRegistry,
    IdentityProviderType,
    WeChatConfig,
    WeChatIdentityProvider,
)


@pytest.mark.asyncio
async def test_create_wechat_provider_with_explicit_config() -> None:
    provider = IdentityProviderRegistry.create(
        IdentityProviderType.WECHAT,
        config=WeChatConfig(app_id="app-id", app_secret="app-secret"),
    )

    try:
        assert isinstance(provider, WeChatIdentityProvider)
        assert provider.config.app_id == "app-id"
    finally:
        await provider.close()


def test_create_rejects_unregistered_provider() -> None:
    with pytest.raises(ValueError, match="is not registered"):
        IdentityProviderRegistry.create(IdentityProviderType.ALIPAY)
