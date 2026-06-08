from __future__ import annotations

from typing import Any

import pytest

from internal.dao.rag import RagChunkMetadata
from internal.services.rag import RagService
from pkg.vectors.contracts import FilterOperator, RetrievalMode, SearchHit
from pkg.vectors.post_retrieval import PostRetrievalResult, PostRetrievalStats


class FakeRepository:
    def __init__(self):
        self.calls: list[dict[str, Any]] = []

    async def retrieve_by_text(self, **kwargs: Any) -> PostRetrievalResult:
        self.calls.append(kwargs)
        return PostRetrievalResult(
            hits=[
                SearchHit(
                    id=1001,
                    text="开票申请提交后通常会在 1-3 个工作日内完成。",
                    metadata={
                        "doc_id": 10,
                        "kb_id": 2,
                        "domain": "order",
                        "chunk_index": 0,
                    },
                    retrieval_mode=RetrievalMode.HYBRID,
                    relevance_score=0.91,
                )
            ],
            documents=[],
            stats=PostRetrievalStats(
                input_hit_count=1,
                deduped_hit_count=1,
                document_count=0,
                returned_document_count=0,
            ),
        )


class FakeMetadataDao:
    async def get_chunk_metadata_map(self, *, chunk_ids):
        assert chunk_ids == [1001]
        return {
            1001: RagChunkMetadata(
                chunk_id=1001,
                doc_id=10,
                kb_id=2,
                title="电子发票开具与通知",
                source_uri="order/help/invoice",
                document_version="v1",
            )
        }


class FakeScopeResolver:
    def resolve_allowed_domains(self, *, user_id: int, requested_context):
        assert user_id == 999
        return {"order", "payment"}

    def resolve_allowed_kb_ids(self, *, user_id: int, requested_context):
        assert user_id == 999
        return {2, 3}


class FakeLLMClient:
    def __init__(self, citations: list[str]):
        self.citations = citations
        self.calls: list[dict[str, Any]] = []

    async def chat_completion_structured(self, **kwargs: Any):
        self.calls.append(kwargs)
        response_model = kwargs["response_model"]
        return response_model(
            answer="通常会在 1-3 个工作日内完成。",
            citations=self.citations,
            confidence="medium",
        )


@pytest.mark.asyncio
async def test_rag_retrieve_intersects_requested_scope_and_builds_evidence() -> None:
    repository = FakeRepository()
    service = RagService(
        llm_client=FakeLLMClient(["ev_001"]),
        embedder=object(),
        metadata_dao=FakeMetadataDao(),
        scope_resolver=FakeScopeResolver(),
        repository=repository,
    )

    result = await service.retrieve(
        user_id=999,
        query=" 发票   多久开具 ",
        requested_domain="order",
        requested_kb_ids=[2, 99],
        top_k=20,
        final_k=5,
    )

    assert result.errors == []
    assert result.query == "发票 多久开具"
    assert result.effective_domains == ["order"]
    assert result.effective_kb_ids == [2]
    filters = repository.calls[0]["filters"]
    assert filters[0].field == "domain"
    assert filters[0].op == FilterOperator.IN
    assert filters[0].value == ["order"]
    assert filters[1].field == "kb_id"
    assert filters[1].op == FilterOperator.IN
    assert filters[1].value == [2]
    assert result.evidence[0].evidence_id == "ev_001"
    assert result.evidence[0].chunk_id == 1001
    assert result.evidence[0].title == "电子发票开具与通知"


@pytest.mark.asyncio
async def test_rag_answer_uses_only_valid_evidence_citations() -> None:
    service = RagService(
        llm_client=FakeLLMClient(["ev_999", "ev_001"]),
        embedder=object(),
        metadata_dao=FakeMetadataDao(),
        scope_resolver=FakeScopeResolver(),
        repository=FakeRepository(),
    )

    result = await service.answer(
        user_id=999,
        question="发票多久开具？",
        requested_domain="order",
        requested_kb_ids=[2],
    )

    assert result.answer == "通常会在 1-3 个工作日内完成。"
    assert [citation.evidence_id for citation in result.citations] == ["ev_001"]
    assert result.citations[0].source_uri == "order/help/invoice"


@pytest.mark.asyncio
async def test_rag_empty_effective_scope_returns_stable_error_without_repository_call() -> (
    None
):
    repository = FakeRepository()
    service = RagService(
        llm_client=FakeLLMClient(["ev_001"]),
        embedder=object(),
        metadata_dao=FakeMetadataDao(),
        scope_resolver=FakeScopeResolver(),
        repository=repository,
    )

    result = await service.retrieve(
        user_id=999,
        query="支付失败怎么办？",
        requested_domain="general",
        requested_kb_ids=[999],
    )

    assert result.errors == ["rag_scope_empty"]
    assert result.evidence == []
    assert repository.calls == []
