from __future__ import annotations

from pydantic import SecretStr

from internal.config import settings
from internal.core import AppException, errors
from pkg.toolkit.openai_cli import OpenAIClient


def _provider_config_value(*, provider: str, field: str) -> str:
    setting_name = f"LLM_{provider.upper().replace('-', '_')}_{field}"
    value = getattr(settings, setting_name, "")
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    return str(value or "")


def new_default_llm_client() -> OpenAIClient:
    """根据默认 LLM provider 配置创建 OpenAI-compatible 客户端。"""
    provider = settings.LLM_DEFAULT_PROVIDER
    base_url = _provider_config_value(provider=provider, field="BASE_URL")
    model = _provider_config_value(provider=provider, field="MODEL")
    api_key = _provider_config_value(provider=provider, field="API_KEY")

    if not base_url or not model or not api_key:
        raise AppException(
            errors.ServiceUnavailable,
            message=f"LLM provider '{provider}' is not configured",
        )

    return OpenAIClient(
        base_url=base_url,
        model=model,
        api_key=api_key,
        provider=provider,
        timeout=60,
    )
