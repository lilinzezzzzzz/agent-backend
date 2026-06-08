from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

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

import pkg.vectors.repositories.chunks as chunks_module
from pkg.vectors.repositories.chunks import ChunkVectorRepository, new_chunk_repository


def test_chunk_repository_enables_full_text_search_by_default():
    repo = ChunkVectorRepository(
        backend=MagicMock(),
        embedder=MagicMock(),
        tenant_id=1,
    )

    assert repo.collection_spec.full_text_search.enabled is True


def test_new_chunk_repository_requires_embedder(monkeypatch):
    backend = MagicMock()
    backend.ensure_collection = AsyncMock()
    embedder = MagicMock()

    monkeypatch.setattr(chunks_module, "create_backend", lambda **_: backend)

    repo = asyncio.run(new_chunk_repository(tenant_id=1, embedder=embedder))

    assert repo.embedder is embedder
    backend.ensure_collection.assert_awaited_once_with(spec=repo.collection_spec)
