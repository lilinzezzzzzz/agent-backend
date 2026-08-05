"""身份提供方配置容器。"""

from dataclasses import dataclass


@dataclass
class WeChatConfig:
    """微信开放平台配置。"""

    app_id: str
    app_secret: str
    grant_type: str = "authorization_code"

    def __post_init__(self) -> None:
        if not self.app_id or not self.app_secret:
            raise ValueError("WeChat config requires app_id and app_secret")


@dataclass
class AlipayConfig:
    """支付宝开放平台配置（预留）。"""

    app_id: str
    app_private_key: str
    alipay_public_key: str
    encrypt_key: str | None = None

    def __post_init__(self) -> None:
        if not self.app_id or not self.app_private_key or not self.alipay_public_key:
            raise ValueError(
                "Alipay config requires app_id, app_private_key, and alipay_public_key"
            )
