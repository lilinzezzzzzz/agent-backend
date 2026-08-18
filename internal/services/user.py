import hashlib
from functools import cache
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from internal.dao.external_identity import (
    ExternalIdentityDao,
    new_external_identity_dao,
)
from internal.dao.user import UserDao, new_user_dao
from internal.models.user import User
from internal.utils.password import PasswordHandler
from pkg.database.audit import AuditActor
from pkg.toolkit.timer import utc_now_naive


class UserService:
    def __init__(self, dao: UserDao, external_identity_dao: ExternalIdentityDao):
        self._user_dao = dao
        self._external_identity_dao = external_identity_dao

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
            audit_actor=AuditActor.system(),
            username=username,
            account=account,
            phone=phone,
            password_hash=password_hash,
        )

        await self._user_dao.insert(user)

        return user

    async def get_or_create_user_by_external_identity(
        self,
        *,
        provider: str,
        connection_key: str,
        subject: str,
        email: str | None = None,
        union_id: str | None = None,
        nickname: str | None = None,
        avatar: str | None = None,
    ) -> User:
        """按外部稳定身份获取用户，不存在时原子创建用户和映射。"""
        self._validate_external_identity_key(
            provider=provider,
            connection_key=connection_key,
            subject=subject,
        )
        email = self._snapshot(email, max_length=255)
        union_id = self._snapshot(union_id, max_length=256)
        nickname = self._snapshot(nickname, max_length=128)
        avatar = self._snapshot(avatar, max_length=512)

        try:
            return await self._get_or_create_external_identity_in_transaction(
                provider=provider,
                connection_key=connection_key,
                subject=subject,
                email=email,
                union_id=union_id,
                nickname=nickname,
                avatar=avatar,
            )
        except IntegrityError:
            # 并发首登时仅一个事务能创建唯一身份。失败方读取获胜事务的结果。
            identity = await self._external_identity_dao.get_by_identity(
                provider=provider,
                connection_key=connection_key,
                subject=subject,
            )
            if identity is None:
                raise
            user = await self._get_active_user_from_primary(identity.user_id)
            if user is None:
                raise RuntimeError(
                    f"External identity {identity.id} references a missing user"
                )
            return user

    async def _get_or_create_external_identity_in_transaction(
        self,
        *,
        provider: str,
        connection_key: str,
        subject: str,
        email: str | None,
        union_id: str | None,
        nickname: str | None,
        avatar: str | None,
    ) -> User:
        authenticated_at = utc_now_naive()
        async with self._user_dao.transaction() as session:
            identity = await self._external_identity_dao.get_by_identity(
                provider=provider,
                connection_key=connection_key,
                subject=subject,
                include_deleted=True,
                for_update=True,
                session=session,
            )
            if identity is not None:
                user = await self._get_active_user(identity.user_id, session=session)
                if user is None:
                    raise RuntimeError(
                        f"External identity {identity.id} references a missing user"
                    )
                updates: dict[str, object] = {
                    "last_authenticated_at": authenticated_at,
                }
                if identity.deleted_at is not None:
                    updates["deleted_at"] = None
                if email is not None:
                    updates["email_snapshot"] = email
                if union_id is not None:
                    updates["union_id"] = union_id
                if nickname is not None:
                    updates["nickname_snapshot"] = nickname
                if avatar is not None:
                    updates["avatar_snapshot"] = avatar
                statement = self._external_identity_dao.update_stmt(
                    self._external_identity_dao.model_cls.id == identity.id,
                    values=updates,
                    audit_actor=AuditActor.system(),
                    include_deleted=True,
                )
                updated = await self._external_identity_dao.execute_update(
                    statement,
                    session=session,
                )
                if updated != 1:
                    raise RuntimeError(
                        f"External identity {identity.id} no longer exists"
                    )
                return user

            identity_digest = hashlib.sha256(
                f"{provider}\0{connection_key}\0{subject}".encode()
            ).hexdigest()
            username = (nickname or f"{provider}_user_{identity_digest[:8]}")[:64]
            user = self._user_dao.create(
                audit_actor=AuditActor.system(),
                username=username,
                account=f"external_{identity_digest[:32]}",
                phone="",
                password_hash=None,
            )
            identity = self._external_identity_dao.create(
                audit_actor=AuditActor.system(),
                user_id=user.id,
                provider=provider,
                connection_key=connection_key,
                subject=subject,
                email_snapshot=email,
                union_id=union_id,
                nickname_snapshot=nickname,
                avatar_snapshot=avatar,
                last_authenticated_at=authenticated_at,
            )
            session.add_all((user, identity))
            return user

    async def _get_active_user(
        self,
        user_id: UUID,
        *,
        session: AsyncSession,
    ) -> User | None:
        statement = (
            self._user_dao.select_stmt()
            .where(self._user_dao.model_cls.id == user_id)
            .with_for_update()
        )
        return await self._user_dao.fetch_one(statement, session=session)

    async def _get_active_user_from_primary(self, user_id: UUID) -> User | None:
        statement = self._user_dao.select_stmt().where(
            self._user_dao.model_cls.id == user_id
        )
        async with self._user_dao.session_provider() as session:
            return await self._user_dao.fetch_one(statement, session=session)

    @staticmethod
    def _snapshot(value: str | None, *, max_length: int) -> str | None:
        if not value:
            return None
        return value[:max_length]

    @staticmethod
    def _validate_external_identity_key(
        *, provider: str, connection_key: str, subject: str
    ) -> None:
        limits = {
            "provider": (provider, 32),
            "connection_key": (connection_key, 64),
            "subject": (subject, 256),
        }
        for name, (value, max_length) in limits.items():
            if not value:
                raise ValueError(f"{name} must not be empty")
            if len(value) > max_length:
                raise ValueError(f"{name} exceeds {max_length} characters")


@cache
def new_user_service() -> UserService:
    """依赖注入：获取 UserService 单例"""
    return UserService(
        dao=new_user_dao(),
        external_identity_dao=new_external_identity_dao(),
    )
