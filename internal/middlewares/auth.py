from starlette.types import ASGIApp, Receive, Scope, Send

from internal.core import AppException, errors
from internal.services.auth import new_auth_service
from internal.utils.signature import signature_auth_handler
from pkg.logger import logger, span_context
from pkg import request_context as context
from pkg.toolkit.middleware import BaseMiddlewareContext


class _AuthConstants:
    """认证相关常量配置"""

    # HTTP 头字段名
    HEADER_AUTHORIZATION: str = "Authorization"
    HEADER_SIGNATURE: str = "X-Signature"
    HEADER_TIMESTAMP: str = "X-Timestamp"
    HEADER_NONCE: str = "X-Nonce"

    # Token 前缀
    BEARER_PREFIX: str = "Bearer "

    # 路径前缀 (认证策略说明)
    PATH_PUBLIC: str = "/v1/public"  # 公共API，无需认证
    PATH_INTERNAL: str = "/v1/internal"  # 内部API，签名认证
    # 其他所有路径默认 Token 认证

    # span 名称
    SPAN_WHITELIST: str = "middleware.auth.whitelist"
    SPAN_INTERNAL: str = "middleware.auth.internal"
    SPAN_TOKEN: str = "middleware.auth.token"

    # 白名单路径 (精确匹配)
    WHITELIST_PATHS: frozenset[str] = frozenset(
        {
            "/auth/login",
            "/auth/register",
            "/auth/wechat/login",  # 微信登录
            "/docs",
            "/openapi.json",
        }
    )


# 全局常量实例
_AUTH_CONST = _AuthConstants()


class _AuthContext(BaseMiddlewareContext):
    """认证上下文,封装认证过程中的状态变量"""

    def is_whitelist(self) -> bool:
        """判断是否在白名单中"""
        return (
            self.path.startswith(_AUTH_CONST.PATH_PUBLIC)
            or self.path in _AUTH_CONST.WHITELIST_PATHS
        )

    def is_internal_api(self) -> bool:
        """判断是否为内部接口"""
        return self.path.startswith(_AUTH_CONST.PATH_INTERNAL)

    def get_signature_headers(self) -> tuple[str | None, str | None, str | None]:
        """获取签名相关头信息"""
        return (
            self.headers.get(_AUTH_CONST.HEADER_SIGNATURE),
            self.headers.get(_AUTH_CONST.HEADER_TIMESTAMP),
            self.headers.get(_AUTH_CONST.HEADER_NONCE),
        )

    def get_token(self) -> str | None:
        """从请求头中提取 token"""
        auth_header = self.headers.get(_AUTH_CONST.HEADER_AUTHORIZATION, "")

        # 兼容 Bearer Token
        if auth_header.startswith(_AUTH_CONST.BEARER_PREFIX):
            return auth_header[len(_AUTH_CONST.BEARER_PREFIX) :]

        return auth_header if auth_header else None


class ASGIAuthMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # 初始化认证上下文
        auth_ctx = _AuthContext(scope)

        # 1. 白名单放行
        if auth_ctx.is_whitelist():
            async with span_context(_AUTH_CONST.SPAN_WHITELIST):
                logger.debug(f"Whitelist path: {auth_ctx.path}")
                context.set_val(context.ContextKey.USER_ID, 0)
            await self.app(scope, receive, send)
            return

        # 2. 内部接口签名校验
        if auth_ctx.is_internal_api():
            async with span_context(_AUTH_CONST.SPAN_INTERNAL):
                logger.debug(f"Internal API access: {auth_ctx.path}")
                await self._handle_internal_auth(auth_ctx)
            await self.app(scope, receive, send)
            return

        # 3. Token 校验
        async with span_context(_AUTH_CONST.SPAN_TOKEN):
            logger.debug(f"Token auth for path: {auth_ctx.path}")
            await self._handle_token_auth(auth_ctx)
        await self.app(scope, receive, send)

    async def _handle_internal_auth(self, auth_ctx: _AuthContext) -> None:
        """处理内部接口签名认证"""
        x_signature, x_timestamp, x_nonce = auth_ctx.get_signature_headers()

        if not signature_auth_handler.verify(
            x_signature=x_signature, x_timestamp=x_timestamp, x_nonce=x_nonce
        ):
            raise AppException(
                errors.InvalidSignature,
                message=f"Signature authentication failed, x_signature={x_signature}, x_timestamp={x_timestamp}, x_nonce={x_nonce}",
            )

        logger.debug(f"Internal API signature verified: {auth_ctx.path}")

    async def _handle_token_auth(self, auth_ctx: _AuthContext) -> None:
        """处理 Token 认证 (基于 Redis 缓存校验)"""
        token = auth_ctx.get_token()

        if not token:
            raise AppException(errors.Unauthorized, message="invalid or missing token")

        logger.debug(f"Verifying token: {token[:10]}...")
        auth_metadata = await new_auth_service().verify_token(token)

        user_id = auth_metadata.get("id")
        if not isinstance(user_id, int):
            raise AppException(
                errors.Unauthorized, message="Invalid user_id in token metadata"
            )

        # 设置用户上下文
        logger.debug(f"Set user_id to context: {user_id}")
        context.set_val(context.ContextKey.USER_ID, user_id)
