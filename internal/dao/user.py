from internal.infra.database import get_read_session, get_session
from internal.models.user import User
from pkg.database.dao import BaseDao


class UserDao(BaseDao[User]):
    _model_cls: type[User] = User

    async def get_by_phone(self, phone: str) -> User | None:
        async with self.read_session_provider() as session:
            result = await session.execute(
                self.select_stmt().where(self.model_cls.phone == phone)
            )
            return result.scalars().first()

    async def get_by_username(self, username: str) -> User | None:
        """根据用户名查询用户"""
        async with self.read_session_provider() as session:
            result = await session.execute(
                self.select_stmt().where(self.model_cls.username == username)
            )
            return result.scalars().first()

    async def is_phone_exist(self, phone: str) -> bool:
        # 利用 first() 查询，找到一条就返回，比 count() 更高效
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
