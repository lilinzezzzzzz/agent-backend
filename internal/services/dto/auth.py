"""认证 Service DTO。"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AuthUserDTO:
    """认证用例返回的用户业务数据。"""

    id: int
    name: str
    phone: str


@dataclass(frozen=True, slots=True)
class UserLoginDTO:
    """认证成功后的业务数据。"""

    user: AuthUserDTO
    token: str
