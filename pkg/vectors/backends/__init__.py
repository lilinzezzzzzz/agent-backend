from __future__ import annotations

from typing import Any

from pkg.vectors.backends.base import (
    BackendProvider,
    BaseVectorBackend,
    CollectionSpec,
    IsolationMode,
    MetricType,
    ScalarDataType,
    ScalarFieldSpec,
    VectorBackend,
)
from pkg.vectors.backends.milvus import MilvusBackend, create_milvus_backend
from pkg.vectors.backends.milvus.types import FullTextSearchSpec, MilvusCollectionSpec
from pkg.vectors.contracts import ConsistencyLevel


def create_backend(*, provider: BackendProvider, **kwargs: Any) -> VectorBackend:
    provider_enum = BackendProvider.is_valid(provider)
    if provider_enum == BackendProvider.MILVUS:
        return create_milvus_backend(**kwargs)
    raise ValueError(f"unsupported backend provider: {provider_enum}")


__all__ = [
    "BackendProvider",
    "BaseVectorBackend",
    "CollectionSpec",
    "ConsistencyLevel",
    "FullTextSearchSpec",
    "IsolationMode",
    "MetricType",
    "MilvusBackend",
    "MilvusCollectionSpec",
    "ScalarDataType",
    "ScalarFieldSpec",
    "VectorBackend",
    "create_backend",
    "create_milvus_backend",
]
