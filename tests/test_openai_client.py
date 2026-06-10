import os
from typing import Any

import pytest

from pkg.llm import OpenAIClient

# --- 测试配置 ---
BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MODEL = "qwen-turbo-latest"
# 注意：这个 API Key (sk-e425e1e7e6ae44f299eb5388ca04adae) 只是一个示例，
# 实际运行时请确保它是一个有效且有权限的阿里云/通义千问 API Key。
API_KEY = os.environ.get("ALIYUN_API_KEY", "")


# ------------------


# 使用 pytest.fixture 来创建 client 实例，供所有测试用例使用
@pytest.fixture(scope="module")
def client() -> OpenAIClient:
    """提供一个 OpenAIClient 实例"""
    return OpenAIClient(base_url=BASE_URL, model=MODEL, api_key=API_KEY)


# 通用的用户消息列表
MESSAGES: list[dict[str, Any]] = [
    {"role": "user", "content": "请用一句话介绍一下通义千问模型。"}
]


@pytest.mark.asyncio
async def test_chat_completion_success(client: OpenAIClient):
    """测试非流式 chat_completion 方法的成功调用"""
    print(f"\n--- Testing chat_completion with model: {client.model} ---")

    response = await client.chat_completion(
        messages=MESSAGES, temperature=0.7, max_tokens=100
    )

    # 1. 断言响应内容有效
    content = response.choices[0].message.content
    assert isinstance(content, str) and len(content) > 10, (
        "Response content is too short or invalid"
    )

    print(f"Response Content: {content[:50]}...")


@pytest.mark.asyncio
async def test_chat_completion_stream_success(client: OpenAIClient):
    """测试流式 chat_completion_stream 方法的成功调用"""
    print(f"\n--- Testing chat_completion_stream with model: {client.model} ---")

    full_content = ""
    # 调用流式方法
    async_generator = client.chat_completion_stream(
        messages=MESSAGES, temperature=0.7, max_tokens=100
    )

    chunk_count = 0

    # 迭代并收集流式响应
    async for chunk in async_generator:
        if chunk is not None:
            full_content += chunk
            chunk_count += 1

    # 1. 断言接收到了多个块
    assert chunk_count > 1, "Stream did not yield enough chunks (or failed silently)"

    # 2. 断言最终内容有效
    assert isinstance(full_content, str) and len(full_content) > 10, (
        "Streamed content is too short or invalid"
    )

    print(f"Streamed Content: {full_content[:50]}...")


@pytest.mark.asyncio
async def test_chat_completion_error_handling(client: OpenAIClient):
    """测试 chat_completion 方法的错误处理 (例如：无效的消息结构)"""
    print("\n--- Testing chat_completion Error Handling ---")

    # 构造一个无效的消息列表 (缺少 role)
    invalid_messages: list[dict[str, Any]] = [{"content": "This message is invalid"}]

    with pytest.raises(ValueError, match="missing role"):
        await client.chat_completion(messages=invalid_messages)


@pytest.mark.asyncio
async def test_chat_completion_with_empty_messages(client: OpenAIClient):
    """测试空消息列表"""
    print("\n--- Testing chat_completion with Empty Messages ---")

    # 客户端在调用 API 之前会将空列表转换为 []，API 可能会返回错误。
    with pytest.raises(Exception):
        await client.chat_completion(messages=[])
