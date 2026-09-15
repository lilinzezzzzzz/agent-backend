import json
from unittest.mock import AsyncMock

import httpx
import openai
import pytest

from pkg.embeddings.errors import (
    EmbeddingResponseValidationError,
    InvalidEmbeddingDimensionError,
)
from pkg.llm.openai_client import OpenAIEmbeddingsClient


@pytest.mark.asyncio
@pytest.mark.parametrize("dimension", [None, 2])
async def test_batch_restores_order_and_uses_effective_dimension(dimension):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "object": "list",
                "model": "test",
                "usage": {"prompt_tokens": 2, "total_tokens": 2},
                "data": [
                    {"object": "embedding", "index": 1, "embedding": [3.0, 4.0]},
                    {"object": "embedding", "index": 0, "embedding": [1.0, 2.0]},
                ],
            },
        )

    async with openai.AsyncOpenAI(
        api_key="test",
        base_url="https://example.test/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    ) as sdk:
        embedder = OpenAIEmbeddingsClient(
            base_url="https://example.test/v1",
            model="test",
            client=sdk,
            dimension=3 if dimension else 2,
        )
        assert await embedder.embed_texts(texts=["a", "b"], dimension=dimension) == [
            [1.0, 2.0],
            [3.0, 4.0],
        ]
        await embedder.close()
        assert not sdk.is_closed()
    assert seen[0].url.path == "/v1/embeddings"
    body = json.loads(seen[0].content)
    assert body == {
        "input": ["a", "b"],
        "model": "test",
        "dimensions": 2,
        "encoding_format": "float",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data,error",
    [
        ([], EmbeddingResponseValidationError),
        ([{"index": 0, "embedding": [1.0, 2.0]}], EmbeddingResponseValidationError),
        (
            [
                {"index": 0, "embedding": [1.0, 2.0]},
                {"index": 0, "embedding": [1.0, 2.0]},
            ],
            EmbeddingResponseValidationError,
        ),
        (
            [
                {"index": 0, "embedding": [1.0, 2.0]},
                {"index": 2, "embedding": [1.0, 2.0]},
            ],
            EmbeddingResponseValidationError,
        ),
        (
            [
                {"index": -1, "embedding": [1.0, 2.0]},
                {"index": 1, "embedding": [1.0, 2.0]},
            ],
            EmbeddingResponseValidationError,
        ),
        (
            [{"index": 0, "embedding": [1.0]}, {"index": 1, "embedding": [1.0, 2.0]}],
            InvalidEmbeddingDimensionError,
        ),
    ],
)
async def test_invalid_batch_response_rejected(data, error):
    def handler(request):
        return httpx.Response(
            200,
            json={
                "object": "list",
                "model": "test",
                "data": data,
                "usage": {"prompt_tokens": 1, "total_tokens": 1},
            },
        )

    async with openai.AsyncOpenAI(
        api_key="test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    ) as sdk:
        embedder = OpenAIEmbeddingsClient(
            base_url="https://example.test", model="test", dimension=3, client=sdk
        )
        with pytest.raises(error):
            await embedder.embed_texts(texts=["a", "b"], dimension=2)


@pytest.mark.asyncio
async def test_empty_batch_does_not_request_and_owned_client_closes():
    async with OpenAIEmbeddingsClient(
        api_key="test",
        model="test",
        base_url="https://example.test",
        timeout=0.5,
    ) as embedder:
        sdk = embedder.client
        sdk.embeddings.create = AsyncMock()
        assert await embedder.embed_texts(texts=[]) == []
        sdk.embeddings.create.assert_not_called()
        assert sdk.timeout == 0.5
    assert sdk.is_closed()


@pytest.mark.asyncio
async def test_single_text_and_query_share_dimension_validation():
    def handler(request):
        assert json.loads(request.content)["dimensions"] == 2
        return httpx.Response(
            200,
            json={
                "object": "list",
                "model": "test",
                "data": [{"index": 0, "embedding": [1.0, 2.0]}],
                "usage": {"prompt_tokens": 1, "total_tokens": 1},
            },
        )

    async with openai.AsyncOpenAI(
        api_key="test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    ) as sdk:
        embedder = OpenAIEmbeddingsClient(
            base_url="https://example.test", model="test", dimension=3, client=sdk
        )
        assert await embedder.embed_text(text="a", dimension=2) == [1.0, 2.0]
        assert await embedder.embed_query(text="a", dimension=2) == [1.0, 2.0]
        assert await embedder.embed_text_safe(text="a", dimension=2) == [1.0, 2.0]


@pytest.mark.asyncio
async def test_sdk_timeout_and_cancellation_propagate():
    import asyncio

    async with OpenAIEmbeddingsClient(
        base_url="https://example.test", model="test"
    ) as client:
        for error in [
            openai.APITimeoutError(
                request=httpx.Request("POST", "https://example.test")
            ),
            asyncio.CancelledError(),
        ]:
            client.embeddings = AsyncMock(side_effect=error)
            with pytest.raises(type(error)):
                await client.embed_texts_safe(texts=["a"])


@pytest.mark.asyncio
async def test_raw_client_preserves_usage_and_omits_unspecified_dimensions():
    def handler(request):
        assert "dimensions" not in json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "object": "list",
                "model": "test",
                "data": [{"index": 0, "embedding": [1.0]}],
                "usage": {"prompt_tokens": 4, "total_tokens": 4},
            },
        )

    async with openai.AsyncOpenAI(
        api_key="test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    ) as sdk:
        client = OpenAIEmbeddingsClient(
            base_url="https://example.test", model="test", client=sdk
        )
        result = await client.embeddings(input="a")
        assert result.usage.total_tokens == 4


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "texts,dimension,error",
    [
        (["a"] * 2049, 2, ValueError),
        (["a"], 0, InvalidEmbeddingDimensionError),
        (["a"], -1, InvalidEmbeddingDimensionError),
    ],
)
async def test_invalid_request_rejected_before_network(texts, dimension, error):
    async with OpenAIEmbeddingsClient(api_key="test", model="test") as embedder:
        embedder.client.embeddings.create = AsyncMock()
        with pytest.raises(error):
            await embedder.embed_texts(texts=texts, dimension=dimension)
        embedder.client.embeddings.create.assert_not_called()
