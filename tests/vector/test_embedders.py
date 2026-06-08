from __future__ import annotations

import sys
import types
from pathlib import Path

if "pkg.vectors" not in sys.modules:
    vectors_package = types.ModuleType("pkg.vectors")
    vectors_package.__path__ = [str(Path(__file__).resolve().parents[2] / "pkg" / "vectors")]
    sys.modules["pkg.vectors"] = vectors_package

from pkg.vectors.embedders import (
    EmbedderProvider,
    OpenAICompatibleEmbedder,
    create_embedder,
)


def test_openai_compatible_provider_builds_embedder():
    embedder = create_embedder(
        provider=EmbedderProvider.OPENAI_COMPATIBLE,
        api_key="test-key",
        base_url="http://embedding.example/v1",
        model="bge-m3",
    )

    assert isinstance(embedder, OpenAICompatibleEmbedder)


def test_openai_compatible_provider_accepts_config_string():
    embedder = create_embedder(
        provider="openai_compatible",
        api_key="test-key",
        base_url="http://embedding.example/v1",
        model="bge-m3",
    )

    assert isinstance(embedder, OpenAICompatibleEmbedder)


def test_create_embedder_does_not_cache_instances():
    first = create_embedder(
        provider=EmbedderProvider.OPENAI_COMPATIBLE,
        api_key="test-key",
        base_url="http://embedding.example/v1",
        model="bge-m3",
    )
    second = create_embedder(
        provider=EmbedderProvider.OPENAI_COMPATIBLE,
        api_key="test-key",
        base_url="http://embedding.example/v1",
        model="bge-m3",
    )

    assert first is not second
