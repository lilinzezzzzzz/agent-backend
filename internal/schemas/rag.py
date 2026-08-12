from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from internal.schemas import ForbidExtraModel
from pkg.vectors.contracts import RetrievalMode


class RagRetrievalOptionsSchema(ForbidExtraModel):
    mode: RetrievalMode = Field(default=RetrievalMode.HYBRID, description="检索模式")
    top_k: int = Field(default=20, description="宽召回数量", ge=1, le=50)
    final_k: int = Field(default=5, description="进入回答的证据数量", ge=1, le=10)

    @model_validator(mode="after")
    def validate_final_k(self) -> RagRetrievalOptionsSchema:
        if self.final_k > self.top_k:
            raise ValueError("final_k must be less than or equal to top_k")
        return self


class RagAnswerReqSchema(ForbidExtraModel):
    question: str = Field(..., description="用户问题", min_length=1, max_length=1000)
    requested_domain: str | None = Field(
        default=None,
        description="客户端期望检索的业务域；最终范围由服务端 allowed scope 求交得到",
        min_length=1,
        max_length=64,
    )
    requested_kb_ids: list[int] | None = Field(
        default=None,
        description="客户端期望检索的知识库 ID 列表；最终范围由服务端 allowed scope 求交得到",
        max_length=20,
    )
    retrieval: RagRetrievalOptionsSchema = Field(
        default_factory=RagRetrievalOptionsSchema
    )
    require_citations: bool = Field(
        default=True, description="是否要求回答携带有效引用"
    )


class RagExternalRunReqSchema(ForbidExtraModel):
    case_id: str = Field(
        ..., description="外部调用方的 case ID", min_length=1, max_length=128
    )
    subject_user_id: UUID = Field(..., description="代表哪个用户解析 allowed scope")
    question: str = Field(
        ..., description="待运行的 RAG 问题", min_length=1, max_length=1000
    )
    requested_domain: str | None = Field(default=None, min_length=1, max_length=64)
    requested_kb_ids: list[int] | None = Field(default=None, max_length=20)
    retrieval: RagRetrievalOptionsSchema = Field(
        default_factory=RagRetrievalOptionsSchema
    )
    return_context: bool = Field(
        default=False, description="是否返回受控 evidence excerpt"
    )


class RagCitationSchema(BaseModel):
    evidence_id: str = Field(..., description="本次 run 内有效的 evidence alias")
    doc_id: int = Field(..., description="文档 ID")
    kb_id: int | None = Field(None, description="知识库 ID")
    chunk_id: int = Field(..., description="稳定 chunk ID")
    title: str | None = Field(None, description="文档标题")
    source_uri: str | None = Field(None, description="来源 URI")
    document_version: str | None = Field(None, description="文档版本")
    score: float | None = Field(None, description="检索得分")


class RagEvidenceSchema(BaseModel):
    evidence_id: str
    doc_id: int
    kb_id: int | None = None
    chunk_id: int
    title: str | None = None
    source_uri: str | None = None
    document_version: str | None = None
    content_excerpt: str = ""
    text_hash: str
    score: float | None = None
    retrieval_mode: str | None = None


class RagAnswerRespSchema(BaseModel):
    run_id: str
    answer: str
    citations: list[RagCitationSchema] = Field(default_factory=list)
    confidence: str
    trace_id: str


class RagExternalRunRespSchema(BaseModel):
    case_id: str
    rag_run_id: str
    answer: str
    citations: list[RagCitationSchema] = Field(default_factory=list)
    retrieved_evidence: list[RagEvidenceSchema] = Field(default_factory=list)
    requested_domain: str | None = None
    requested_kb_ids: list[int] | None = None
    retrieval_mode: RetrievalMode
    latency_ms: int = Field(ge=0)
    errors: list[str] = Field(default_factory=list)


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
