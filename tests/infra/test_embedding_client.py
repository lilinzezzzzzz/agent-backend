from __future__ import annotations

import sys
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import SecretStr

if "pkg.vectors" not in sys.modules:
    vectors_package = types.ModuleType("pkg.vectors")
    vectors_package.__path__ = [str(Path(__file__).resolve().parents[2] / "pkg" / "vectors")]
    sys.modules["pkg.vectors"] = vectors_package

infra_module = sys.modules.get("internal.infra")
if infra_module is not None and not hasattr(infra_module, "__path__"):
    sys.modules.pop("internal.infra", None)

from internal.core import AppException, errors  # noqa: E402
from internal.infra.embedding import client as embedding_client_module  # noqa: E402


class FakeEmbedder:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


def test_new_default_embedder_uses_embedding_config(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = SimpleNamespace(
        EMBEDDING_PROVIDER="openai_compatible",
        EMBEDDING_BASE_URL="http://embedding.example/v1",
        EMBEDDING_MODEL="bge-m3",
        EMBEDDING_API_KEY=SecretStr("embedding-api-key"),
        EMBEDDING_DIMENSION=1024,
        EMBEDDING_TIMEOUT_SECONDS=30,
    )

    def fake_create_embedder(**kwargs: Any) -> FakeEmbedder:
        return FakeEmbedder(**kwargs)

    monkeypatch.setattr(embedding_client_module, "settings", settings)
    monkeypatch.setattr(embedding_client_module, "create_embedder", fake_create_embedder)

    embedder = embedding_client_module.new_default_embedder()

    assert isinstance(embedder, FakeEmbedder)
    assert embedder.kwargs == {
        "provider": "openai_compatible",
        "api_key": "embedding-api-key",
        "base_url": "http://embedding.example/v1",
        "model": "bge-m3",
        "dimension": 1024,
        "timeout": 30,
    }


def test_new_default_embedder_rejects_missing_provider_config(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = SimpleNamespace(
        EMBEDDING_PROVIDER="openai_compatible",
        EMBEDDING_BASE_URL="",
        EMBEDDING_MODEL="bge-m3",
        EMBEDDING_API_KEY=SecretStr("embedding-api-key"),
        EMBEDDING_DIMENSION=1024,
        EMBEDDING_TIMEOUT_SECONDS=30,
    )
    monkeypatch.setattr(embedding_client_module, "settings", settings)

    with pytest.raises(AppException) as exc_info:
        embedding_client_module.new_default_embedder()

    assert exc_info.value.error == errors.ServiceUnavailable
    assert exc_info.value.message == "Embedding provider 'openai_compatible' is not configured"


def test_new_default_embedder_supports_no_auth_openai_compatible_service(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = SimpleNamespace(
        EMBEDDING_PROVIDER="openai_compatible",
        EMBEDDING_BASE_URL="http://embedding.example/v1",
        EMBEDDING_MODEL="bge-m3",
        EMBEDDING_API_KEY=SecretStr(""),
        EMBEDDING_DIMENSION=1024,
        EMBEDDING_TIMEOUT_SECONDS=30,
    )

    def fake_create_embedder(**kwargs: Any) -> FakeEmbedder:
        return FakeEmbedder(**kwargs)

    monkeypatch.setattr(embedding_client_module, "settings", settings)
    monkeypatch.setattr(embedding_client_module, "create_embedder", fake_create_embedder)

    embedder = embedding_client_module.new_default_embedder()

    assert isinstance(embedder, FakeEmbedder)
    assert embedder.kwargs["api_key"] == "not-required"
