from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field
from pymilvus import RRFRanker, WeightedRanker

from pkg.vectors.backends.base import CollectionSpec, EmptyIndexParams, IndexConfig, IndexParams
from pkg.vectors.contracts import RetrievalMode

MILVUS_HOST = "localhost"
MILVUS_PORT = 19530
DEFAULT_HNSW_SEARCH_EF = 64


class MilvusDenseIndexType(StrEnum):
    FLAT = "FLAT"
    HNSW = "HNSW"
    IVF_FLAT = "IVF_FLAT"
    IVF_SQ8 = "IVF_SQ8"
    IVF_PQ = "IVF_PQ"
    DISKANN = "DISKANN"


class MilvusSparseIndexType(StrEnum):
    SPARSE_INVERTED_INDEX = "SPARSE_INVERTED_INDEX"


class MilvusConsistencyLevel(StrEnum):
    STRONG = "Strong"
    SESSION = "Session"
    BOUNDED = "Bounded"
    EVENTUALLY = "Eventually"
    CUSTOMIZED = "Customized"


class MilvusHnswIndexParams(IndexParams):
    M: int | None = Field(default=None, gt=0)
    efConstruction: int | None = Field(default=None, gt=0)


class MilvusIvfIndexParams(IndexParams):
    nlist: int | None = Field(default=None, gt=0)


class MilvusIvfPqIndexParams(MilvusIvfIndexParams):
    m: int | None = Field(default=None, gt=0)
    nbits: int | None = Field(default=None, gt=0)


class MilvusSparseInvertedIndexParams(IndexParams):
    drop_ratio_build: float | None = Field(default=None, ge=0.0, le=1.0)


class MilvusFlatIndexConfig(IndexConfig, extra="forbid"):
    index_type: Literal[MilvusDenseIndexType.FLAT] = MilvusDenseIndexType.FLAT
    params: EmptyIndexParams = Field(default_factory=EmptyIndexParams)


class MilvusHnswIndexConfig(IndexConfig, extra="forbid"):
    index_type: Literal[MilvusDenseIndexType.HNSW] = MilvusDenseIndexType.HNSW
    params: MilvusHnswIndexParams = Field(default_factory=MilvusHnswIndexParams)


class MilvusIvfFlatIndexConfig(IndexConfig, extra="forbid"):
    index_type: Literal[MilvusDenseIndexType.IVF_FLAT] = MilvusDenseIndexType.IVF_FLAT
    params: MilvusIvfIndexParams = Field(default_factory=MilvusIvfIndexParams)


class MilvusIvfSq8IndexConfig(IndexConfig, extra="forbid"):
    index_type: Literal[MilvusDenseIndexType.IVF_SQ8] = MilvusDenseIndexType.IVF_SQ8
    params: MilvusIvfIndexParams = Field(default_factory=MilvusIvfIndexParams)


class MilvusIvfPqIndexConfig(IndexConfig, extra="forbid"):
    index_type: Literal[MilvusDenseIndexType.IVF_PQ] = MilvusDenseIndexType.IVF_PQ
    params: MilvusIvfPqIndexParams = Field(default_factory=MilvusIvfPqIndexParams)


class MilvusDiskAnnIndexConfig(IndexConfig, extra="forbid"):
    index_type: Literal[MilvusDenseIndexType.DISKANN] = MilvusDenseIndexType.DISKANN
    params: EmptyIndexParams = Field(default_factory=EmptyIndexParams)


type MilvusIndexConfig = Annotated[
    MilvusFlatIndexConfig
    | MilvusHnswIndexConfig
    | MilvusIvfFlatIndexConfig
    | MilvusIvfSq8IndexConfig
    | MilvusIvfPqIndexConfig
    | MilvusDiskAnnIndexConfig,
    Field(discriminator="index_type"),
]


class MilvusSparseInvertedIndexConfig(IndexConfig, extra="forbid"):
    index_type: Literal[MilvusSparseIndexType.SPARSE_INVERTED_INDEX] = MilvusSparseIndexType.SPARSE_INVERTED_INDEX
    params: MilvusSparseInvertedIndexParams = Field(default_factory=MilvusSparseInvertedIndexParams)


type MilvusSparseIndexConfig = Annotated[
    MilvusSparseInvertedIndexConfig,
    Field(discriminator="index_type"),
]


class FullTextSearchSpec(BaseModel, extra="forbid"):
    """Milvus BM25 / full-text search 配置。"""

    enabled: bool = False
    sparse_vector_field: str = Field(default="text_sparse", min_length=1)
    function_name: str = Field(default="text_bm25_emb", min_length=1)
    analyzer_params: dict[str, object] = Field(default_factory=dict)
    function_params: dict[str, object] = Field(default_factory=dict)
    index_config: MilvusSparseIndexConfig = Field(default_factory=MilvusSparseInvertedIndexConfig)
    description: str = ""


class MilvusCollectionSpec(CollectionSpec, extra="forbid"):
    """Milvus collection 规格定义。"""

    text_max_length: int = Field(default=65_535, gt=0)
    index_config: MilvusIndexConfig = Field(default_factory=MilvusHnswIndexConfig)
    full_text_search: FullTextSearchSpec = Field(default_factory=FullTextSearchSpec)
    consistency_level: MilvusConsistencyLevel = MilvusConsistencyLevel.SESSION
    enable_dynamic_field: bool = False


@dataclass(frozen=True)
class SearchBranchPlan:
    name: str
    anns_field: str
    data: list[object]
    search_params: dict[str, object]
    limit: int


@dataclass(frozen=True)
class SearchExecutionPlan:
    mode: RetrievalMode
    expr: str
    output_fields: tuple[str, ...]
    consistency_level: str
    final_limit: int
    branches: tuple[SearchBranchPlan, ...]
    candidate_limit: int | None = None
    ranker: RRFRanker | WeightedRanker | None = None

    @property
    def branch_names(self) -> tuple[str, ...]:
        return tuple(branch.name for branch in self.branches)


__all__ = [
    "DEFAULT_HNSW_SEARCH_EF",
    "FullTextSearchSpec",
    "MILVUS_HOST",
    "MILVUS_PORT",
    "MilvusCollectionSpec",
    "MilvusConsistencyLevel",
    "MilvusDenseIndexType",
    "MilvusDiskAnnIndexConfig",
    "MilvusFlatIndexConfig",
    "MilvusHnswIndexConfig",
    "MilvusHnswIndexParams",
    "MilvusIndexConfig",
    "MilvusIvfFlatIndexConfig",
    "MilvusIvfIndexParams",
    "MilvusIvfPqIndexConfig",
    "MilvusIvfPqIndexParams",
    "MilvusIvfSq8IndexConfig",
    "MilvusSparseIndexConfig",
    "MilvusSparseIndexType",
    "MilvusSparseInvertedIndexConfig",
    "MilvusSparseInvertedIndexParams",
    "SearchBranchPlan",
    "SearchExecutionPlan",
]
