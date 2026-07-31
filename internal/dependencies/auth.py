from typing import Annotated

from fastapi import Header, Request

from internal.core import AppException, errors
from internal.utils.signature import signature_auth_handler
from pkg.logger import logger
from pkg.tracing import span_context

_INTERNAL_AUTH_SPAN_NAME = "middleware.auth.internal"

OptionalSignatureHeader = Annotated[str | None, Header(alias="X-Signature")]
OptionalTimestampHeader = Annotated[str | None, Header(alias="X-Timestamp")]
OptionalNonceHeader = Annotated[str | None, Header(alias="X-Nonce")]


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
