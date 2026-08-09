from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Request

from internal.core import AppException, errors
from internal.services.auth import AuthService, new_auth_service
from internal.utils.signature import signature_auth_handler
from pkg import request_context as context
from pkg.logger import logger
from pkg.tracing import span_context

_INTERNAL_AUTH_SPAN_NAME = "middleware.auth.internal"
_TOKEN_AUTH_SPAN_NAME = "middleware.auth.token"

OptionalSignatureHeader = Annotated[str | None, Header(alias="X-Signature")]
OptionalTimestampHeader = Annotated[str | None, Header(alias="X-Timestamp")]
OptionalNonceHeader = Annotated[str | None, Header(alias="X-Nonce")]
OptionalAuthorizationHeader = Annotated[str | None, Header(alias="Authorization")]


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    """当前请求通过 Token 认证后得到的最小身份信息。"""

    user_id: int
    token: str


def _extract_token(authorization: str | None) -> str | None:
    if authorization is None:
        return None
    if authorization.startswith("Bearer "):
        return authorization[len("Bearer ") :]
    return authorization


async def require_authenticated_user(
    request: Request,
    auth_service: Annotated[AuthService, Depends(new_auth_service)],
    authorization: OptionalAuthorizationHeader = None,
) -> AuthenticatedPrincipal:
    """校验用户 Token，并把用户 ID 写入当前请求上下文。"""
    async with span_context(_TOKEN_AUTH_SPAN_NAME):
        logger.debug(f"Token auth for path: {request.url.path}")
        token = _extract_token(authorization)
        if not token:
            raise AppException(errors.Unauthorized, message="invalid or missing token")

        auth_metadata = await auth_service.verify_token(token)
        user_id = auth_metadata.get("id")
        if not isinstance(user_id, int):
            raise AppException(
                errors.Unauthorized,
                message="Invalid user_id in token metadata",
            )

        context.set_user_id(user_id)
        logger.debug(f"Set user_id to context: {user_id}")
        return AuthenticatedPrincipal(user_id=user_id, token=token)


AuthenticatedUserDependency = Depends(require_authenticated_user)


async def require_internal_signature(
    request: Request,
    x_signature: OptionalSignatureHeader = None,
    x_timestamp: OptionalTimestampHeader = None,
    x_nonce: OptionalNonceHeader = None,
) -> None:
    """校验内部接口签名，不建立登录用户上下文。"""
    async with span_context(_INTERNAL_AUTH_SPAN_NAME):
        logger.debug(f"Internal API access: {request.url.path}")
        if not signature_auth_handler.verify(
            x_signature=x_signature,
            x_timestamp=x_timestamp,
            x_nonce=x_nonce,
        ):
            raise AppException(
                errors.InvalidSignature,
                message="Signature authentication failed",
            )

        logger.debug(f"Internal API signature verified: {request.url.path}")


InternalSignatureDependency = Depends(require_internal_signature)
