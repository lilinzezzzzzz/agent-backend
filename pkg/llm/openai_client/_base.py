from types import TracebackType
from typing import Self

import openai


class BaseOpenAIClient:
    """共享 SDK 连接配置；注入的连接由调用方负责关闭。"""

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout: float = 180,
        api_key: str = "password",
        provider: str | None = None,
        client: openai.AsyncOpenAI | None = None,
    ):
        self.base_url = base_url
        self.model = model
        self.provider = self._normalize_provider(provider) or self._infer_provider(
            base_url, model
        )
        self._owns_client = client is None
        self.client = (
            client
            if client is not None
            else openai.AsyncOpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=timeout,
            )
        )

    @staticmethod
    def _normalize_provider(provider: str | None) -> str | None:
        if not provider:
            return None
        return provider.lower().replace("-", "_")

    @classmethod
    def _infer_provider(cls, base_url: str, model: str) -> str:
        value = f"{base_url} {model}".lower()
        if "deepseek" in value:
            return "deepseek"
        if "xiaomimimo" in value or "mimo" in value:
            return "mimo"
        if "openai" in value:
            return "openai"
        return "openai_compatible"

    async def close(self) -> None:
        if self._owns_client:
            await self.client.close()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()
