"""微信身份提供方实现。"""

from typing import Any

from pkg.http_client import AsyncHttpClient
from pkg.logger import logger

from ..base import BaseIdentityProvider, ExternalIdentityProfile
from ..config import WeChatConfig


class WeChatIdentityProvider(BaseIdentityProvider):
    """通过微信 OAuth2.0 获取外部身份资料。"""

    ACCESS_TOKEN_URL = "https://api.weixin.qq.com/sns/oauth2/access_token"
    USER_INFO_URL = "https://api.weixin.qq.com/sns/userinfo"

    def __init__(self, config: WeChatConfig):
        self.config = config
        self._http_client = AsyncHttpClient(
            base_url="",
            timeout=30,
            headers={"Content-Type": "application/json"},
        )

    async def get_access_token(self, code: str) -> dict[str, Any]:
        params = {
            "appid": self.config.app_id,
            "secret": self.config.app_secret,
            "code": code,
            "grant_type": self.config.grant_type,
        }
        result = await self._http_client.get(self.ACCESS_TOKEN_URL, params=params)

        if not result.success:
            logger.error(f"WeChat API failed: {result.error}")
            raise ValueError(f"获取微信 access_token 失败：{result.error}")

        response_data = result.json()
        if "errcode" in response_data and response_data["errcode"] != 0:
            error_message = response_data.get("errmsg", "未知错误")
            logger.error(f"WeChat API error: {error_message}")
            raise ValueError(f"微信 API 错误：{error_message}")

        return response_data

    async def get_user_info(
        self, access_token: str, open_id: str
    ) -> ExternalIdentityProfile:
        params = {
            "access_token": access_token,
            "openid": open_id,
            "lang": "zh_CN",
        }
        result = await self._http_client.get(self.USER_INFO_URL, params=params)

        if not result.success:
            logger.error(f"WeChat user info API failed: {result.error}")
            raise ValueError(f"获取微信用户信息失败：{result.error}")

        response_data = result.json()
        if "errcode" in response_data and response_data["errcode"] != 0:
            error_message = response_data.get("errmsg", "未知错误")
            logger.error(f"WeChat API error: {error_message}")
            raise ValueError(f"微信 API 错误：{error_message}")

        return ExternalIdentityProfile(
            open_id=response_data.get("openid", ""),
            union_id=response_data.get("unionid"),
            avatar=response_data.get("headimgurl"),
            nickname=response_data.get("nickname"),
            raw_data=response_data,
        )

    def get_provider_name(self) -> str:
        return "wechat"

    async def close(self) -> None:
        await self._http_client.close()
