from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pkg.embeddings.base import BaseEmbedder, Embedder, EmbedderProvider
from pkg.embeddings.errors import (
    EmbeddingCoreError,
    EmbeddingResponseValidationError,
    InvalidEmbeddingDimensionError,
)


def _build_openai_embedder(**kwargs: Any) -> Embedder:
    # 实现依赖本包的基础接口；延迟导入避免包初始化时形成循环依赖。
    from pkg.llm.openai_client.embeddings import create_openai_embedder

    return create_openai_embedder(**kwargs)


EMBEDDER_BUILDERS: dict[EmbedderProvider, Callable[..., Embedder]] = {
    EmbedderProvider.OPENAI_COMPATIBLE: _build_openai_embedder,
}


def create_embedder(*, provider: EmbedderProvider | str, **kwargs: Any) -> Embedder:
    provider_enum = EmbedderProvider.is_valid(provider)
    try:
        builder = EMBEDDER_BUILDERS[provider_enum]
    except KeyError as exc:
        raise ValueError(f"unsupported embedder provider: {provider_enum}") from exc
    return builder(**kwargs)


__all__ = [
    "BaseEmbedder",
    "EMBEDDER_BUILDERS",
    "EmbeddingCoreError",
    "EmbeddingResponseValidationError",
    "Embedder",
    "EmbedderProvider",
    "InvalidEmbeddingDimensionError",
    "create_embedder",
]
