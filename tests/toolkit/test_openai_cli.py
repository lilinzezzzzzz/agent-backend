from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from dotenv import dotenv_values
import httpx
from openai import APIStatusError
from openai.types.chat.chat_completion_chunk import (
    ChatCompletionChunk,
    Choice,
    ChoiceDelta,
)
from pydantic import AliasChoices, BaseModel, Field

from pkg.toolkit.openai_cli import (
    OpenAIClient,
    ProviderCapabilities,
    StructuredOutputMode,
    StructuredOutputRefusalError,
    ThinkingMode,
)


@dataclass(frozen=True)
class LLMProviderConfig:
    """OpenAI-compatible LLM provider config loaded from project env files."""

    provider: str
    base_url: str
    model: str
    api_key: str


class SummaryModel(BaseModel):
    """用于结构化输出测试的 Pydantic 模型"""

    title: str
    tags: list[str]


class LLMStructuredAnswer(BaseModel):
    """真实 OpenAI-compatible provider 结构化输出模型"""

    summary: str = Field(
        validation_alias=AliasChoices("summary", "answer", "explanation")
    )
    keywords: list[str]


class AsyncChunkStream:
    def __init__(self, chunks: list[ChatCompletionChunk]):
        self._chunks = chunks

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for chunk in self._chunks:
            yield chunk


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _provider_key(provider: str, field: str) -> str:
    normalized_provider = provider.upper().replace("-", "_")
    return f"LLM_{normalized_provider}_{field}"


def _load_llm_provider_config() -> LLMProviderConfig:
    configs_dir = _repo_root() / "configs"
    secrets_path = configs_dir / ".secrets"
    if not secrets_path.exists():
        pytest.skip("configs/.secrets not found")

    secrets = dotenv_values(secrets_path)
    app_env = secrets.get("APP_ENV")
    if not app_env:
        pytest.skip("APP_ENV not found in configs/.secrets")

    env_path = configs_dir / f".env.{app_env}"
    if not env_path.exists():
        pytest.skip(f"{env_path.name} not found")

    values = {
        **dotenv_values(env_path),
        **secrets,
    }
    provider = values.get("LLM_DEFAULT_PROVIDER")
    if not provider:
        pytest.skip("LLM_DEFAULT_PROVIDER not configured")

    base_url = values.get(_provider_key(provider, "BASE_URL"))
    model = values.get(_provider_key(provider, "MODEL"))
    api_key = values.get(_provider_key(provider, "API_KEY"))
    if not base_url or not model or not api_key:
        pytest.skip(
            f"{provider} LLM provider base_url, model, or api_key not configured"
        )

    return LLMProviderConfig(
        provider=provider,
        base_url=base_url,
        model=model,
        api_key=api_key,
    )


@pytest.fixture(scope="module")
def llm_provider_config() -> LLMProviderConfig:
    return _load_llm_provider_config()


@pytest.fixture(scope="module")
def llm_client(llm_provider_config: LLMProviderConfig) -> OpenAIClient:
    return OpenAIClient(
        base_url=llm_provider_config.base_url,
        model=llm_provider_config.model,
        api_key=llm_provider_config.api_key,
        timeout=60,
        provider=llm_provider_config.provider,
    )


@pytest.fixture
def mock_client() -> OpenAIClient:
    return OpenAIClient(
        base_url="https://example.test/v1",
        model="test-model",
        api_key="test-api-key",
    )


def test_get_completion_params_applies_deepseek_thinking_control():
    """测试 DeepSeek 思考控制会转换为 extra_body.thinking 和 reasoning_effort"""
    client = OpenAIClient(
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
        api_key="test-api-key",
        provider="deepseek",
    )

    params = client._get_completion_params(
        messages=[{"role": "user", "content": "hello"}],
        stream=False,
        thinking=True,
        reasoning_effort="high",
        extra_body={"custom": "value"},
    )

    assert params["reasoning_effort"] == "high"
    assert params["extra_body"] == {
        "custom": "value",
        "thinking": {"type": "enabled"},
    }


def test_get_completion_params_disables_deepseek_thinking_without_effort():
    """测试关闭思考时不会继续传递 reasoning_effort"""
    client = OpenAIClient(
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
        api_key="test-api-key",
        provider="deepseek",
    )

    params = client._get_completion_params(
        messages=[{"role": "user", "content": "hello"}],
        stream=False,
        thinking=False,
        reasoning_effort="high",
    )

    assert "reasoning_effort" not in params
    assert params["extra_body"] == {"thinking": {"type": "disabled"}}


def test_get_completion_params_uses_openai_reasoning_effort_without_extra_body():
    """测试 OpenAI 风格模型只使用顶层 reasoning_effort"""
    client = OpenAIClient(
        base_url="https://api.openai.com/v1",
        model="gpt-5-mini",
        api_key="test-api-key",
        provider="openai",
    )

    params = client._get_completion_params(
        messages=[{"role": "user", "content": "hello"}],
        stream=False,
        thinking=True,
        reasoning_effort="medium",
    )

    assert params["reasoning_effort"] == "medium"
    assert "extra_body" not in params


def test_get_completion_params_accepts_thinking_mode_enum_and_string():
    """测试思考模式既支持枚举，也兼容字符串输入"""
    client = OpenAIClient(
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
        api_key="test-api-key",
        provider="deepseek",
    )

    enum_params = client._get_completion_params(
        messages=[{"role": "user", "content": "hello"}],
        stream=False,
        thinking=ThinkingMode.ENABLED,
    )
    string_params = client._get_completion_params(
        messages=[{"role": "user", "content": "hello"}],
        stream=False,
        thinking="enabled",
    )

    assert enum_params["extra_body"] == {"thinking": {"type": "enabled"}}
    assert string_params["extra_body"] == {"thinking": {"type": "enabled"}}


@pytest.mark.asyncio
async def test_chat_completion_structured_success(mock_client: OpenAIClient):
    """测试结构化输出成功解析为指定 Pydantic 模型"""
    parsed = SummaryModel(title="OpenAI", tags=["llm", "structured-output"])
    completion = SimpleNamespace(
        id="chatcmpl_mock",
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    parsed=parsed,
                    refusal=None,
                )
            )
        ],
    )
    parse_mock = AsyncMock(return_value=completion)
    mock_client.client.chat.completions.parse = parse_mock
    audited_responses = []

    response = await mock_client.chat_completion_structured(
        messages=[{"role": "user", "content": "提取标题和标签"}],
        response_model=SummaryModel,
        temperature=0.1,
        _audit_hook=audited_responses.append,
    )

    assert response == parsed
    assert audited_responses == [completion]

    parse_mock.assert_awaited_once()
    kwargs = parse_mock.await_args.kwargs
    assert kwargs["model"] == "test-model"
    assert kwargs["response_format"] is SummaryModel
    assert kwargs["messages"][0]["role"] == "user"
    assert "_audit_hook" not in kwargs


@pytest.mark.asyncio
async def test_chat_completion_structured_refusal(mock_client: OpenAIClient):
    """测试结构化输出 refusal 会返回可判断的错误信息"""
    completion = SimpleNamespace(
        id="chatcmpl_mock_refusal",
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    parsed=None,
                    refusal="I cannot assist with that request.",
                )
            )
        ],
    )
    mock_client.client.chat.completions.parse = AsyncMock(return_value=completion)

    with pytest.raises(StructuredOutputRefusalError, match="I cannot assist"):
        await mock_client.chat_completion_structured(
            messages=[{"role": "user", "content": "提取标题和标签"}],
            response_model=SummaryModel,
        )


@pytest.mark.asyncio
async def test_chat_completion_structured_error_handling(mock_client: OpenAIClient):
    """测试结构化输出方法复用消息校验错误处理"""
    parse_mock = AsyncMock()
    mock_client.client.chat.completions.parse = parse_mock

    with pytest.raises(ValueError, match="missing role"):
        await mock_client.chat_completion_structured(
            messages=[{"content": "This message is invalid"}],
            response_model=SummaryModel,
        )

    parse_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_chat_completion_structured_falls_back_to_json_object():
    """测试 Structured Outputs 不可用时退回 JSON mode 并用 Pydantic 校验"""
    client = OpenAIClient(
        base_url="https://example.test/v1",
        model="test-model",
        api_key="test-api-key",
        provider_capabilities=ProviderCapabilities(
            name="test",
            structured_output_mode=StructuredOutputMode.NATIVE_WITH_JSON_OBJECT_FALLBACK,
        ),
    )
    http_response = httpx.Response(
        400,
        request=httpx.Request("POST", "https://example.test/v1/chat/completions"),
        json={"error": {"message": "This response_format type is unavailable now"}},
    )
    client.client.chat.completions.parse = AsyncMock(
        side_effect=APIStatusError(
            "This response_format type is unavailable now",
            response=http_response,
            body={"error": {"message": "This response_format type is unavailable now"}},
        )
    )
    fallback_completion = SimpleNamespace(
        id="chatcmpl_json_object",
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content='{"title": "OpenAI", "tags": ["json", "pydantic"]}',
                )
            )
        ],
    )
    create_mock = AsyncMock(return_value=fallback_completion)
    client.client.chat.completions.create = create_mock

    response = await client.chat_completion_structured(
        messages=[{"role": "user", "content": "请用 JSON 输出标题和标签"}],
        response_model=SummaryModel,
    )

    assert response == SummaryModel(title="OpenAI", tags=["json", "pydantic"])

    create_mock.assert_awaited_once()
    assert create_mock.await_args.kwargs["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_chat_completion_stream_skips_empty_choices(mock_client: OpenAIClient):
    """测试流式响应会跳过兼容服务返回的空 choices chunk"""
    empty_chunk = ChatCompletionChunk.model_construct(
        id="chunk_empty",
        choices=[],
        created=0,
        model="test-model",
        object="chat.completion.chunk",
    )
    content_chunk = ChatCompletionChunk.model_construct(
        id="chunk_content",
        choices=[
            Choice.model_construct(
                index=0,
                delta=ChoiceDelta.model_construct(content="hello"),
                finish_reason=None,
            )
        ],
        created=0,
        model="test-model",
        object="chat.completion.chunk",
    )
    mock_client.client.chat.completions.create = AsyncMock(
        return_value=AsyncChunkStream([empty_chunk, content_chunk])
    )

    chunks = [
        chunk
        async for chunk in mock_client.chat_completion_stream(
            messages=[{"role": "user", "content": "hello"}],
        )
    ]

    assert chunks == ["hello"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_openai_compatible_chat_completion(llm_client: OpenAIClient):
    """使用 configs/.secrets 和对应 .env 调用默认 OpenAI-compatible provider 非流式接口"""
    response = await llm_client.chat_completion(
        messages=[
            {"role": "system", "content": "你是大模型助手，请用简洁中文回答。"},
            {"role": "user", "content": "用一句话说明你是谁。"},
        ],
        temperature=0.2,
        top_p=0.95,
        max_completion_tokens=256,
    )

    content = response.choices[0].message.content
    assert isinstance(content, str)
    assert content.strip()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_deepseek_chat_completion_with_thinking_control(
    llm_provider_config: LLMProviderConfig,
    llm_client: OpenAIClient,
):
    """使用 DeepSeek 官方思考控制参数关闭思考模式"""
    if llm_provider_config.provider != "deepseek":
        pytest.skip("DeepSeek-specific thinking control test")

    response = await llm_client.chat_completion(
        messages=[
            {"role": "system", "content": "你是大模型助手，请用简洁中文回答。"},
            {"role": "user", "content": "用一句话说明什么是思考模式。"},
        ],
        thinking=False,
        max_completion_tokens=256,
    )

    content = response.choices[0].message.content
    assert isinstance(content, str)
    assert content.strip()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_openai_compatible_chat_completion_stream(llm_client: OpenAIClient):
    """使用 configs/.secrets 和对应 .env 调用默认 OpenAI-compatible provider 流式接口"""
    chunks: list[str] = []
    async for chunk in llm_client.chat_completion_stream(
        messages=[
            {"role": "system", "content": "你是大模型助手，请用简洁中文回答。"},
            {"role": "user", "content": "用一句话说明结构化输出的价值。"},
        ],
        temperature=0.2,
        top_p=0.95,
        max_completion_tokens=256,
    ):
        if chunk:
            chunks.append(chunk)

    content = "".join(chunks)
    assert content.strip()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_openai_compatible_chat_completion_structured(llm_client: OpenAIClient):
    """优先使用 Structured Outputs 调用默认 OpenAI-compatible provider 并解析为 Pydantic 模型"""
    response = await llm_client.chat_completion_structured(
        messages=[
            {
                "role": "system",
                "content": (
                    "你必须输出合法 JSON，字段名固定为 summary 和 keywords。"
                    '示例：{"summary": "一句话总结", "keywords": ["关键词1", "关键词2"]}'
                ),
            },
            {"role": "user", "content": "用一句话解释什么是 RAG，并给出两个关键词。"},
        ],
        response_model=LLMStructuredAnswer,
        temperature=0.2,
        top_p=0.95,
        max_completion_tokens=1024,
    )

    assert response.summary.strip()
    assert response.keywords
