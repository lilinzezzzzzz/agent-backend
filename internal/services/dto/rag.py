from __future__ import annotations

from dataclasses import dataclass, field

from internal.schemas.rag import (
    RagAnswerRespSchema,
    RagCitationSchema,
    RagEvidenceSchema,
    RagExternalRunRespSchema,
)
from pkg.vectors.contracts import RetrievalMode


@dataclass(frozen=True, slots=True)
class PreparedRetrievalQueryDTO:
    original_question: str
    retrieval_query: str
    expanded_queries: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class RagCitationDTO:
    evidence_id: str
    doc_id: int
    kb_id: int | None
    chunk_id: int
    title: str | None
    source_uri: str | None
    document_version: str | None
    score: float | None

    def to_schema(self) -> RagCitationSchema:
        return RagCitationSchema(
            evidence_id=self.evidence_id,
            doc_id=self.doc_id,
            kb_id=self.kb_id,
            chunk_id=self.chunk_id,
            title=self.title,
            source_uri=self.source_uri,
            document_version=self.document_version,
            score=self.score,
        )


@dataclass(frozen=True, slots=True)
class RagEvidenceDTO:
    evidence_id: str
    chunk_id: int
    doc_id: int
    kb_id: int | None
    title: str | None
    source_uri: str | None
    document_version: str | None
    text_hash: str
    content_excerpt: str
    score: float | None
    retrieval_mode: str | None

    def to_schema(self) -> RagEvidenceSchema:
        return RagEvidenceSchema(
            evidence_id=self.evidence_id,
            chunk_id=self.chunk_id,
            doc_id=self.doc_id,
            kb_id=self.kb_id,
            title=self.title,
            source_uri=self.source_uri,
            document_version=self.document_version,
            text_hash=self.text_hash,
            content_excerpt=self.content_excerpt,
            score=self.score,
            retrieval_mode=self.retrieval_mode,
        )

    def to_citation(self) -> RagCitationDTO:
        return RagCitationDTO(
            evidence_id=self.evidence_id,
            doc_id=self.doc_id,
            kb_id=self.kb_id,
            chunk_id=self.chunk_id,
            title=self.title,
            source_uri=self.source_uri,
            document_version=self.document_version,
            score=self.score,
        )

    def to_action_match(self) -> dict[str, object]:
        return {
            "evidence_id": self.evidence_id,
            "doc_id": self.doc_id,
            "kb_id": self.kb_id,
            "chunk_id": self.chunk_id,
            "title": self.title,
            "content": self.content_excerpt,
            "source_uri": self.source_uri,
            "score": self.score,
        }


@dataclass(frozen=True, slots=True)
class RagRetrievalDTO:
    run_id: str
    trace_id: str
    query: str
    requested_domain: str | None
    requested_kb_ids: list[int] | None
    effective_domains: list[str]
    effective_kb_ids: list[int]
    retrieval_mode: RetrievalMode
    evidence: list[RagEvidenceDTO] = field(default_factory=list)
    latency_ms: int = 0
    errors: list[str] = field(default_factory=list)

    def to_action_result(self) -> dict[str, object]:
        return {
            "ok": not self.errors,
            "query": self.query,
            "matches": [item.to_action_match() for item in self.evidence],
            "total": len(self.evidence),
            "errors": list(self.errors),
        }


@dataclass(frozen=True, slots=True)
class RagAnswerDTO:
    run_id: str
    answer: str
    citations: list[RagCitationDTO]
    confidence: str
    trace_id: str
    retrieval: RagRetrievalDTO

    def to_schema(self) -> RagAnswerRespSchema:
        return RagAnswerRespSchema(
            run_id=self.run_id,
            answer=self.answer,
            citations=[citation.to_schema() for citation in self.citations],
            confidence=self.confidence,
            trace_id=self.trace_id,
        )


@dataclass(frozen=True, slots=True)
class RagExternalRunDTO:
    case_id: str
    rag_run_id: str
    answer: str
    citations: list[RagCitationDTO]
    retrieved_evidence: list[RagEvidenceDTO]
    requested_domain: str | None
    requested_kb_ids: list[int] | None
    retrieval_mode: RetrievalMode
    latency_ms: int
    errors: list[str]

    def to_schema(self) -> RagExternalRunRespSchema:
        return RagExternalRunRespSchema(
            case_id=self.case_id,
            rag_run_id=self.rag_run_id,
            answer=self.answer,
            citations=[citation.to_schema() for citation in self.citations],
            retrieved_evidence=[
                evidence.to_schema() for evidence in self.retrieved_evidence
            ],
            requested_domain=self.requested_domain,
            requested_kb_ids=self.requested_kb_ids,
            retrieval_mode=self.retrieval_mode,
            latency_ms=self.latency_ms,
            errors=list(self.errors),
        )
