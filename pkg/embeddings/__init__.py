from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pkg.embeddings.base import BaseEmbedder, Embedder, EmbedderProvider
from pkg.embeddings.errors import (
    EmbeddingCoreError,
    EmbeddingResponseValidationError,
    InvalidEmbeddingDimensionError,
)
from pkg.embeddings.openai_compatible import (
    OpenAICompatibleEmbedder,
    VECTOR_DIMENSION,
    create_openai_compatible_embedder,
)

EMBEDDER_BUILDERS: dict[EmbedderProvider, Callable[..., Embedder]] = {
    EmbedderProvider.OPENAI_COMPATIBLE: create_openai_compatible_embedder,
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
    "OpenAICompatibleEmbedder",
    "VECTOR_DIMENSION",
    "create_embedder",
    "create_openai_compatible_embedder",
]
