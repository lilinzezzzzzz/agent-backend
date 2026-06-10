"""微信登录策略实现 - 配置通过参数注入"""

from typing import Any

from pkg.logger import logger
from pkg.http_client import AsyncHttpClient

from ..base import BaseThirdPartyAuthStrategy, ThirdPartyUserInfo
from ..config import WeChatConfig


class WeChatAuthStrategy(BaseThirdPartyAuthStrategy):
    """微信 OAuth2.0 认证策略

    使用示例:
        ```python
        strategy = WeChatAuthStrategy(
            config=WeChatConfig(
                app_id="your_app_id",
                app_secret="your_app_secret",
            )
        )

        token_info = await strategy.get_access_token(code)
        user_info = await strategy.get_user_info(
            access_token=token_info["access_token"],
            open_id=token_info["openid"]
        )
        ```
    """

    # 微信 API 端点
    ACCESS_TOKEN_URL = "https://api.weixin.qq.com/sns/oauth2/access_token"
    USER_INFO_URL = "https://api.weixin.qq.com/sns/userinfo"

    def __init__(self, config: WeChatConfig):
        """
        初始化微信认证策略

        Args:
            config: 微信配置（通过依赖注入）
        """
        self.config = config
        self._http_client = AsyncHttpClient(
            base_url="",
            timeout=30,
            headers={"Content-Type": "application/json"},
        )

    async def get_access_token(self, code: str) -> dict[str, Any]:
        """
        通过授权码获取 access_token

        Args:
            code: 微信授权码

        Returns:
            包含 access_token、expires_in、refresh_token、openid、scope 等信息的字典

        Raises:
            ValueError: 当微信 API 返回错误时
        """
        params = {
            "appid": self.config.app_id,
            "secret": self.config.app_secret,
            "code": code,
            "grant_type": self.config.grant_type,
        }

        result = await self._http_client.get(self.ACCESS_TOKEN_URL, params=params)

        if not result.success:
            error_msg = f"WeChat API failed: {result.error}"
            logger.error(error_msg)
            raise ValueError(f"获取微信 access_token 失败：{result.error}")

        response_data = result.json()

        # 检查微信返回的错误
        if "errcode" in response_data and response_data["errcode"] != 0:
            error_msg = f"WeChat API error: {response_data.get('errmsg', 'unknown error')}"
            logger.error(error_msg)
            raise ValueError(f"微信 API 错误：{response_data.get('errmsg', '未知错误')}")

        return response_data

    async def get_user_info(self, access_token: str, open_id: str) -> ThirdPartyUserInfo:
        """
        获取微信用户信息

        Args:
            access_token: 微信 access_token
            open_id: 微信 open_id

        Returns:
            ThirdPartyUserInfo: 标准化的微信用户信息

        Raises:
            ValueError: 当微信 API 返回错误时
        """
        params = {
            "access_token": access_token,
            "openid": open_id,
            "lang": "zh_CN",
        }

        result = await self._http_client.get(self.USER_INFO_URL, params=params)

        if not result.success:
            error_msg = f"WeChat user info API failed: {result.error}"
            logger.error(error_msg)
            raise ValueError(f"获取微信用户信息失败：{result.error}")

        response_data = result.json()

        # 检查微信返回的错误
        if "errcode" in response_data and response_data["errcode"] != 0:
            error_msg = f"WeChat API error: {response_data.get('errmsg', 'unknown error')}"
            logger.error(error_msg)
            raise ValueError(f"微信 API 错误：{response_data.get('errmsg', '未知错误')}")

        # 提取标准化用户信息
        return ThirdPartyUserInfo(
            open_id=response_data.get("openid", ""),
            union_id=response_data.get("unionid"),
            avatar=response_data.get("headimgurl"),
            nickname=response_data.get("nickname"),
            raw_data=response_data,
        )

    def get_platform_name(self) -> str:
        """获取平台名称"""
        return "wechat"

    async def close(self) -> None:
        """关闭 HTTP 客户端"""
        await self._http_client.close()
