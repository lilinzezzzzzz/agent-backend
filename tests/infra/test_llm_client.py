from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from internal.core import AppException, errors
from internal.infra.llm import client as llm_client_module


class FakeOpenAIClient:
    def __init__(
        self, *, base_url: str, model: str, api_key: str, provider: str, timeout: int
    ):
        self.base_url = base_url
        self.model = model
        self.api_key = api_key
        self.provider = provider
        self.timeout = timeout


def test_new_default_llm_client_uses_default_provider_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SimpleNamespace(
        LLM_DEFAULT_PROVIDER="deepseek",
        LLM_DEEPSEEK_BASE_URL="https://api.deepseek.com",
        LLM_DEEPSEEK_MODEL="deepseek-chat",
        LLM_DEEPSEEK_API_KEY=SecretStr("test-api-key"),
    )
    monkeypatch.setattr(llm_client_module, "settings", settings)
    monkeypatch.setattr(llm_client_module, "OpenAIClient", FakeOpenAIClient)

    client = llm_client_module.new_default_llm_client()

    assert client.base_url == "https://api.deepseek.com"
    assert client.model == "deepseek-chat"
    assert client.api_key == "test-api-key"
    assert client.provider == "deepseek"
    assert client.timeout == 60


def test_new_default_llm_client_rejects_missing_provider_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SimpleNamespace(
        LLM_DEFAULT_PROVIDER="deepseek",
        LLM_DEEPSEEK_BASE_URL="",
        LLM_DEEPSEEK_MODEL="deepseek-chat",
        LLM_DEEPSEEK_API_KEY=SecretStr("test-api-key"),
    )
    monkeypatch.setattr(llm_client_module, "settings", settings)

    with pytest.raises(AppException) as exc_info:
        llm_client_module.new_default_llm_client()

    assert exc_info.value.error == errors.ServiceUnavailable
    assert exc_info.value.message == "LLM provider 'deepseek' is not configured"
