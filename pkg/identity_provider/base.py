"""外部身份提供方的通用接口。"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class ExternalIdentityProfile:
    """外部身份提供方返回的标准化身份资料。"""

    open_id: str
    union_id: str | None = None
    avatar: str | None = None
    nickname: str | None = None
    raw_data: dict[str, Any] | None = None


class BaseIdentityProvider(ABC):
    """外部身份提供方基类。

    Provider 应保持无状态，配置通过构造函数注入。
    """

    @abstractmethod
    async def get_access_token(self, code: str) -> dict[str, Any]:
        """使用授权码换取外部访问令牌。"""

    @abstractmethod
    async def get_user_info(
        self, access_token: str, open_id: str
    ) -> ExternalIdentityProfile:
        """获取并标准化外部身份资料。"""

    @abstractmethod
    def get_provider_name(self) -> str:
        """返回稳定的身份提供方名称。"""

    async def close(self) -> None:
        """释放 Provider 持有的资源。"""
