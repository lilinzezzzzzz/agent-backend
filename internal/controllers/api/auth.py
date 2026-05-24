"""用户认证相关 API 接口"""

import secrets
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header

from internal.cache.auth import new_auth_cache
from internal.config import settings
from internal.core import AppException, errors
from internal.schemas import BaseResponse
from internal.schemas.user import (
    UserDetailSchema,
    UserLoginReqSchema,
    UserLoginRespSchema,
    UserLogoutRespSchema,
    UserRegisterReqSchema,
    WeChatLoginReqSchema,
)
from internal.services.user import UserService, new_user_service
from pkg.logger import logger
from pkg.third_party_auth import WeChatAuthStrategy, WeChatConfig
from pkg.toolkit.context import get_user_id
from pkg.toolkit.response import ResponsePayload, success_response

_auth_cache = new_auth_cache()

router = APIRouter(prefix="/auth", tags=["authentication"])

# Token 配置
TOKEN_EXPIRE_MINUTES = 30  # Token 有效期 30 分钟


def generate_token() -> str:
    """
    生成安全的随机 token

    使用 secrets 模块生成加密安全的 token：
    - 使用操作系统级别的真随机数生成器
    - 适合生成安全令牌、会话 ID 等
    - 比 uuid 更安全，适合认证场景

    Returns:
        str: 格式为 'tk_{hex}' 的 token，长度 34 字符
    """
    # secrets.token_hex(16) 生成 32 字符的十六进制字符串
    return f"tk_{secrets.token_hex(16)}"


# 依赖注入类型注解（FastAPI 0.95+ 推荐用法）
UserServiceDep = Annotated[UserService, Depends(new_user_service)]


@router.post("/login", response_model=BaseResponse[UserLoginRespSchema], summary="用户登录")
async def login(
    req: UserLoginReqSchema,
    user_service: UserServiceDep,
) -> ResponsePayload:
    """
    用户登录接口

    - 验证用户名密码
    - 生成 token 并存储到 Redis
    - 返回用户信息和 token
    """
    # 查询用户
    user = await user_service.get_user_by_username(req.username)

    if not user:
        raise AppException(errors.Unauthorized, message="用户名或密码错误")

    # 验证密码
    if not await user_service.verify_password(user, req.password):
        raise AppException(errors.Unauthorized, message="用户名或密码错误")

    # 生成 token
    token = generate_token()

    # 构建用户元数据
    user_metadata = {
        "id": user.id,
        "username": user.username,
        "phone": user.phone,
        "created_at": int(datetime.now(UTC).timestamp()),
    }

    # 存储 token 到 Redis 并加入用户 token 列表
    await _auth_cache.save_user_session(user.id, token, user_metadata, ex=TOKEN_EXPIRE_MINUTES * 60)

    logger.info(f"User {user.id} logged in successfully, token: {token[:10]}...")

    return success_response(
        data=UserLoginRespSchema(
            user=UserDetailSchema(id=user.id, name=user.username, phone=user.phone),
            token=token,
        )
    )


@router.post("/logout", response_model=BaseResponse[UserLogoutRespSchema], summary="用户登出")
async def logout(authorization: str | None = Header(None)) -> ResponsePayload:
    """
    用户登出接口

    - 从请求头获取 token
    - 从 Redis 中删除 token
    - 使 token 失效
    """
    if not authorization:
        raise AppException(errors.Unauthorized, message="缺少认证信息")

    # 提取 token (支持 Bearer token 格式)
    token = authorization
    if authorization.startswith("Bearer "):
        token = authorization[7:]

    # 获取当前用户 ID（从上下文）
    user_id = get_user_id()
    if not user_id:
        raise AppException(errors.Unauthorized, message="无效的用户上下文")

    # 撤销会话：删除 metadata 并从 token 列表中移除
    deleted_count = await _auth_cache.revoke_user_session(user_id, token)

    if deleted_count > 0:
        logger.info(f"User {user_id} logged out successfully")
    else:
        logger.warning(f"Logout failed: token not found, user_id: {user_id}")

    return success_response(data=UserLogoutRespSchema(message="登出成功"))


@router.post("/register", response_model=BaseResponse[UserLoginRespSchema], summary="用户注册")
async def register(
    req: UserRegisterReqSchema,
    user_service: UserServiceDep,
) -> ResponsePayload:
    """
    用户注册接口

    - 验证手机号是否已存在
    - 加密密码并创建用户
    - 自动生成 token 并登录
    """
    try:
        # 创建用户（账号默认使用手机号）
        user = await user_service.create_user(
            username=req.username,
            account=req.phone,  # 账号使用手机号
            phone=req.phone,
            password=req.password,
        )

        # 生成 token
        token = generate_token()

        # 构建用户元数据
        user_metadata = {
            "id": user.id,
            "username": user.username,
            "phone": user.phone,
            "created_at": int(datetime.now(UTC).timestamp()),
        }

        # 存储 token 到 Redis 并加入用户 token 列表
        await _auth_cache.save_user_session(user.id, token, user_metadata, ex=TOKEN_EXPIRE_MINUTES * 60)

        logger.info(f"User {user.id} registered successfully, token: {token[:10]}...")

        return success_response(
            data=UserLoginRespSchema(
                user=UserDetailSchema(id=user.id, name=user.username, phone=user.phone),
                token=token,
            )
        )

    except ValueError as e:
        # 手机号已存在等错误
        raise AppException(errors.BadRequest, message=str(e)) from e
    except Exception as e:
        raise AppException(errors.InternalServerError, message="注册失败，请稍后重试") from e


@router.get("/me", response_model=BaseResponse[UserDetailSchema], summary="获取当前用户信息")
async def get_current_user() -> ResponsePayload:
    """
    根据 token 查询 Redis 缓存的用户元数据

    - 从请求头获取 token
    - 从 Redis 查询用户元数据
    - 返回用户详细信息
    """
    # 从上下文获取用户 ID（由 auth 中间件设置）
    user_id = get_user_id()

    if not user_id:
        raise AppException(errors.Unauthorized, message="未认证的用户")

    # TODO: 如果需要从数据库获取最新用户信息，可以在这里查询
    # 目前直接从 token 元数据中获取

    # 由于 user_id 已经在上下文中，说明 token 验证通过
    # 这里可以返回一个基本的用户信息
    # 实际应用中可能需要从数据库或缓存中获取完整的用户信息

    logger.debug(f"Get current user info, user_id: {user_id}")

    # TODO: 这里应该从数据库或缓存获取完整的用户信息
    # 暂时返回一个基本的响应
    return success_response(data=UserDetailSchema(id=user_id, name="unknown", phone=""))


@router.post(
    "/wechat/login",
    response_model=BaseResponse[UserLoginRespSchema],
    summary="微信登录",
)
async def wechat_login(
    req: WeChatLoginReqSchema,
    user_service: UserServiceDep,
) -> ResponsePayload:
    """
    微信登录接口

    流程:
    1. 通过 code 换取 access_token 和 openid
    2. 查询 openid 是否存在
       - 存在：直接登录，生成 token
       - 不存在：创建新用户，生成 token
    3. 返回用户信息和 token
    """
    # 通过配置创建策略实例（依赖注入）
    strategy = WeChatAuthStrategy(
        config=WeChatConfig(
            app_id=settings.WECHAT_APP_ID,
            app_secret=settings.WECHAT_APP_SECRET.get_secret_value(),
        )
    )

    try:
        # 1. 通过授权码获取 access_token
        token_result = await strategy.get_access_token(req.code)

        access_token = token_result.get("access_token")
        openid = token_result.get("openid")

        if not access_token or not openid:
            raise AppException(errors.BadRequest, message="微信授权失败")

        # 2. 获取微信用户信息
        wechat_user_info = await strategy.get_user_info(access_token, openid)

        # 3. 获取或创建用户
        user = await user_service.get_or_create_user_by_third_party(
            platform="wechat",
            third_party_info=wechat_user_info,
        )

        # 4. 生成 token
        token = generate_token()

        # 构建用户元数据
        user_metadata = {
            "id": user.id,
            "username": user.username,
            "phone": user.phone,
            "created_at": int(datetime.now(UTC).timestamp()),
        }

        # 5. 存储 token 到 Redis 并加入用户 token 列表
        await _auth_cache.save_user_session(
            user.id,
            token,
            user_metadata,
            ex=TOKEN_EXPIRE_MINUTES * 60,
        )

        logger.info(f"WeChat user {user.id} logged in successfully, openid: {openid}")

        return success_response(
            data=UserLoginRespSchema(
                user=UserDetailSchema(id=user.id, name=user.username, phone=user.phone),
                token=token,
            )
        )

    except ValueError as e:
        logger.error(f"WeChat login error: {e}")
        raise AppException(errors.BadRequest, message=str(e)) from e
    except Exception as e:
        logger.error(f"WeChat login unexpected error: {e}")
        raise AppException(errors.InternalServerError, message="微信登录失败，请稍后重试") from e
    finally:
        # 关闭策略资源
        await strategy.close()
