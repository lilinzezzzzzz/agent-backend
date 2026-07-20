from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

if "pkg.vectors" not in sys.modules:
    vectors_package = types.ModuleType("pkg.vectors")
    vectors_package.__path__ = [
        str(Path(__file__).resolve().parents[3] / "pkg" / "vectors")
    ]
    sys.modules["pkg.vectors"] = vectors_package

from pkg.vectors.backends.base import IsolationMode
from pkg.vectors.contracts import FilterOperator, SearchHit

from internal.rag.repositories.chunk_vector import (
    ChunkVectorDocument,
    ChunkVectorRepository,
    new_chunk_repository,
)


def test_chunk_repository_enables_full_text_search_by_default():
    repo = ChunkVectorRepository(
        backend=MagicMock(),
        embedder=MagicMock(),
    )

    assert repo.collection_spec.full_text_search.enabled is True


def test_chunk_repository_disables_vector_isolation_by_default():
    repo = ChunkVectorRepository(
        backend=MagicMock(),
        embedder=MagicMock(),
        scope_value=1,
    )

    assert repo.collection_spec.isolation_mode == IsolationMode.NONE
    assert repo.scope_field is None
    assert repo.build_scope_filters() == []


def test_chunk_repository_supports_kb_id_as_regular_filter():
    repo = ChunkVectorRepository(
        backend=MagicMock(),
        embedder=MagicMock(),
    )
    repo.search_by_text = AsyncMock(
        return_value=[
            SearchHit(
                id=1,
                text="发票开具完成后会通知用户",
                metadata={
                    "doc_id": 10,
                    "kb_id": 2,
                    "domain": "order",
                    "chunk_index": 0,
                },
                relevance_score=0.9,
            )
        ]
    )

    results = asyncio.run(
        repo.search_similar(
            query_text="发票通知",
            filters={"kb_id": [2, 3]},
            similarity_threshold=0.1,
        )
    )

    assert results[0]["kb_id"] == 2
    filters = repo.search_by_text.call_args.kwargs["filters"]
    assert filters[0].field == "kb_id"
    assert filters[0].op == FilterOperator.IN
    assert filters[0].value == [2, 3]


def test_chunk_repository_writes_required_rag_metadata():
    repo = ChunkVectorRepository(
        backend=MagicMock(),
        embedder=MagicMock(),
    )

    records = repo.to_records(
        entity=ChunkVectorDocument(
            id=1,
            doc_id=10,
            kb_id=2,
            domain="order",
            chunk_index=3,
            text="发票开具完成后会通知用户",
        )
    )

    assert records[0].metadata == {
        "doc_id": 10,
        "kb_id": 2,
        "domain": "order",
        "chunk_index": 3,
    }


def test_chunk_repository_supports_domain_as_regular_filter():
    repo = ChunkVectorRepository(
        backend=MagicMock(),
        embedder=MagicMock(),
    )
    repo.search_by_text = AsyncMock(return_value=[])

    asyncio.run(
        repo.search_similar(
            query_text="发票通知",
            filters={"domain": ["order", "payment"]},
            similarity_threshold=0.1,
        )
    )

    filters = repo.search_by_text.call_args.kwargs["filters"]
    assert filters[0].field == "domain"
    assert filters[0].op == FilterOperator.IN
    assert filters[0].value == ["order", "payment"]


def test_new_chunk_repository_requires_embedder(monkeypatch):
    import internal.rag.repositories.chunk_vector as chunk_vector_module
    import pkg.vectors.backends as backends

    backend = MagicMock()
    backend.ensure_collection = AsyncMock()
    embedder = MagicMock()
    backend_kwargs: dict[str, object] = {}

    def create_backend(**kwargs):
        backend_kwargs.update(kwargs)
        return backend

    monkeypatch.setattr(backends, "create_backend", create_backend)
    monkeypatch.setattr(
        chunk_vector_module,
        "settings",
        SimpleNamespace(
            MILVUS_URI="http://milvus.example:19530",
            MILVUS_DB_NAME="rag_test",
            MILVUS_TIMEOUT_SECONDS=7,
        ),
    )

    repo = asyncio.run(new_chunk_repository(embedder=embedder, scope_value=1))

    assert repo.embedder is embedder
    assert backend_kwargs == {
        "provider": backends.BackendProvider.MILVUS,
        "uri": "http://milvus.example:19530",
        "db_name": "rag_test",
        "timeout": 7,
    }
    backend.ensure_collection.assert_awaited_once_with(spec=repo.collection_spec)
