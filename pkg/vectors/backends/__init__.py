from __future__ import annotations

from collections.abc import Callable
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
from pkg.vectors.backends.zvec import ZvecBackend, create_zvec_backend
from pkg.vectors.backends.zvec.specs import ZvecCollectionSpec
from pkg.vectors.contracts import ConsistencyLevel

BACKEND_BUILDERS: dict[BackendProvider, Callable[..., VectorBackend]] = {
    BackendProvider.MILVUS: create_milvus_backend,
    BackendProvider.ZVEC: create_zvec_backend,
}


def create_backend(*, provider: BackendProvider, **kwargs: Any) -> VectorBackend:
    provider_enum = BackendProvider.is_valid(provider)
    try:
        builder = BACKEND_BUILDERS[provider_enum]
    except KeyError as exc:
        raise ValueError(f"unsupported backend provider: {provider_enum}") from exc
    return builder(**kwargs)


__all__ = [
    "BACKEND_BUILDERS",
    "BaseVectorBackend",
    "BackendProvider",
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
    "ZvecBackend",
    "ZvecCollectionSpec",
    "create_backend",
    "create_zvec_backend",
]
