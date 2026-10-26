from __future__ import annotations

from pydantic import SecretStr

from internal.config import settings
from internal.core import AppException, errors
from pkg.llm import OpenAIResponsesClient


def _provider_config_value(*, provider: str, field: str) -> str:
    provider_key = provider.lower().replace("-", "_")
    if field == "API_KEY":
        value = getattr(settings.secrets, f"llm_{provider_key}_api_key", "")
    else:
        provider_settings = getattr(settings.llm, provider_key, None)
        value = (
            getattr(provider_settings, field.lower(), "") if provider_settings else ""
        )
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    return str(value or "")


def new_default_llm_client() -> OpenAIResponsesClient:
    """根据默认 LLM provider 配置创建 Responses API 客户端。"""
    provider = settings.llm.default_provider
    base_url = _provider_config_value(provider=provider, field="BASE_URL")
    model = _provider_config_value(provider=provider, field="MODEL")
    api_key = _provider_config_value(provider=provider, field="API_KEY")

    if not base_url or not model or not api_key:
        raise AppException(
            errors.ServiceUnavailable,
            message=f"LLM provider '{provider}' is not configured",
        )

    return OpenAIResponsesClient(
        base_url=base_url,
        model=model,
        api_key=api_key,
        provider=provider,
        timeout=settings.agent.llm_timeout_seconds,
    )
