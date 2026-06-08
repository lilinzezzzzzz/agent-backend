from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Sequence
from typing import Any

from openai import AsyncOpenAI

from pkg.toolkit.string import mask_string
from pkg.vectors.embedders.base import BaseEmbedder
from pkg.vectors.errors import RecordValidationError

# =========================================================
# 默认配置常量
# =========================================================

VECTOR_DIMENSION: int = 1024
DEFAULT_EMBEDDING_TOKEN_LIMIT: int = 8191
DEFAULT_TIKTOKEN_OFFLINE: bool = True


class OpenAICompatibleEmbedder(BaseEmbedder):
    """OpenAI-compatible embeddings API 客户端。

    适用于 OpenAI embeddings、自建兼容服务，以及通过 vLLM、Xinference、
    TEI 等网关暴露 OpenAI-compatible `/embeddings` 的 BGE 向量服务。
    """

    def __init__(
        self,
        *,
        api_key: str,
        embedding_model_name: str,
        base_url: str | None = None,
        dimension: int | None = None,
        timeout: float | None = None,
        token_limit: int | None = None,
        offline_token_count: bool | None = None,
    ) -> None:
        super().__init__(dimension=dimension)
        if not api_key:
            raise ValueError("api_key 不能为空")
        if not embedding_model_name:
            raise ValueError("embedding_model_name 不能为空")

        self._api_key = api_key
        self._base_url = base_url
        # 已由上层完成 provider/model 解析，这里只保存最终可请求的模型名
        self._embedding_model_name = embedding_model_name
        self._timeout = timeout
        self._token_limit = token_limit
        self._offline_token_count = offline_token_count
        self._client: AsyncOpenAI | None = None

    @property
    def client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=self._api_key,
                base_url=self._base_url,
            )
        return self._client

    def _get_model_info(self) -> dict[str, str | None]:
        """获取模型信息（api_key 脱敏）"""
        return {
            "model": self._embedding_model_name,
            "base_url": self._base_url,
            "api_key": mask_string(self._api_key) if self._api_key else None,
        }

    async def embed_texts(
        self, *, texts: Sequence[str], dimension: int | None = None
    ) -> list[list[float]]:
        if not texts:
            return []

        effective_dimension = dimension if dimension is not None else self._dimension
        response = await self.client.embeddings.create(
            **self._build_request_kwargs(
                input_value=list(texts), dimension=effective_dimension
            ),
        )
        if not response.data:
            raise RecordValidationError("batch embedding 返回为空")

        vectors = [list(item.embedding) for item in response.data]
        self.validate_vectors(vectors, source="embed_texts")
        return vectors

    async def embed_texts_safe(
        self,
        *,
        texts: Sequence[str],
        dimension: int | None = None,
        caller_module: str = "embed_texts_safe",
    ) -> list[list[float]]:
        """批量文本向量化（带异常捕获和日志记录）"""
        if not texts:
            return []

        return await self._execute_safe(
            self.embed_texts(texts=texts, dimension=dimension),
            context_name=caller_module,
        )

    async def embed_text(
        self, *, text: str, dimension: int | None = None
    ) -> list[float]:
        effective_dimension = dimension if dimension is not None else self._dimension
        response = await self.client.embeddings.create(
            **self._build_request_kwargs(
                input_value=text, dimension=effective_dimension
            ),
        )
        if not response.data:
            raise RecordValidationError("query embedding 返回为空")

        vector = list(response.data[0].embedding)
        self.validate_vector(vector, source="embed_text")
        return vector

    async def embed_text_safe(
        self,
        *,
        text: str,
        dimension: int | None = None,
        caller_module: str = "embed_text_safe",
    ) -> list[float]:
        """单个文本向量化（带异常捕获和日志记录）"""
        return await self._execute_safe(
            self.embed_text(text=text, dimension=dimension),
            context_name=caller_module,
        )

    async def _execute_safe[T](
        self,
        coro: Awaitable[T],
        *,
        context_name: str,
    ) -> T:
        """执行协程并处理异常"""
        try:
            return await coro
        except asyncio.CancelledError:
            raise
        except TimeoutError as e:
            raise Exception(f"Embedding 请求超时: base_url={self._base_url}") from e
        except Exception as e:
            raise Exception(f"Embedding 请求失败: {e!s}") from e

    def _build_request_kwargs(
        self, *, input_value: str | list[str], dimension: int | None = None
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "input": input_value,
            "model": self._embedding_model_name,
        }
        if dimension is not None:
            kwargs["dimensions"] = dimension
        if self._timeout is not None:
            kwargs["timeout"] = self._timeout
        return kwargs


def create_openai_compatible_embedder(
    *,
    api_key: str,
    model_name: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    dimension: int = VECTOR_DIMENSION,
    timeout: float | None = None,
    token_limit: int = DEFAULT_EMBEDDING_TOKEN_LIMIT,
    offline_token_count: bool = DEFAULT_TIKTOKEN_OFFLINE,
) -> OpenAICompatibleEmbedder:
    resolved_model_name = model_name or model
    if not resolved_model_name:
        raise ValueError("model_name 不能为空")
    return OpenAICompatibleEmbedder(
        api_key=api_key,
        embedding_model_name=resolved_model_name,
        base_url=base_url,
        dimension=dimension,
        timeout=timeout,
        token_limit=token_limit,
        offline_token_count=offline_token_count,
    )


__all__ = [
    "DEFAULT_EMBEDDING_TOKEN_LIMIT",
    "DEFAULT_TIKTOKEN_OFFLINE",
    "OpenAICompatibleEmbedder",
    "VECTOR_DIMENSION",
    "create_openai_compatible_embedder",
]
