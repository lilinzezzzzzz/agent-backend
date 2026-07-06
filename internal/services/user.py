from internal.dao.third_party_account import (
    ThirdPartyAccountDao,
    new_third_party_account_dao,
)
from internal.dao.user import UserDao, new_user_dao
from internal.models.user import User
from internal.utils.password import PasswordHandler
from pkg.third_party_auth.base import ThirdPartyUserInfo


class UserService:
    def __init__(self, dao: UserDao, third_party_dao: ThirdPartyAccountDao):
        self._user_dao = dao
        self._third_party_dao = third_party_dao

    @staticmethod
    async def hello_world():
        return "Hello World"

    async def get_user_by_phone(self, phone: str) -> User | None:
        # 建议直接传参数，而不是传 request 对象，这样 Service 更纯粹，更容易测试
        return await self._user_dao.get_by_phone(phone)

    async def get_user_by_username(self, username: str) -> User | None:
        """根据用户名查询用户"""
        return await self._user_dao.get_by_username(username)

    @staticmethod
    async def verify_password(user: User, password: str) -> bool:
        """
        验证用户密码

        Args:
            user: 用户对象
            password: 待验证的密码

        Returns:
            bool: 密码正确返回 True，否则返回 False
        """
        if not user.password_hash:
            return False

        return PasswordHandler.verify_password(password, user.password_hash)

    async def create_user(
        self, username: str, account: str, phone: str, password: str
    ) -> User:
        """
        创建新用户（带密码加密）

        Args:
            username: 用户名
            account: 账号
            phone: 手机号
            password: 原始密码

        Returns:
            User: 创建的用户对象
        """
        # 检查手机号是否已存在
        if await self._user_dao.is_phone_exist(phone):
            raise ValueError(f"手机号 {phone} 已被注册")

        # 加密密码
        password_hash = PasswordHandler.hash_password(password)

        # 创建用户
        user = self._user_dao.create(
            username=username,
            account=account,
            phone=phone,
            password_hash=password_hash,
            creator_id=0,
        )

        await self._user_dao.insert(user)

        return user

    async def get_or_create_user_by_third_party(
        self, platform: str, third_party_info: ThirdPartyUserInfo
    ) -> User:
        """
        根据第三方用户信息获取或创建用户

        Args:
            platform: 平台名称 (wechat, alipay, google, github 等)
            third_party_info: 第三方用户信息

        Returns:
            User: 用户对象
        """
        # 查询第三方账号是否已存在
        existing_account = await self._third_party_dao.get_by_platform_and_openid(
            platform, third_party_info.open_id
        )

        # 如果账号已存在，返回关联的用户
        if existing_account:
            user = await self._user_dao.query_by_primary_id(existing_account.user_id)
            if not user:
                raise RuntimeError(f"User {existing_account.user_id} not found")
            return user

        # 否则创建新用户并绑定第三方账号
        username = (
            third_party_info.nickname or f"{platform}_{third_party_info.open_id[:8]}"
        )

        # 创建用户
        user = self._user_dao.create(
            username=username,
            account=f"{platform}_{third_party_info.open_id}",
            phone="",  # 第三方登录默认无手机号，需要后续绑定
            password_hash=None,  # 无密码
        )

        # 创建第三方账号关联记录
        self._third_party_dao.create(
            user_id=user.id,
            platform=platform,
            open_id=third_party_info.open_id,
            union_id=third_party_info.union_id,
            avatar=third_party_info.avatar,
            nickname=third_party_info.nickname,
            access_token=getattr(third_party_info, "access_token", None),
            refresh_token=getattr(third_party_info, "refresh_token", None),
            expires_at=getattr(third_party_info, "expires_at", None),
            extra_data=getattr(third_party_info, "extra_data", None),
        )

        return user

    async def bind_third_party_account(
        self, user: User, platform: str, third_party_info: ThirdPartyUserInfo
    ) -> None:
        """
        将第三方账号绑定到现有用户

        Args:
            user: 现有用户对象
            platform: 平台名称
            third_party_info: 第三方用户信息

        Raises:
            ValueError: 当该第三方账号已被其他用户绑定时
        """
        # 检查该第三方账号是否已被绑定
        existing_account = await self._third_party_dao.get_by_platform_and_openid(
            platform, third_party_info.open_id
        )

        if existing_account and existing_account.user_id != user.id:
            raise ValueError(f"该{platform}账号已被其他用户绑定")

        # 如果已经绑定到当前用户，更新信息
        if existing_account:
            await self._third_party_dao.update(
                existing_account,
                union_id=third_party_info.union_id,
                avatar=third_party_info.avatar,
                nickname=third_party_info.nickname,
                access_token=getattr(third_party_info, "access_token", None),
                refresh_token=getattr(third_party_info, "refresh_token", None),
                expires_at=getattr(third_party_info, "expires_at", None),
                extra_data=getattr(third_party_info, "extra_data", None),
            )
        else:
            # 创建新的绑定关系
            self._third_party_dao.create(
                user_id=user.id,
                platform=platform,
                open_id=third_party_info.open_id,
                union_id=third_party_info.union_id,
                avatar=third_party_info.avatar,
                nickname=third_party_info.nickname,
                access_token=getattr(third_party_info, "access_token", None),
                refresh_token=getattr(third_party_info, "refresh_token", None),
                expires_at=getattr(third_party_info, "expires_at", None),
                extra_data=getattr(third_party_info, "extra_data", None),
            )


# 全局单例（懒加载）
_user_service: UserService | None = None


def new_user_service() -> UserService:
    """依赖注入：获取 UserService 单例"""
    global _user_service
    if _user_service is None:
        _user_service = UserService(
            dao=new_user_dao(),
            third_party_dao=new_third_party_account_dao(),
        )
    return _user_service
