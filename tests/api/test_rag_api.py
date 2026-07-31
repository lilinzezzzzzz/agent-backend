from __future__ import annotations

from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from internal.controllers import internal as internal_controller
from internal.controllers.api import rag as rag_controller
from internal.controllers.internal import rag as internal_rag_controller
from internal.dependencies.auth import require_internal_signature
from internal.schemas.rag import (
    RagAnswerDTO,
    RagCitationDTO,
    RagRetrievalDTO,
    RagExternalRunDTO,
)
from pkg.vectors.contracts import RetrievalMode


class FakeRagService:
    def __init__(self):
        self.answer_calls: list[dict[str, Any]] = []
        self.external_run_calls: list[dict[str, Any]] = []

    async def answer(self, **kwargs: Any) -> RagAnswerDTO:
        self.answer_calls.append(kwargs)
        retrieval = RagRetrievalDTO(
            run_id="rag_run_1",
            trace_id="trace_1",
            query=kwargs["question"],
            requested_domain=kwargs.get("requested_domain"),
            requested_kb_ids=kwargs.get("requested_kb_ids"),
            effective_domains=["order"],
            effective_kb_ids=[2],
            retrieval_mode=RetrievalMode.HYBRID,
        )
        return RagAnswerDTO(
            run_id="rag_run_1",
            answer="通常会在 1-3 个工作日内完成。",
            citations=[
                RagCitationDTO(
                    evidence_id="ev_001",
                    doc_id=10,
                    kb_id=2,
                    chunk_id=1001,
                    title="电子发票开具与通知",
                    source_uri="order/help/invoice",
                    document_version="v1",
                    score=0.91,
                )
            ],
            confidence="medium",
            trace_id="trace_1",
            retrieval=retrieval,
        )

    async def run_external(self, **kwargs: Any) -> RagExternalRunDTO:
        self.external_run_calls.append(kwargs)
        return RagExternalRunDTO(
            case_id=kwargs["case_id"],
            rag_run_id="rag_run_1",
            answer="通常会在 1-3 个工作日内完成。",
            citations=[],
            retrieved_evidence=[],
            requested_domain=kwargs.get("requested_domain"),
            requested_kb_ids=kwargs.get("requested_kb_ids"),
            retrieval_mode=RetrievalMode.HYBRID,
            latency_ms=12,
            errors=[],
        )


@pytest_asyncio.fixture
async def rag_client():
    service = FakeRagService()
    app = FastAPI()
    app.include_router(rag_controller.router, prefix="/v1")
    app.include_router(internal_controller.router)
    app.dependency_overrides[rag_controller.new_rag_service] = lambda: service
    app.dependency_overrides[internal_rag_controller.new_rag_service] = lambda: service
    app.dependency_overrides[require_internal_signature] = lambda: None

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client, service

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_rag_answer_endpoint_wraps_service_dto(rag_client) -> None:
    client, service = rag_client
    response = await client.post(
        "/v1/rag/answer",
        json={
            "question": "发票开具后多久通知用户？",
            "requested_domain": "order",
            "requested_kb_ids": [2],
            "retrieval": {"mode": "hybrid", "top_k": 20, "final_k": 5},
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["run_id"] == "rag_run_1"
    assert data["citations"][0]["evidence_id"] == "ev_001"
    assert service.answer_calls[0]["requested_domain"] == "order"
    assert service.answer_calls[0]["requested_kb_ids"] == [2]


@pytest.mark.asyncio
async def test_rag_internal_external_run_endpoint_does_not_accept_gold_fields(
    rag_client,
) -> None:
    client, service = rag_client
    response = await client.post(
        "/v1/internal/rag/external-runs",
        json={
            "case_id": "case_001",
            "subject_user_id": 999,
            "question": "发票开具后多久通知用户？",
            "requested_domain": "order",
            "requested_kb_ids": [2],
            "retrieval": {"mode": "hybrid", "top_k": 20, "final_k": 5},
            "expected_answer": "不应被接受",
        },
    )

    assert response.status_code == 422
    assert service.external_run_calls == []


@pytest.mark.asyncio
async def test_rag_internal_external_run_endpoint_wraps_run_data(rag_client) -> None:
    client, service = rag_client
    response = await client.post(
        "/v1/internal/rag/external-runs",
        json={
            "case_id": "case_001",
            "subject_user_id": 999,
            "question": "发票开具后多久通知用户？",
            "requested_domain": "order",
            "requested_kb_ids": [2],
            "retrieval": {"mode": "hybrid", "top_k": 20, "final_k": 5},
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["case_id"] == "case_001"
    assert "effective_domains" not in data
    assert "effective_kb_ids" not in data
    assert service.external_run_calls[0]["subject_user_id"] == 999
