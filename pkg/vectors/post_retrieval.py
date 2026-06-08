from __future__ import annotations

import inspect
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable, Sequence
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from pkg.vectors.contracts import RetrievalMode, ScalarValue, SearchHit

type ChunkReranker = Callable[..., list[SearchHit] | Awaitable[list[SearchHit]]]
type DocumentReranker = Callable[..., list["CollapsedSearchHit"] | Awaitable[list["CollapsedSearchHit"]]]


class DedupKeepStrategy(StrEnum):
    FIRST = "first"
    BEST_SCORE = "best_score"


class ScoreAggregation(StrEnum):
    MAX = "max"
    SUM = "sum"
    MEAN = "mean"


class DedupConfig(BaseModel, extra="forbid"):
    enabled: bool = True
    key_fields: list[str] = Field(default_factory=lambda: ["id"])
    keep_strategy: DedupKeepStrategy = DedupKeepStrategy.BEST_SCORE


class CollapseConfig(BaseModel, extra="forbid"):
    enabled: bool = True
    key_fields: list[str] = Field(
        default_factory=lambda: [
            "metadata.doc_id",
            "payload.doc_id",
            "metadata.document_id",
            "payload.document_id",
            "metadata.source_id",
            "payload.source_id",
        ]
    )
    max_chunks_per_document: int = Field(default=3, gt=0)
    max_documents: int | None = Field(default=None, gt=0)
    score_aggregation: ScoreAggregation = ScoreAggregation.MAX
    fallback_to_chunk_id: bool = True


class PostRetrievalConfig(BaseModel, extra="forbid"):
    dedup: DedupConfig = Field(default_factory=DedupConfig)
    collapse: CollapseConfig = Field(default_factory=CollapseConfig)


class CollapsedSearchHit(BaseModel, extra="forbid"):
    document_key: ScalarValue | str
    primary_hit_id: int = Field(gt=0)
    hit_count: int = Field(gt=0)
    chunk_ids: list[int] = Field(default_factory=list)
    text: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)
    retrieval_mode: RetrievalMode | None = None
    relevance_score: float | None = None
    raw_score: float | None = None
    chunks: list[SearchHit] = Field(default_factory=list)


class PostRetrievalStats(BaseModel, extra="forbid"):
    input_hit_count: int = Field(ge=0)
    deduped_hit_count: int = Field(ge=0)
    document_count: int = Field(ge=0)
    returned_document_count: int = Field(ge=0)


class PostRetrievalResult(BaseModel, extra="forbid"):
    hits: list[SearchHit] = Field(default_factory=list)
    documents: list[CollapsedSearchHit] = Field(default_factory=list)
    stats: PostRetrievalStats


class PostRetrievalPipeline:
    def __init__(
        self,
        *,
        config: PostRetrievalConfig | None = None,
        chunk_reranker: ChunkReranker | None = None,
        document_reranker: DocumentReranker | None = None,
    ) -> None:
        self._config = config or PostRetrievalConfig()
        self._chunk_reranker = chunk_reranker
        self._document_reranker = document_reranker

    @property
    def config(self) -> PostRetrievalConfig:
        return self._config

    async def run(
        self,
        *,
        hits: Sequence[SearchHit],
        query_text: str | None = None,
        query_vector: list[float] | None = None,
    ) -> PostRetrievalResult:
        ordered_hits = self._sort_hits(hits)
        deduped_hits = self._deduplicate_hits(ordered_hits)
        reranked_hits = await self._rerank_hits(
            hits=deduped_hits,
            query_text=query_text,
            query_vector=query_vector,
        )
        documents = self._collapse_hits(reranked_hits)
        reranked_documents = await self._rerank_documents(
            documents=documents,
            query_text=query_text,
            query_vector=query_vector,
        )
        if self._config.collapse.max_documents is not None:
            reranked_documents = reranked_documents[: self._config.collapse.max_documents]

        return PostRetrievalResult(
            hits=reranked_hits,
            documents=reranked_documents,
            stats=PostRetrievalStats(
                input_hit_count=len(hits),
                deduped_hit_count=len(reranked_hits),
                document_count=len(documents),
                returned_document_count=len(reranked_documents),
            ),
        )

    async def _rerank_hits(
        self,
        *,
        hits: list[SearchHit],
        query_text: str | None,
        query_vector: list[float] | None,
    ) -> list[SearchHit]:
        if self._chunk_reranker is None:
            return hits
        reranked = self._chunk_reranker(
            hits=list(hits),
            query_text=query_text,
            query_vector=query_vector,
        )
        return list(await self._maybe_await(reranked))

    async def _rerank_documents(
        self,
        *,
        documents: list[CollapsedSearchHit],
        query_text: str | None,
        query_vector: list[float] | None,
    ) -> list[CollapsedSearchHit]:
        if self._document_reranker is None:
            return self._sort_documents(documents)
        reranked = self._document_reranker(
            documents=list(documents),
            query_text=query_text,
            query_vector=query_vector,
        )
        return list(await self._maybe_await(reranked))

    def _deduplicate_hits(self, hits: Sequence[SearchHit]) -> list[SearchHit]:
        if not self._config.dedup.enabled:
            return list(hits)

        unique_hits: dict[str, SearchHit] = {}
        for hit in hits:
            dedup_key = self._resolve_dedup_key(hit=hit)
            if dedup_key not in unique_hits:
                unique_hits[dedup_key] = hit
                continue
            if self._config.dedup.keep_strategy == DedupKeepStrategy.BEST_SCORE and self._is_better_hit(
                candidate=hit,
                current=unique_hits[dedup_key],
            ):
                unique_hits[dedup_key] = hit

        return self._sort_hits(unique_hits.values())

    def _collapse_hits(self, hits: Sequence[SearchHit]) -> list[CollapsedSearchHit]:
        if not hits:
            return []

        grouped_hits: dict[ScalarValue | str, list[SearchHit]] = defaultdict(list)
        for hit in hits:
            document_key = self._resolve_document_key(hit=hit)
            grouped_hits[document_key].append(hit)

        documents: list[CollapsedSearchHit] = []
        for document_key, grouped in grouped_hits.items():
            sorted_group = self._sort_hits(grouped)
            kept_chunks = sorted_group[: self._config.collapse.max_chunks_per_document]
            primary_hit = sorted_group[0]
            documents.append(
                CollapsedSearchHit(
                    document_key=document_key,
                    primary_hit_id=primary_hit.id,
                    hit_count=len(sorted_group),
                    chunk_ids=[chunk.id for chunk in kept_chunks],
                    text=primary_hit.text,
                    metadata=dict(primary_hit.metadata),
                    payload=dict(primary_hit.payload),
                    retrieval_mode=primary_hit.retrieval_mode,
                    relevance_score=self._aggregate_scores(
                        [hit.relevance_score for hit in sorted_group],
                    ),
                    raw_score=self._aggregate_scores(
                        [hit.raw_score for hit in sorted_group],
                    ),
                    chunks=kept_chunks,
                )
            )
        return self._sort_documents(documents)

    def _resolve_dedup_key(self, *, hit: SearchHit) -> str:
        for field_path in self._config.dedup.key_fields:
            value = self._resolve_hit_value(hit=hit, field_path=field_path)
            if value is not None:
                return f"{field_path}:{value}"
        return f"id:{hit.id}"

    def _resolve_document_key(self, *, hit: SearchHit) -> ScalarValue | str:
        if not self._config.collapse.enabled:
            return f"hit:{hit.id}"

        for field_path in self._config.collapse.key_fields:
            value = self._resolve_hit_value(hit=hit, field_path=field_path)
            if isinstance(value, (str, int, float, bool)):
                return value

        if self._config.collapse.fallback_to_chunk_id:
            return f"hit:{hit.id}"
        raise ValueError(f"无法为 SearchHit 解析 document key: hit_id={hit.id}")

    def _resolve_hit_value(
        self,
        *,
        hit: SearchHit,
        field_path: str,
    ) -> Any:
        current: Any = hit
        for part in field_path.split("."):
            if isinstance(current, BaseModel):
                current = getattr(current, part, None)
            elif isinstance(current, dict):
                current = current.get(part)
            else:
                current = getattr(current, part, None)
            if current is None:
                return None
        return current

    def _aggregate_scores(self, scores: Sequence[float | None]) -> float | None:
        available_scores = [score for score in scores if score is not None]
        if not available_scores:
            return None

        if self._config.collapse.score_aggregation == ScoreAggregation.MAX:
            return max(available_scores)
        if self._config.collapse.score_aggregation == ScoreAggregation.SUM:
            return sum(available_scores)
        return sum(available_scores) / len(available_scores)

    @staticmethod
    def _hit_sort_key(hit: SearchHit) -> tuple[float, float, int]:
        relevance = hit.relevance_score if hit.relevance_score is not None else float("-inf")
        raw = hit.raw_score if hit.raw_score is not None else float("-inf")
        return relevance, raw, -hit.id

    def _sort_hits(self, hits: Iterable[SearchHit]) -> list[SearchHit]:
        return sorted(hits, key=self._hit_sort_key, reverse=True)

    @staticmethod
    def _document_sort_key(document: CollapsedSearchHit) -> tuple[float, float, int]:
        relevance = document.relevance_score if document.relevance_score is not None else float("-inf")
        raw = document.raw_score if document.raw_score is not None else float("-inf")
        return relevance, raw, -document.primary_hit_id

    def _sort_documents(
        self,
        documents: Sequence[CollapsedSearchHit],
    ) -> list[CollapsedSearchHit]:
        return sorted(documents, key=self._document_sort_key, reverse=True)

    @staticmethod
    def _is_better_hit(*, candidate: SearchHit, current: SearchHit) -> bool:
        return PostRetrievalPipeline._hit_sort_key(candidate) > PostRetrievalPipeline._hit_sort_key(current)

    @staticmethod
    async def _maybe_await[T](value: T | Awaitable[T]) -> T:
        if inspect.isawaitable(value):
            return await value
        return value
