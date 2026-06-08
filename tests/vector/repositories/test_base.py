from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

if "pkg.vectors" not in sys.modules:
    vectors_package = types.ModuleType("pkg.vectors")
    vectors_package.__path__ = [str(Path(__file__).resolve().parents[3] / "pkg" / "vectors")]
    sys.modules["pkg.vectors"] = vectors_package

if "zvec" not in sys.modules:
    zvec_module = types.ModuleType("zvec")
    zvec_module.Collection = type("Collection", (), {})
    zvec_module.CollectionOption = type("CollectionOption", (), {})
    zvec_module.CollectionSchema = type("CollectionSchema", (), {})
    zvec_module.Doc = type("Doc", (), {})
    zvec_module.VectorQuery = type("VectorQuery", (), {})
    zvec_module.DataType = types.SimpleNamespace(
        STRING="STRING",
        INT64="INT64",
        DOUBLE="DOUBLE",
        BOOL="BOOL",
        VECTOR_FP32="VECTOR_FP32",
    )
    zvec_module.MetricType = types.SimpleNamespace(
        COSINE="COSINE",
        IP="IP",
        L2="L2",
    )
    zvec_module.HnswIndexParam = type("HnswIndexParam", (), {})
    zvec_module.IVFIndexParam = type("IVFIndexParam", (), {})
    zvec_module.FlatIndexParam = type("FlatIndexParam", (), {})
    zvec_module.InvertIndexParam = type("InvertIndexParam", (), {})
    zvec_module.FieldSchema = type("FieldSchema", (), {})
    zvec_module.VectorSchema = type("VectorSchema", (), {})
    zvec_module.open = MagicMock()
    zvec_module.create_and_open = MagicMock()
    sys.modules["zvec"] = zvec_module

from pkg.vectors.backends.base import CollectionSpec, IsolationMode, ScalarDataType, ScalarFieldSpec
from pkg.vectors.context_assembly import DocumentContextAssembler
from pkg.vectors.contracts import ConsistencyLevel, RetrievalMode, SearchHit, VectorRecord
from pkg.vectors.post_retrieval import PostRetrievalPipeline
from pkg.vectors.repositories.base import BaseVectorRepository


class DummyRepository(BaseVectorRepository[int]):
    @property
    def collection_spec(self) -> CollectionSpec:
        return CollectionSpec(name="test_collection", dimension=4)

    def to_records(self, *, entity: int) -> list[VectorRecord]:
        return [
            VectorRecord(
                id=entity,
                text=str(entity),
                embedding=[0.1, 0.2, 0.3, 0.4],
            )
        ]


class SharedFilterScopeRepository(DummyRepository):
    @property
    def collection_spec(self) -> CollectionSpec:
        return CollectionSpec(
            name="test_collection",
            dimension=4,
            isolation_mode=IsolationMode.SHARED_FILTER,
            scalar_fields=[
                ScalarFieldSpec(name="org_id", data_type=ScalarDataType.INT64),
            ],
        )

    @property
    def scope_field(self) -> str | None:
        return "org_id"


class SharedFilterStringScopeRepository(DummyRepository):
    @property
    def collection_spec(self) -> CollectionSpec:
        return CollectionSpec(
            name="test_collection",
            dimension=4,
            isolation_mode=IsolationMode.SHARED_FILTER,
            scalar_fields=[
                ScalarFieldSpec(name="workspace_key", data_type=ScalarDataType.STRING),
            ],
        )

    @property
    def scope_field(self) -> str | None:
        return "workspace_key"


class SharedFilterUnsupportedScopeTypeRepository(DummyRepository):
    @property
    def collection_spec(self) -> CollectionSpec:
        return CollectionSpec(
            name="test_collection",
            dimension=4,
            isolation_mode=IsolationMode.SHARED_FILTER,
            scalar_fields=[
                ScalarFieldSpec(name="score", data_type=ScalarDataType.FLOAT),
            ],
        )

    @property
    def scope_field(self) -> str | None:
        return "score"


class SharedFilterMissingScopeFieldRepository(DummyRepository):
    @property
    def collection_spec(self) -> CollectionSpec:
        return CollectionSpec(
            name="test_collection",
            dimension=4,
            isolation_mode=IsolationMode.SHARED_FILTER,
        )


class SharedFilterMissingScalarFieldRepository(DummyRepository):
    @property
    def collection_spec(self) -> CollectionSpec:
        return CollectionSpec(
            name="test_collection",
            dimension=4,
            isolation_mode=IsolationMode.SHARED_FILTER,
        )

    @property
    def scope_field(self) -> str | None:
        return "org_id"


@pytest.fixture
def backend() -> MagicMock:
    backend = MagicMock()
    backend.ensure_collection = AsyncMock()
    backend.upsert = AsyncMock()
    backend.delete = AsyncMock(return_value=0)
    backend.fetch = AsyncMock(return_value=[])
    backend.search = AsyncMock(return_value=[])
    return backend


@pytest.fixture
def embedder() -> MagicMock:
    embedder = MagicMock()
    embedder.embed_query = AsyncMock(return_value=[0.1, 0.2, 0.3, 0.4])
    embedder.embed_texts = AsyncMock(return_value=[])
    return embedder


def test_collection_spec_disables_isolation_by_default():
    spec = CollectionSpec(name="test_collection", dimension=4)

    assert spec.isolation_mode == IsolationMode.NONE


def test_scope_value_does_not_scope_queries_without_explicit_isolation_mode(
    backend: MagicMock,
    embedder: MagicMock,
):
    repo = DummyRepository(backend=backend, embedder=embedder, scope_value=1)

    asyncio.run(repo.search_by_text(query_text="hello", top_k=1))

    request = backend.search.await_args.kwargs["request"]
    assert request.filters == []


def test_shared_filter_isolation_mode_adds_int_scope_filter(backend: MagicMock, embedder: MagicMock):
    repo = SharedFilterScopeRepository(backend=backend, embedder=embedder, scope_value=1)

    asyncio.run(repo.search_by_text(query_text="hello", top_k=1))

    request = backend.search.await_args.kwargs["request"]
    assert len(request.filters) == 1
    assert request.filters[0].field == "org_id"
    assert request.filters[0].value == 1


def test_shared_filter_isolation_mode_adds_string_scope_filter(backend: MagicMock, embedder: MagicMock):
    repo = SharedFilterStringScopeRepository(backend=backend, embedder=embedder, scope_value="workspace-prod")

    asyncio.run(repo.search_by_text(query_text="hello", top_k=1))

    request = backend.search.await_args.kwargs["request"]
    assert len(request.filters) == 1
    assert request.filters[0].field == "workspace_key"
    assert request.filters[0].value == "workspace-prod"


def test_shared_filter_isolation_mode_requires_scope_field(backend: MagicMock, embedder: MagicMock):
    repo = SharedFilterMissingScopeFieldRepository(backend=backend, embedder=embedder, scope_value=1)

    with pytest.raises(ValueError, match="scope_field"):
        repo.build_scope_filters()


def test_shared_filter_isolation_mode_requires_scope_value(backend: MagicMock, embedder: MagicMock):
    repo = SharedFilterScopeRepository(backend=backend, embedder=embedder)

    with pytest.raises(ValueError, match="scope_value"):
        repo.build_scope_filters()


def test_shared_filter_isolation_mode_requires_scalar_field(backend: MagicMock, embedder: MagicMock):
    repo = SharedFilterMissingScalarFieldRepository(backend=backend, embedder=embedder, scope_value=1)

    with pytest.raises(ValueError, match="scalar_fields"):
        repo.build_scope_filters()


def test_shared_filter_isolation_mode_rejects_mismatched_scope_value_type(
    backend: MagicMock,
    embedder: MagicMock,
):
    repo = SharedFilterScopeRepository(backend=backend, embedder=embedder, scope_value="1")

    with pytest.raises(TypeError, match="scope_value 必须是 int"):
        repo.build_scope_filters()


def test_shared_filter_isolation_mode_rejects_bool_scope_value(backend: MagicMock, embedder: MagicMock):
    repo = SharedFilterScopeRepository(backend=backend, embedder=embedder, scope_value=True)

    with pytest.raises(TypeError, match="不支持 bool"):
        repo.build_scope_filters()


def test_shared_filter_isolation_mode_rejects_unsupported_scope_field_type(
    backend: MagicMock,
    embedder: MagicMock,
):
    repo = SharedFilterUnsupportedScopeTypeRepository(backend=backend, embedder=embedder, scope_value=1)

    with pytest.raises(ValueError, match="只支持 INT64 或 STRING"):
        repo.build_scope_filters()


def test_fetch_by_ids_passes_consistency_level(backend: MagicMock, embedder: MagicMock):
    repo = DummyRepository(backend=backend, embedder=embedder)

    asyncio.run(
        repo.fetch_by_ids(
            ids=[1],
            consistency_level=ConsistencyLevel.STRONG,
        )
    )

    assert backend.fetch.await_args.kwargs["consistency_level"] == ConsistencyLevel.STRONG


def test_search_by_text_passes_consistency_level(backend: MagicMock, embedder: MagicMock):
    repo = DummyRepository(backend=backend, embedder=embedder)

    asyncio.run(
        repo.search_by_text(
            query_text="hello",
            top_k=1,
            consistency_level=ConsistencyLevel.STRONG,
        )
    )

    request = backend.search.await_args.kwargs["request"]
    assert request.consistency_level == ConsistencyLevel.STRONG
    assert request.query_text == "hello"


def test_search_by_text_full_text_mode_skips_embedding(
    backend: MagicMock,
    embedder: MagicMock,
):
    repo = DummyRepository(backend=backend, embedder=embedder)

    asyncio.run(
        repo.search_by_text(
            query_text="hello",
            top_k=1,
            retrieval_mode=RetrievalMode.FULL_TEXT,
        )
    )

    embedder.embed_query.assert_not_awaited()
    request = backend.search.await_args.kwargs["request"]
    assert request.vector is None
    assert request.retrieval_mode == RetrievalMode.FULL_TEXT


def test_retrieve_by_text_runs_post_retrieval_pipeline(
    backend: MagicMock,
    embedder: MagicMock,
):
    repo = DummyRepository(backend=backend, embedder=embedder)
    backend.search.return_value = [
        SearchHit(id=1, text="chunk-1", metadata={"doc_id": 10}, relevance_score=0.9),
        SearchHit(id=2, text="chunk-2", metadata={"doc_id": 10}, relevance_score=0.8),
        SearchHit(id=3, text="chunk-3", metadata={"doc_id": 20}, relevance_score=0.85),
    ]

    result = asyncio.run(
        repo.retrieve_by_text(
            query_text="hello",
            top_k=3,
            post_retrieval=PostRetrievalPipeline(),
        )
    )

    assert result.stats.input_hit_count == 3
    assert len(result.documents) == 2
    assert result.documents[0].document_key == 10


def test_assemble_context_by_text_runs_post_retrieval_and_context_assembly(
    backend: MagicMock,
    embedder: MagicMock,
):
    repo = DummyRepository(backend=backend, embedder=embedder)
    backend.search.return_value = [
        SearchHit(
            id=2,
            text="chunk-2",
            metadata={"doc_id": 10, "chunk_index": 2, "title": "Doc 10"},
            relevance_score=0.9,
        ),
        SearchHit(
            id=1,
            text="chunk-1",
            metadata={"doc_id": 10, "chunk_index": 1, "title": "Doc 10"},
            relevance_score=0.95,
        ),
        SearchHit(
            id=3,
            text="chunk-3",
            metadata={"doc_id": 20, "chunk_index": 1, "title": "Doc 20"},
            relevance_score=0.85,
        ),
    ]

    result = asyncio.run(
        repo.assemble_context_by_text(
            query_text="hello",
            top_k=3,
            post_retrieval=PostRetrievalPipeline(),
            context_assembler=DocumentContextAssembler(),
        )
    )

    assert len(result.documents) == 2
    assert result.documents[0].document_key == 10
    assert result.documents[0].chunk_ids == [1, 2]
    assert "Doc 10" in result.context_text
    assert result.context_text.index("chunk-1") < result.context_text.index("chunk-2")
