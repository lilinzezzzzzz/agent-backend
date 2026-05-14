"""认证服务层"""

import secrets
from datetime import UTC, datetime

from internal.cache.auth import AuthCache, new_auth_cache
from internal.config import settings
from internal.core import AppException, errors
from internal.models.user import User
from internal.services.dto.auth import AuthUserDTO, UserLoginDTO
from internal.services.user import UserService, new_user_service
from pkg.logger import logger
from pkg.third_party_auth import WeChatAuthStrategy, WeChatConfig

TOKEN_EXPIRE_MINUTES = 30


class AuthService:
    def __init__(self, *, auth_cache: AuthCache, user_service: UserService):
        self._auth_cache = auth_cache
        self._user_service = user_service

    @staticmethod
    def generate_token() -> str:
        """生成安全的随机 token。"""
        return f"tk_{secrets.token_hex(16)}"

    @staticmethod
    def _build_user_dto(user: User) -> AuthUserDTO:
        return AuthUserDTO(id=user.id, name=user.username, phone=user.phone)

    @staticmethod
    def _build_user_metadata(user: User) -> dict[str, int | str]:
        return {
            "id": user.id,
            "username": user.username,
            "phone": user.phone,
            "created_at": int(datetime.now(UTC).timestamp()),
        }

    async def _save_user_session(self, *, user: User, token: str) -> None:
        await self._auth_cache.save_user_session(
            user.id,
            token,
            self._build_user_metadata(user),
            ex=TOKEN_EXPIRE_MINUTES * 60,
        )

    async def login(self, *, username: str, password: str) -> UserLoginDTO:
        """验证账号密码并创建用户会话。"""
        user = await self._user_service.get_user_by_username(username)
        if not user:
            raise AppException(errors.Unauthorized, message="用户名或密码错误")

        if not await self._user_service.verify_password(user, password):
            raise AppException(errors.Unauthorized, message="用户名或密码错误")

        token = self.generate_token()
        await self._save_user_session(user=user, token=token)

        logger.info(f"User {user.id} logged in successfully, token: {token[:10]}...")
        return UserLoginDTO(user=self._build_user_dto(user), token=token)

    async def register(self, *, username: str, phone: str, password: str) -> UserLoginDTO:
        """创建用户并创建登录会话。"""
        try:
            user = await self._user_service.create_user(
                username=username,
                account=phone,
                phone=phone,
                password=password,
            )
            token = self.generate_token()
            await self._save_user_session(user=user, token=token)
        except ValueError as exc:
            raise AppException(errors.BadRequest, message=str(exc)) from exc
        except AppException:
            raise
        except Exception as exc:
            logger.error(f"Register user failed: {exc}")
            raise AppException(errors.InternalServerError, message="注册失败，请稍后重试") from exc

        logger.info(f"User {user.id} registered successfully, token: {token[:10]}...")
        return UserLoginDTO(user=self._build_user_dto(user), token=token)

    async def logout(self, *, user_id: int | None, token: str | None) -> None:
        """撤销当前用户会话。"""
        if not token:
            raise AppException(errors.Unauthorized, message="缺少认证信息")
        if not user_id:
            raise AppException(errors.Unauthorized, message="无效的用户上下文")

        deleted_count = await self._auth_cache.revoke_user_session(user_id, token)
        if deleted_count > 0:
            logger.info(f"User {user_id} logged out successfully")
        else:
            logger.warning(f"Logout failed: token not found, user_id: {user_id}")

    async def get_current_user(self, *, user_id: int | None) -> AuthUserDTO:
        """返回当前认证上下文中的用户业务数据。"""
        if not user_id:
            raise AppException(errors.Unauthorized, message="未认证的用户")

        logger.debug(f"Get current user info, user_id: {user_id}")
        return AuthUserDTO(id=user_id, name="unknown", phone="")

    async def wechat_login(self, *, code: str) -> UserLoginDTO:
        """使用微信授权码登录并创建用户会话。"""
        strategy = WeChatAuthStrategy(
            config=WeChatConfig(
                app_id=settings.WECHAT_APP_ID,
                app_secret=settings.WECHAT_APP_SECRET.get_secret_value(),
            )
        )

        try:
            token_result = await strategy.get_access_token(code)
            access_token = token_result.get("access_token")
            openid = token_result.get("openid")
            if not access_token or not openid:
                raise AppException(errors.BadRequest, message="微信授权失败")

            wechat_user_info = await strategy.get_user_info(access_token, openid)
            user = await self._user_service.get_or_create_user_by_third_party(
                platform="wechat",
                third_party_info=wechat_user_info,
            )

            token = self.generate_token()
            await self._save_user_session(user=user, token=token)
        except ValueError as exc:
            logger.error(f"WeChat login error: {exc}")
            raise AppException(errors.BadRequest, message=str(exc)) from exc
        except AppException:
            raise
        except Exception as exc:
            logger.error(f"WeChat login unexpected error: {exc}")
            raise AppException(errors.InternalServerError, message="微信登录失败，请稍后重试") from exc
        finally:
            await strategy.close()

        logger.info(f"WeChat user {user.id} logged in successfully, openid: {openid}")
        return UserLoginDTO(user=self._build_user_dto(user), token=token)

    async def verify_token(self, token: str) -> dict:
        """验证 token，成功返回 user_metadata，失败抛出 AppException"""
        user_metadata: dict | None = await self._auth_cache.get_user_metadata(token)
        if user_metadata is None:
            raise AppException(errors.Unauthorized, message="Token verification failed: token not found")

        user_id = user_metadata.get("id")
        if not user_id:
            raise AppException(errors.Unauthorized, message="Token verification failed: user_id is None")

        # 检查有没有在token 列表里
        token_list: list = await self._auth_cache.get_user_token_list(user_id)
        if token not in token_list:
            raise AppException(
                errors.Unauthorized,
                message=f"Token verification failed: token not found in token list, user_id: {user_id}",
            )

        return user_metadata


# 全局单例（懒加载）
_auth_service: AuthService | None = None


def new_auth_service() -> AuthService:
    """依赖注入：获取 AuthService 单例"""
    global _auth_service
    if _auth_service is None:
        _auth_service = AuthService(
            auth_cache=new_auth_cache(),
            user_service=new_user_service(),
        )
    return _auth_service
