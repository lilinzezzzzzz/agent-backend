from __future__ import annotations

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


class RagEvaluationRunReqSchema(ForbidExtraModel):
    case_id: str = Field(
        ..., description="外部评测系统的 case ID", min_length=1, max_length=128
    )
    subject_user_id: int = Field(
        ..., description="代表哪个用户解析 allowed scope", ge=0
    )
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


class RagEvaluationRunRespSchema(BaseModel):
    case_id: str
    rag_run_id: str
    answer: str
    citations: list[RagCitationSchema] = Field(default_factory=list)
    retrieved_evidence: list[RagEvidenceSchema] = Field(default_factory=list)
    requested_domain: str | None = None
    requested_kb_ids: list[int] | None = None
    effective_domains: list[str] = Field(default_factory=list)
    effective_kb_ids: list[int] = Field(default_factory=list)
    retrieval_mode: RetrievalMode
    latency_ms: int = Field(ge=0)
    errors: list[str] = Field(default_factory=list)
