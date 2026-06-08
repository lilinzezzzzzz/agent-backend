from __future__ import annotations

import hashlib
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Protocol

import anyio
from pydantic import BaseModel, Field

from internal.config import settings
from internal.core import AppException, errors
from internal.dao.rag import RagChunkMetadata, RagMetadataDao, new_rag_metadata_dao
from internal.services.dto.rag import (
    PreparedRetrievalQueryDTO,
    RagAnswerDTO,
    RagCitationDTO,
    RagEvidenceDTO,
    RagExternalRunDTO,
    RagRetrievalDTO,
)
from internal.vectors.chunks import ChunkVectorRepository, new_chunk_repository
from pkg.logger import logger
from pkg.toolkit import context
from pkg.toolkit.string import uuid6_unique_str_id
from pkg.vectors.contracts import (
    FilterCondition,
    FilterOperator,
    RetrievalMode,
    SearchHit,
)
from pkg.vectors.post_retrieval import (
    CollapseConfig,
    PostRetrievalConfig,
    PostRetrievalPipeline,
    PostRetrievalResult,
)

type RagRepositoryFactory = Callable[..., Awaitable[ChunkVectorRepository]]

_DEFAULT_TOP_K = 20
_DEFAULT_FINAL_K = 5
_MAX_CONTEXT_EVIDENCE_CHARS = 2000
_MAX_ACTION_EXCERPT_CHARS = 800
_LOW_CONFIDENCE_ANSWER = "没有找到足够证据，暂时无法基于知识库确认这个问题。"
_EMPTY_SCOPE_ERROR = "rag_scope_empty"
_NO_EVIDENCE_ERROR = "rag_no_evidence"
_INVALID_CITATION_ERROR = "rag_invalid_citation"


class RagScopeResolver(Protocol):
    def resolve_allowed_domains(
        self, *, user_id: int, requested_context: Mapping[str, Any]
    ) -> set[str]: ...

    def resolve_allowed_kb_ids(
        self, *, user_id: int, requested_context: Mapping[str, Any]
    ) -> set[int]: ...


class SettingsRagScopeResolver:
    """Resolve RAG visibility from server-side settings."""

    def resolve_allowed_domains(
        self, *, user_id: int, requested_context: Mapping[str, Any]
    ) -> set[str]:
        return {domain for domain in settings.RAG_ALLOWED_DOMAINS if domain}

    def resolve_allowed_kb_ids(
        self, *, user_id: int, requested_context: Mapping[str, Any]
    ) -> set[int]:
        return {kb_id for kb_id in settings.RAG_ALLOWED_KB_IDS if kb_id > 0}


class RagLLMClient(Protocol):
    async def chat_completion_structured[StructuredOutputT: BaseModel](
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        response_model: type[StructuredOutputT],
        max_tokens: int | None = None,
        temperature: float | None = None,
        **kwargs: Any,
    ) -> StructuredOutputT: ...


class _RagLLMAnswerModel(BaseModel):
    answer: str = Field(default="")
    citations: list[str] = Field(default_factory=list)
    confidence: str = Field(default="low")


class RagService:
    def __init__(
        self,
        *,
        llm_client: RagLLMClient,
        embedder: Any,
        metadata_dao: RagMetadataDao,
        scope_resolver: RagScopeResolver | None = None,
        repository: ChunkVectorRepository | None = None,
        repository_factory: RagRepositoryFactory | None = None,
        max_context_chars: int | None = None,
    ) -> None:
        self._llm_client = llm_client
        self._embedder = embedder
        self._metadata_dao = metadata_dao
        self._scope_resolver = scope_resolver or SettingsRagScopeResolver()
        self._repository = repository
        self._repository_factory = repository_factory or new_chunk_repository
        self._repository_lock = anyio.Lock()
        self._max_context_chars = max_context_chars or settings.RAG_MAX_CONTEXT_CHARS

    async def retrieve(
        self,
        *,
        user_id: int,
        query: str,
        requested_domain: str | None = None,
        requested_kb_ids: Sequence[int] | None = None,
        top_k: int = _DEFAULT_TOP_K,
        final_k: int = _DEFAULT_FINAL_K,
        retrieval_mode: RetrievalMode = RetrievalMode.HYBRID,
    ) -> RagRetrievalDTO:
        started = time.perf_counter()
        run_id = self._new_run_id()
        trace_id = self._resolve_trace_id()
        prepared_query = await self.prepare_retrieval_query(question=query)
        top_k = self._bounded_top_k(top_k)
        final_k = self._bounded_final_k(final_k=final_k, top_k=top_k)
        scope = self._resolve_effective_scope(
            user_id=user_id,
            requested_domain=requested_domain,
            requested_kb_ids=requested_kb_ids,
        )

        if not scope.effective_domains or not scope.effective_kb_ids:
            return RagRetrievalDTO(
                run_id=run_id,
                trace_id=trace_id,
                query=prepared_query.retrieval_query,
                requested_domain=requested_domain,
                requested_kb_ids=list(requested_kb_ids)
                if requested_kb_ids is not None
                else None,
                effective_domains=scope.effective_domains,
                effective_kb_ids=scope.effective_kb_ids,
                retrieval_mode=retrieval_mode,
                latency_ms=self._elapsed_ms(started),
                errors=[_EMPTY_SCOPE_ERROR],
            )

        filters = self._build_scope_filters(scope=scope)
        repository = await self._get_repository()
        retrieval_result = await repository.retrieve_by_text(
            query_text=prepared_query.retrieval_query,
            top_k=top_k,
            filters=filters,
            include_payload=True,
            retrieval_mode=retrieval_mode,
            candidate_top_k=top_k,
            post_retrieval=PostRetrievalPipeline(
                config=PostRetrievalConfig(
                    collapse=CollapseConfig(
                        max_chunks_per_document=final_k,
                        max_documents=final_k,
                    )
                )
            ),
        )
        hits = self._select_evidence_hits(result=retrieval_result, final_k=final_k)
        evidence = await self._build_evidence_store(hits=hits)
        errors = [] if evidence else [_NO_EVIDENCE_ERROR]
        latency_ms = self._elapsed_ms(started)
        logger.info(
            "rag.retrieve completed run_id={} trace_id={} evidence_count={} latency_ms={}",
            run_id,
            trace_id,
            len(evidence),
            latency_ms,
        )
        return RagRetrievalDTO(
            run_id=run_id,
            trace_id=trace_id,
            query=prepared_query.retrieval_query,
            requested_domain=requested_domain,
            requested_kb_ids=list(requested_kb_ids)
            if requested_kb_ids is not None
            else None,
            effective_domains=scope.effective_domains,
            effective_kb_ids=scope.effective_kb_ids,
            retrieval_mode=retrieval_mode,
            evidence=evidence,
            latency_ms=latency_ms,
            errors=errors,
        )

    async def prepare_retrieval_query(
        self, *, question: str
    ) -> PreparedRetrievalQueryDTO:
        """Prepare a user question for retrieval."""
        return PreparedRetrievalQueryDTO(
            original_question=question,
            retrieval_query=self._normalize_question(question),
            expanded_queries=[],
        )

    async def answer(
        self,
        *,
        user_id: int,
        question: str,
        requested_domain: str | None = None,
        requested_kb_ids: Sequence[int] | None = None,
        top_k: int = _DEFAULT_TOP_K,
        final_k: int = _DEFAULT_FINAL_K,
        retrieval_mode: RetrievalMode = RetrievalMode.HYBRID,
        require_citations: bool = True,
        answer_on_low_confidence: bool = False,
    ) -> RagAnswerDTO:
        retrieval = await self.retrieve(
            user_id=user_id,
            query=question,
            requested_domain=requested_domain,
            requested_kb_ids=requested_kb_ids,
            top_k=top_k,
            final_k=final_k,
            retrieval_mode=retrieval_mode,
        )
        if retrieval.errors or not retrieval.evidence:
            return RagAnswerDTO(
                run_id=retrieval.run_id,
                answer=_LOW_CONFIDENCE_ANSWER,
                citations=[],
                confidence="low",
                trace_id=retrieval.trace_id,
                retrieval=retrieval,
            )

        llm_answer = await self._generate_answer(
            question=retrieval.query,
            evidence=retrieval.evidence,
        )
        citations = self._validate_citations(
            citation_ids=llm_answer.citations,
            evidence=retrieval.evidence,
        )
        if require_citations and not citations:
            errors = [*retrieval.errors, _INVALID_CITATION_ERROR]
            retrieval = RagRetrievalDTO(
                run_id=retrieval.run_id,
                trace_id=retrieval.trace_id,
                query=retrieval.query,
                requested_domain=retrieval.requested_domain,
                requested_kb_ids=retrieval.requested_kb_ids,
                effective_domains=retrieval.effective_domains,
                effective_kb_ids=retrieval.effective_kb_ids,
                retrieval_mode=retrieval.retrieval_mode,
                evidence=retrieval.evidence,
                latency_ms=retrieval.latency_ms,
                errors=errors,
            )
            if not answer_on_low_confidence:
                return RagAnswerDTO(
                    run_id=retrieval.run_id,
                    answer=_LOW_CONFIDENCE_ANSWER,
                    citations=[],
                    confidence="low",
                    trace_id=retrieval.trace_id,
                    retrieval=retrieval,
                )

        return RagAnswerDTO(
            run_id=retrieval.run_id,
            answer=llm_answer.answer or _LOW_CONFIDENCE_ANSWER,
            citations=citations,
            confidence=llm_answer.confidence or "low",
            trace_id=retrieval.trace_id,
            retrieval=retrieval,
        )

    async def run_external(
        self,
        *,
        case_id: str,
        subject_user_id: int,
        question: str,
        requested_domain: str | None = None,
        requested_kb_ids: Sequence[int] | None = None,
        top_k: int = _DEFAULT_TOP_K,
        final_k: int = _DEFAULT_FINAL_K,
        retrieval_mode: RetrievalMode = RetrievalMode.HYBRID,
        return_context: bool = False,
    ) -> RagExternalRunDTO:
        answer = await self.answer(
            user_id=subject_user_id,
            question=question,
            requested_domain=requested_domain,
            requested_kb_ids=requested_kb_ids,
            top_k=top_k,
            final_k=final_k,
            retrieval_mode=retrieval_mode,
            require_citations=True,
        )
        evidence = (
            answer.retrieval.evidence
            if return_context
            else self._strip_context(answer.retrieval.evidence)
        )
        return RagExternalRunDTO(
            case_id=case_id,
            rag_run_id=answer.run_id,
            answer=answer.answer,
            citations=answer.citations,
            retrieved_evidence=evidence,
            requested_domain=answer.retrieval.requested_domain,
            requested_kb_ids=answer.retrieval.requested_kb_ids,
            retrieval_mode=answer.retrieval.retrieval_mode,
            latency_ms=answer.retrieval.latency_ms,
            errors=answer.retrieval.errors,
        )

    async def _get_repository(self) -> ChunkVectorRepository:
        if self._repository is not None:
            return self._repository

        async with self._repository_lock:
            if self._repository is None:
                self._repository = await self._repository_factory(
                    embedder=self._embedder
                )
            return self._repository

    async def _generate_answer(
        self,
        *,
        question: str,
        evidence: Sequence[RagEvidenceDTO],
    ) -> _RagLLMAnswerModel:
        try:
            return await self._llm_client.chat_completion_structured(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是生产 RAG 问答助手。只能基于本次提供的 evidence 回答；"
                            "citations 只能填 evidence_id，不能编造 chunk_id、标题或来源。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": self._build_answer_prompt(
                            question=question, evidence=evidence
                        ),
                    },
                ],
                response_model=_RagLLMAnswerModel,
                temperature=0,
                max_tokens=800,
                thinking=False,
            )
        except Exception as exc:
            logger.warning("rag.answer llm failed: {}", type(exc).__name__)
            raise AppException(
                errors.ServiceUnavailable, message="RAG 生成暂不可用"
            ) from exc

    def _build_answer_prompt(
        self, *, question: str, evidence: Sequence[RagEvidenceDTO]
    ) -> str:
        parts = [f"question: {question}", "evidence:"]
        used_chars = 0
        for item in evidence:
            text = item.content_excerpt[:_MAX_CONTEXT_EVIDENCE_CHARS]
            block = (
                f"\n[{item.evidence_id}]\n"
                f"doc_id: {item.doc_id}\n"
                f"chunk_id: {item.chunk_id}\n"
                f"content: {text}"
            )
            if used_chars + len(block) > self._max_context_chars:
                break
            parts.append(block)
            used_chars += len(block)
        parts.append(
            "\nReturn JSON with fields: answer, citations, confidence. "
            "confidence must be one of low, medium, high."
        )
        return "\n".join(parts)

    async def _build_evidence_store(
        self, *, hits: Sequence[SearchHit]
    ) -> list[RagEvidenceDTO]:
        unique_hits: list[SearchHit] = []
        seen_chunk_ids: set[int] = set()
        for hit in hits:
            if hit.id in seen_chunk_ids:
                continue
            seen_chunk_ids.add(hit.id)
            unique_hits.append(hit)

        metadata_map = await self._metadata_dao.get_chunk_metadata_map(
            chunk_ids=[hit.id for hit in unique_hits]
        )
        evidence: list[RagEvidenceDTO] = []
        for index, hit in enumerate(unique_hits, start=1):
            metadata = metadata_map.get(hit.id)
            item = self._build_evidence(
                hit=hit, metadata=metadata, evidence_index=index
            )
            if item is not None:
                evidence.append(item)
        return evidence

    def _build_evidence(
        self,
        *,
        hit: SearchHit,
        metadata: RagChunkMetadata | None,
        evidence_index: int,
    ) -> RagEvidenceDTO | None:
        doc_id = (
            metadata.doc_id
            if metadata and metadata.doc_id is not None
            else self._read_int(hit, "doc_id")
        )
        if doc_id is None:
            return None
        kb_id = (
            metadata.kb_id
            if metadata and metadata.kb_id is not None
            else self._read_int(hit, "kb_id")
        )
        text = hit.text or ""
        excerpt = self._truncate_text(text, _MAX_ACTION_EXCERPT_CHARS)
        retrieval_mode = (
            hit.retrieval_mode.value if hit.retrieval_mode is not None else None
        )
        return RagEvidenceDTO(
            evidence_id=f"ev_{evidence_index:03d}",
            chunk_id=hit.id,
            doc_id=doc_id,
            kb_id=kb_id,
            title=metadata.title
            if metadata and metadata.title is not None
            else self._read_str(hit, "title"),
            source_uri=metadata.source_uri
            if metadata and metadata.source_uri is not None
            else self._read_str(hit, "source_uri"),
            document_version=metadata.document_version
            if metadata and metadata.document_version is not None
            else self._read_str(hit, "document_version"),
            text_hash=self._text_hash(text or excerpt),
            content_excerpt=excerpt,
            score=hit.relevance_score,
            retrieval_mode=retrieval_mode,
        )

    def _validate_citations(
        self,
        *,
        citation_ids: Sequence[str],
        evidence: Sequence[RagEvidenceDTO],
    ) -> list[RagCitationDTO]:
        evidence_map = {item.evidence_id: item for item in evidence}
        citations: list[RagCitationDTO] = []
        seen: set[str] = set()
        for citation_id in citation_ids:
            if citation_id in seen:
                continue
            seen.add(citation_id)
            item = evidence_map.get(citation_id)
            if item is not None:
                citations.append(item.to_citation())
        return citations

    @staticmethod
    def _select_evidence_hits(
        *, result: PostRetrievalResult, final_k: int
    ) -> list[SearchHit]:
        hits: list[SearchHit] = []
        for document in result.documents:
            hits.extend(document.chunks)
            if len(hits) >= final_k:
                return hits[:final_k]
        return list(result.hits[:final_k])

    def _resolve_effective_scope(
        self,
        *,
        user_id: int,
        requested_domain: str | None,
        requested_kb_ids: Sequence[int] | None,
    ) -> "_ResolvedRagScope":
        requested_context: dict[str, Any] = {
            "requested_domain": requested_domain,
            "requested_kb_ids": list(requested_kb_ids)
            if requested_kb_ids is not None
            else None,
        }
        allowed_domains = self._scope_resolver.resolve_allowed_domains(
            user_id=user_id,
            requested_context=requested_context,
        )
        allowed_kb_ids = self._scope_resolver.resolve_allowed_kb_ids(
            user_id=user_id,
            requested_context=requested_context,
        )
        requested_domains = {requested_domain} if requested_domain else allowed_domains
        requested_kb_id_set = (
            set(requested_kb_ids) if requested_kb_ids is not None else allowed_kb_ids
        )
        return _ResolvedRagScope(
            allowed_domains=sorted(allowed_domains),
            allowed_kb_ids=sorted(allowed_kb_ids),
            effective_domains=sorted(allowed_domains & requested_domains),
            effective_kb_ids=sorted(allowed_kb_ids & requested_kb_id_set),
        )

    @staticmethod
    def _build_scope_filters(
        *, scope: "_ResolvedRagScope"
    ) -> tuple[FilterCondition, ...]:
        return (
            FilterCondition(
                field="domain", op=FilterOperator.IN, value=scope.effective_domains
            ),
            FilterCondition(
                field="kb_id", op=FilterOperator.IN, value=scope.effective_kb_ids
            ),
        )

    @staticmethod
    def _read_int(hit: SearchHit, field: str) -> int | None:
        value = hit.metadata.get(field, hit.payload.get(field))
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
        return None

    @staticmethod
    def _read_str(hit: SearchHit, field: str) -> str | None:
        value = hit.metadata.get(field, hit.payload.get(field))
        if isinstance(value, str) and value:
            return value
        return None

    @staticmethod
    def _strip_context(evidence: Sequence[RagEvidenceDTO]) -> list[RagEvidenceDTO]:
        return [
            RagEvidenceDTO(
                evidence_id=item.evidence_id,
                chunk_id=item.chunk_id,
                doc_id=item.doc_id,
                kb_id=item.kb_id,
                title=item.title,
                source_uri=item.source_uri,
                document_version=item.document_version,
                text_hash=item.text_hash,
                content_excerpt="",
                score=item.score,
                retrieval_mode=item.retrieval_mode,
            )
            for item in evidence
        ]

    @staticmethod
    def _normalize_question(question: str) -> str:
        return " ".join(question.strip().split())

    @staticmethod
    def _bounded_top_k(top_k: int) -> int:
        return max(1, min(top_k, 50))

    @staticmethod
    def _bounded_final_k(*, final_k: int, top_k: int) -> int:
        return max(1, min(final_k, top_k, 10))

    @staticmethod
    def _truncate_text(text: str, max_chars: int) -> str:
        normalized = " ".join(text.split())
        if len(normalized) <= max_chars:
            return normalized
        return f"{normalized[:max_chars]}..."

    @staticmethod
    def _text_hash(text: str) -> str:
        return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return int((time.perf_counter() - started) * 1000)

    @staticmethod
    def _new_run_id() -> str:
        return f"rag_run_{uuid6_unique_str_id()}"

    @staticmethod
    def _resolve_trace_id() -> str:
        try:
            return context.get_trace_id()
        except LookupError:
            return f"trace_{uuid6_unique_str_id()}"


class _ResolvedRagScope(BaseModel):
    allowed_domains: list[str]
    allowed_kb_ids: list[int]
    effective_domains: list[str]
    effective_kb_ids: list[int]


_rag_service: RagService | None = None


def new_rag_service() -> RagService:
    """依赖注入：获取 RagService 单例。"""
    global _rag_service
    if _rag_service is None:
        from internal.infra.embedding import new_default_embedder
        from internal.infra.llm import new_default_llm_client

        _rag_service = RagService(
            llm_client=new_default_llm_client(),
            embedder=new_default_embedder(),
            metadata_dao=new_rag_metadata_dao(),
        )
    return _rag_service
