"""身份提供方注册表。"""

from collections.abc import Callable
from enum import Enum
from typing import Any

from pkg.logger import logger

from .base import BaseIdentityProvider
from .providers.wechat import WeChatIdentityProvider

IdentityProviderBuilder = Callable[..., BaseIdentityProvider]


class IdentityProviderType(str, Enum):
    """可配置的外部身份提供方类型。"""

    WECHAT = "wechat"
    ALIPAY = "alipay"


class IdentityProviderRegistry:
    """注册并创建外部身份提供方实例。"""

    _providers: dict[IdentityProviderType, IdentityProviderBuilder] = {
        IdentityProviderType.WECHAT: WeChatIdentityProvider,
    }

    @classmethod
    def register_provider(
        cls,
        provider_type: IdentityProviderType,
        builder: IdentityProviderBuilder,
    ) -> None:
        cls._providers[provider_type] = builder
        logger.info(f"Registered identity provider for {provider_type.value}")

    @classmethod
    def create(
        cls,
        provider_type: IdentityProviderType | str,
        **kwargs: Any,
    ) -> BaseIdentityProvider:
        """使用显式注入的配置参数创建 Provider。"""

        if isinstance(provider_type, str):
            try:
                provider_type = IdentityProviderType(provider_type.lower())
            except ValueError as exc:
                raise ValueError(
                    f"Unsupported identity provider: {provider_type}"
                ) from exc

        builder = cls._providers.get(provider_type)
        if builder is None:
            raise ValueError(
                f"Identity provider '{provider_type.value}' is not registered. "
                f"Available providers: {[item.value for item in cls._providers]}"
            )

        return builder(**kwargs)

    @classmethod
    def get_available_provider_types(cls) -> list[str]:
        return [provider_type.value for provider_type in cls._providers]
