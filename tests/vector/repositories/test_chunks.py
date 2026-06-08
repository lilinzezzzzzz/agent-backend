from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

if "pkg.vectors" not in sys.modules:
    vectors_package = types.ModuleType("pkg.vectors")
    vectors_package.__path__ = [
        str(Path(__file__).resolve().parents[3] / "pkg" / "vectors")
    ]
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

import internal.vectors.chunks as chunks_module
from pkg.vectors.backends.base import IsolationMode
from pkg.vectors.contracts import FilterOperator, SearchHit

from internal.vectors.chunks import ChunkVectorRepository, new_chunk_repository


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
                metadata={"doc_id": 10, "kb_id": 2},
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


def test_new_chunk_repository_requires_embedder(monkeypatch):
    backend = MagicMock()
    backend.ensure_collection = AsyncMock()
    embedder = MagicMock()

    monkeypatch.setattr(chunks_module, "create_backend", lambda **_: backend)

    repo = asyncio.run(new_chunk_repository(embedder=embedder, scope_value=1))

    assert repo.embedder is embedder
    backend.ensure_collection.assert_awaited_once_with(spec=repo.collection_spec)
