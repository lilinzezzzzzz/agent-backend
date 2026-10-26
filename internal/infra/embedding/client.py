from __future__ import annotations

from internal.config import settings
from internal.core import AppException, errors
from pkg.embeddings import create_embedder
from pkg.embeddings.base import Embedder

_NO_AUTH_API_KEY = "not-required"


def new_default_embedder() -> Embedder:
    """根据默认 embedding 配置创建 OpenAI-compatible Embedder。"""
    provider = settings.embedding.provider
    base_url = settings.embedding.base_url
    model = settings.embedding.model
    api_key = settings.secrets.embedding_api_key.get_secret_value()

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
        dimension=settings.embedding.dimension,
        timeout=settings.embedding.timeout_seconds,
    )
