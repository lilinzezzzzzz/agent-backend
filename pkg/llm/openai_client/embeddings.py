from collections.abc import Sequence

import openai
from openai import NOT_GIVEN
from openai.types import CreateEmbeddingResponse

from pkg.embeddings.base import BaseEmbedder
from pkg.embeddings.errors import (
    EmbeddingResponseValidationError,
    InvalidEmbeddingDimensionError,
)
from pkg.llm.openai_client._base import BaseOpenAIClient

VECTOR_DIMENSION: int = 1024


class OpenAIEmbeddingsClient(BaseOpenAIClient, BaseEmbedder):
    """统一 embeddings API 与 Embedder 实现，复用公共 SDK 生命周期。"""

    def __init__(
        self,
        base_url: str = "https://api.openai.com/v1",
        model: str = "",
        timeout: float = 180,
        api_key: str = "password",
        provider: str | None = None,
        *,
        dimension: int | None = None,
        client: openai.AsyncOpenAI | None = None,
    ) -> None:
        if not api_key or not model:
            raise ValueError("api_key 和 model 不能为空")
        BaseEmbedder.__init__(self, dimension=dimension)
        BaseOpenAIClient.__init__(
            self, base_url, model, timeout, api_key, provider, client
        )

    async def embeddings(
        self,
        *,
        input: str | Sequence[str],
        dimensions: int | None = None,
    ) -> CreateEmbeddingResponse:
        return await self.client.embeddings.create(
            input=input if isinstance(input, str) else list(input),
            model=self.model,
            dimensions=dimensions if dimensions is not None else NOT_GIVEN,
            encoding_format="float",
        )

    async def embed_texts(
        self,
        *,
        texts: Sequence[str],
        dimension: int | None = None,
    ) -> list[list[float]]:
        if not texts:
            return []
        if len(texts) > 2048:
            raise ValueError("单批文本数量不能超过 2048，请由调用方分批")
        effective_dimension = dimension if dimension is not None else self._dimension
        if effective_dimension is not None and effective_dimension <= 0:
            raise InvalidEmbeddingDimensionError("dimension 必须为正整数")
        response = await self.embeddings(input=texts, dimensions=effective_dimension)
        if len(response.data) != len(texts):
            raise EmbeddingResponseValidationError("embedding 结果数量与输入不一致")
        indices = [item.index for item in response.data]
        if any(type(index) is not int for index in indices) or sorted(indices) != list(
            range(len(texts))
        ):
            raise EmbeddingResponseValidationError("embedding index 重复、缺失或越界")
        vectors = [
            list(item.embedding)
            for item in sorted(response.data, key=lambda item: item.index)
        ]
        for vector in vectors:
            if effective_dimension is not None and len(vector) != effective_dimension:
                raise InvalidEmbeddingDimensionError(
                    f"embedding 维度不匹配: got={len(vector)}, expected={effective_dimension}"
                )
        return vectors

    async def embed_text(
        self, *, text: str, dimension: int | None = None
    ) -> list[float]:
        return (await self.embed_texts(texts=[text], dimension=dimension))[0]

    async def embed_texts_safe(
        self,
        *,
        texts: Sequence[str],
        dimension: int | None = None,
        caller_module: str = "embed_texts_safe",
    ) -> list[list[float]]:
        """保留批量便捷入口，SDK 异常与取消原样传播。"""
        return await self.embed_texts(texts=texts, dimension=dimension)


def create_openai_embedder(
    *,
    api_key: str,
    model_name: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    dimension: int = VECTOR_DIMENSION,
    timeout: float | None = None,
) -> OpenAIEmbeddingsClient:
    resolved_model = model_name or model
    if not resolved_model:
        raise ValueError("model_name 不能为空")
    return OpenAIEmbeddingsClient(
        api_key=api_key,
        model=resolved_model,
        base_url=base_url or "https://api.openai.com/v1",
        dimension=dimension,
        timeout=timeout if timeout is not None else 600,
    )
