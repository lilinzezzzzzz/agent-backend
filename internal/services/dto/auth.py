"""认证 Service DTO。"""

from dataclasses import dataclass

from internal.schemas.user import UserDetailSchema, UserLoginRespSchema


@dataclass(frozen=True, slots=True)
class AuthUserDTO:
    """认证用例返回的用户业务数据。"""

    id: int
    name: str
    phone: str

    def to_schema(self) -> UserDetailSchema:
        """转换为 API 响应 schema。"""
        return UserDetailSchema(id=self.id, name=self.name, phone=self.phone)


@dataclass(frozen=True, slots=True)
class UserLoginDTO:
    """认证成功后的业务数据。"""

    user: AuthUserDTO
    token: str

    def to_schema(self) -> UserLoginRespSchema:
        """转换为 API 响应 schema。"""
        return UserLoginRespSchema(user=self.user.to_schema(), token=self.token)
