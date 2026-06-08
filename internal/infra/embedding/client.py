from __future__ import annotations

from internal.config import settings
from internal.core import AppException, errors
from pkg.vectors.embedders import create_embedder
from pkg.vectors.embedders.base import Embedder

_NO_AUTH_API_KEY = "not-required"


def new_default_embedder() -> Embedder:
    """根据默认 embedding 配置创建 OpenAI-compatible Embedder。"""
    provider = settings.EMBEDDING_PROVIDER
    base_url = settings.EMBEDDING_BASE_URL
    model = settings.EMBEDDING_MODEL
    api_key = settings.EMBEDDING_API_KEY.get_secret_value()

    if provider != "openai_compatible" or not base_url or not model:
        raise AppException(
            errors.ServiceUnavailable,
            message=f"Embedding provider '{provider}' is not configured",
        )

    return create_embedder(
        provider=provider,
        api_key=api_key or _NO_AUTH_API_KEY,
        base_url=base_url,
        model=model,
        dimension=settings.EMBEDDING_DIMENSION,
        timeout=settings.EMBEDDING_TIMEOUT_SECONDS,
    )
