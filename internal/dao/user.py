from internal.infra.database import get_read_session, get_session
from internal.models.user import User
from pkg.database.dao import BaseDao


class UserDao(BaseDao[User]):
    _model_cls: type[User] = User

    async def get_by_phone(self, phone: str) -> User | None:
        statement = self.select_stmt().where(self.model_cls.phone == phone)
        return await self.fetch_first(statement)

    async def get_by_username(self, username: str) -> User | None:
        """根据用户名查询用户"""
        statement = self.select_stmt().where(self.model_cls.username == username)
        return await self.fetch_first(statement)

    async def is_phone_exist(self, phone: str) -> bool:
        # 复用手机号唯一查询，避免额外 COUNT。
        return await self.get_by_phone(phone) is not None


# 全局单例（懒加载）
_user_dao: UserDao | None = None


def new_user_dao() -> UserDao:
    global _user_dao
    if _user_dao is None:
        _user_dao = UserDao(
            session_provider=get_session,
            read_session_provider=get_read_session,
        )
    return _user_dao
