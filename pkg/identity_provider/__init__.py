"""外部身份提供方适配层。"""

from .base import BaseIdentityProvider, ExternalIdentityProfile
from .config import AlipayConfig, WeChatConfig
from .providers.wechat import WeChatIdentityProvider
from .registry import IdentityProviderRegistry, IdentityProviderType

__all__ = [
    "AlipayConfig",
    "BaseIdentityProvider",
    "ExternalIdentityProfile",
    "IdentityProviderRegistry",
    "IdentityProviderType",
    "WeChatConfig",
    "WeChatIdentityProvider",
]
