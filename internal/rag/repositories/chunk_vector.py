from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pkg.vectors.backends.base import (
    BackendProvider,
    CollectionName,
    MetricType,
    ScalarDataType,
    ScalarFieldSpec,
    VectorBackend,
)
from pkg.vectors.backends.milvus.types import MilvusCollectionSpec
from pkg.vectors.contracts import (
    FilterCondition,
    FilterOperator,
    ScalarValue,
    SearchHit,
    VectorRecord,
)
from pkg.embeddings.base import Embedder
from pkg.vectors.repositories.base import (
    BaseVectorRepository,
    ScopeValue,
    build_scalar_filters,
)

CHUNK_ALLOWED_FILTER_FIELDS = frozenset({"doc_id", "kb_id", "domain"})


@dataclass(slots=True)
class ChunkVectorDocument:
    id: int
    doc_id: int
    domain: str
    chunk_index: int
    text: str
    kb_id: int | None = None


class ChunkVectorRepository(BaseVectorRepository[ChunkVectorDocument]):
    def __init__(
        self,
        *,
        backend: VectorBackend,
        embedder: Embedder,
        scope_value: ScopeValue | None = None,
        collection_name: str = CollectionName.CHUNKS,
        dimension: int = 1024,
    ) -> None:
        super().__init__(backend=backend, embedder=embedder, scope_value=scope_value)
        self._collection_spec = MilvusCollectionSpec.model_validate(
            {
                "name": collection_name,
                "dimension": dimension,
                "metric_type": MetricType.COSINE,
                "payload_field": None,
                "scalar_fields": [
                    ScalarFieldSpec(name="doc_id", data_type=ScalarDataType.INT64),
                    ScalarFieldSpec(
                        name="kb_id", data_type=ScalarDataType.INT64, nullable=True
                    ),
                    ScalarFieldSpec(
                        name="domain",
                        data_type=ScalarDataType.STRING,
                        max_length=64,
                    ),
                    ScalarFieldSpec(
                        name="chunk_index",
                        data_type=ScalarDataType.INT64,
                    ),
                ],
                "index_config": {
                    "index_type": "HNSW",
                    "params": {"M": 30, "efConstruction": 200},
                },
                "full_text_search": {"enabled": True},
                "description": "Knowledge chunk vector collection",
            }
        )

    @property
    def collection_spec(self) -> MilvusCollectionSpec:
        return self._collection_spec

    @property
    def scope_field(self) -> str | None:
        return None

    @staticmethod
    def build_default_search_filters() -> list[FilterCondition]:
        return []

    def to_records(self, *, entity: ChunkVectorDocument) -> list[VectorRecord]:
        metadata: dict[str, ScalarValue] = {
            "doc_id": entity.doc_id,
            "domain": entity.domain,
            "chunk_index": entity.chunk_index,
        }
        if entity.kb_id is not None:
            metadata["kb_id"] = entity.kb_id
        return [
            VectorRecord(
                id=entity.id,
                text=entity.text,
                metadata=metadata,
            )
        ]

    async def delete_chunks(self, chunk_ids: list[int]) -> int:
        # 调用方已在业务层完成知识库/文档级校验，这里直接走主键删除以命中 backend 的快路径。
        return await self.backend.delete(
            spec=self.collection_spec,
            ids=chunk_ids,
        )

    async def search_chunks(
        self,
        *,
        query_text: str,
        top_k: int,
        similarity_threshold: float | None = None,
        filters: tuple[FilterCondition, ...] = (),
        include_payload: bool = True,
    ) -> list[SearchHit]:
        hits = await self.search_by_text(
            query_text=query_text,
            top_k=top_k,
            filters=filters,
            include_payload=include_payload,
        )
        if similarity_threshold is None:
            return hits
        return [
            hit
            for hit in hits
            if hit.relevance_score is not None
            and hit.relevance_score >= similarity_threshold
        ]

    async def search_similar(
        self,
        *,
        query_text: str,
        top_k: int = 10,
        similarity_threshold: float = 0.45,
        document_id: int | None = None,
        data_type: str | None = None,
        status: str | None = None,
        filters: dict[str, Any] | None = None,
        query_embedding: list[float] | None = None,
    ) -> list[dict[str, Any]]:
        if data_type is not None or status is not None:
            raise ValueError(
                "chunk collection 仅保留 id/doc_id/kb_id/domain/chunk_index/content，不支持 data_type/status 过滤"
            )

        raw_filters = dict(filters or {})
        invalid_fields = sorted(set(raw_filters) - CHUNK_ALLOWED_FILTER_FIELDS)
        if invalid_fields:
            raise ValueError(
                f"chunk collection 仅支持按 doc_id/kb_id/domain 过滤，不支持字段: {', '.join(invalid_fields)}"
            )
        filter_conditions = list(build_scalar_filters(raw_filters))
        if document_id is not None:
            filter_conditions.append(
                FilterCondition(field="doc_id", op=FilterOperator.EQ, value=document_id)
            )

        if query_embedding is None:
            hits = await self.search_by_text(
                query_text=query_text,
                top_k=top_k,
                filters=tuple(filter_conditions),
            )
        else:
            hits = await self.search_by_vector(
                query_vector=query_embedding,
                top_k=top_k,
                filters=tuple(filter_conditions),
            )

        results: list[dict[str, Any]] = []
        for hit in hits:
            score = hit.relevance_score
            if score is None or score < similarity_threshold:
                continue
            metadata = dict(hit.metadata)
            metadata.setdefault("collection", "chunk")
            results.append(
                {
                    "id": hit.id,
                    "doc_id": metadata.get("doc_id"),
                    "kb_id": metadata.get("kb_id"),
                    "content": hit.text or "",
                    "score": score,
                    "metadata": metadata,
                }
            )
        return results


async def new_chunk_repository(
    *, embedder: Embedder, scope_value: ScopeValue | None = None
) -> ChunkVectorRepository:
    from pkg.vectors.backends import create_backend

    repository = ChunkVectorRepository(
        backend=create_backend(provider=BackendProvider.MILVUS),
        embedder=embedder,
        scope_value=scope_value,
    )
    await repository.ensure_collection()
    return repository
