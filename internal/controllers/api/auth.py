"""用户认证相关 API 接口"""

from typing import Annotated

from fastapi import APIRouter, Depends, Header

from internal.schemas import BaseResponse
from internal.schemas.user import (
    UserDetailSchema,
    UserLoginReqSchema,
    UserLoginRespSchema,
    UserRegisterReqSchema,
    WeChatLoginReqSchema,
)
from internal.services.auth import AuthService, new_auth_service
from pkg.toolkit.context import get_user_id
from pkg.toolkit.response import CustomORJSONResponse, success_response

router = APIRouter(prefix="/auth", tags=["authentication"])


AuthServiceDep = Annotated[AuthService, Depends(new_auth_service)]


def _extract_bearer_token(authorization: str | None) -> str | None:
    if authorization is None:
        return None
    if authorization.startswith("Bearer "):
        return authorization[7:]
    return authorization


@router.post("/login", response_model=BaseResponse[UserLoginRespSchema], summary="用户登录")
async def login(
    req: UserLoginReqSchema,
    auth_service: AuthServiceDep,
) -> CustomORJSONResponse:
    """用户登录。

    业务摘要:
        使用用户名和密码完成登录，成功后返回当前用户信息和访问 token。

    权限边界:
        匿名可访问；用户名和密码校验由 Service 层完成，失败返回 `errors.Unauthorized`。

    业务边界:
        会创建 Redis 用户会话并追加用户 token 列表；
        不修改用户基础资料，不承担第三方登录。

    Args:
        req: 登录请求体，包含用户名和密码。
        auth_service: 通过依赖注入获取的 `AuthService` 实例。

    Returns:
        `BaseResponse[UserLoginRespSchema]`：成功时返回用户信息和访问 token；
        用户名或密码错误返回 `errors.Unauthorized`。
    """
    login_data = await auth_service.login(username=req.username, password=req.password)
    return success_response(data=login_data.to_schema())


@router.post("/logout", response_model=BaseResponse[None], summary="用户登出")
async def logout(
    auth_service: AuthServiceDep,
    authorization: str | None = Header(None),
) -> CustomORJSONResponse:
    """用户登出。

    业务摘要:
        撤销当前请求 token 对应的用户会话。

    权限边界:
        需要有效的用户 token（`/v1` 前缀默认认证）；仅能撤销当前用户上下文中的会话。

    业务边界:
        删除 Redis token metadata，并从用户 token 列表移除当前 token；不删除用户账号。

    Args:
        auth_service: 通过依赖注入获取的 `AuthService` 实例。
        authorization: `Authorization` 请求头，支持裸 token 或 `Bearer <token>`。

    Returns:
        `BaseResponse[None]`：成功时 data 为 `None`；
        缺少 token 或用户上下文无效返回 `errors.Unauthorized`。
    """
    await auth_service.logout(
        user_id=get_user_id(),
        token=_extract_bearer_token(authorization),
    )
    return success_response()


@router.post("/register", response_model=BaseResponse[UserLoginRespSchema], summary="用户注册")
async def register(
    req: UserRegisterReqSchema,
    auth_service: AuthServiceDep,
) -> CustomORJSONResponse:
    """用户注册。

    业务摘要:
        使用用户名、手机号和密码创建新用户，成功后自动创建登录会话。

    权限边界:
        匿名可访问；手机号唯一性和密码处理由 Service 层校验与执行。

    业务边界:
        会写入用户数据并创建 Redis 用户会话；不发送短信验证码，不绑定第三方账号。

    Args:
        req: 注册请求体，包含用户名、手机号和密码。
        auth_service: 通过依赖注入获取的 `AuthService` 实例。

    Returns:
        `BaseResponse[UserLoginRespSchema]`：成功时返回用户信息和访问 token；
        手机号已存在返回 `errors.BadRequest`。
    """
    login_data = await auth_service.register(
        username=req.username,
        phone=req.phone,
        password=req.password,
    )
    return success_response(data=login_data.to_schema())


@router.get("/me", response_model=BaseResponse[UserDetailSchema], summary="获取当前用户信息")
async def get_current_user(auth_service: AuthServiceDep) -> CustomORJSONResponse:
    """获取当前用户信息。

    业务摘要:
        根据认证中间件写入的用户上下文返回当前用户基础信息。

    权限边界:
        需要有效的用户 token（`/v1` 前缀默认认证）；只返回当前用户上下文对应的数据。

    业务边界:
        只读接口，无写副作用；
        当前实现只读取用户上下文，不刷新 token，不查询第三方账号。

    Args:
        auth_service: 通过依赖注入获取的 `AuthService` 实例。

    Returns:
        `BaseResponse[UserDetailSchema]`：成功时返回当前用户基础信息；
        用户上下文无效返回 `errors.Unauthorized`。
    """
    user_data = await auth_service.get_current_user(user_id=get_user_id())
    return success_response(data=user_data.to_schema())


@router.post(
    "/wechat/login",
    response_model=BaseResponse[UserLoginRespSchema],
    summary="微信登录",
)
async def wechat_login(
    req: WeChatLoginReqSchema,
    auth_service: AuthServiceDep,
) -> CustomORJSONResponse:
    """微信登录。

    业务摘要:
        使用微信授权码完成第三方登录，成功后返回用户信息和访问 token。

    权限边界:
        匿名可访问；微信授权码校验、第三方账号匹配和用户创建由 Service 层完成。

    业务边界:
        会调用微信接口、读取或创建本地用户和第三方账号绑定，并创建 Redis 用户会话；
        不绑定手机号，不更新非微信来源的用户资料。

    Args:
        req: 微信登录请求体，包含微信授权码。
        auth_service: 通过依赖注入获取的 `AuthService` 实例。

    Returns:
        `BaseResponse[UserLoginRespSchema]`：成功时返回用户信息和访问 token；
        授权失败返回 `errors.BadRequest`，第三方异常返回 `errors.InternalServerError`。
    """
    login_data = await auth_service.wechat_login(code=req.code)
    return success_response(data=login_data.to_schema())
