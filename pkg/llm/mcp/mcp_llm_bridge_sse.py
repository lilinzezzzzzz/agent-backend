"""大模型 + MCP SSE 桥接 —— DeepSeek 通过 HTTP SSE 发现并调用线上 MCP 工具。

与 stdio 桥接相比，仅传输层从 stdio_client 换成 sse_client，
LLM function call 流程完全不变。
"""

import asyncio
import json
import os
from typing import Any, cast

from openai import OpenAI
from openai.types.chat import (
    ChatCompletionAssistantMessageParam,
    ChatCompletionMessageParam,
    ChatCompletionToolMessageParam,
    ChatCompletionToolParam,
)

from mcp.client.session import ClientSession
from mcp.client.sse import sse_client
from mcp.types import Tool

# ---------------------------------------------------------------------------
# DeepSeek 客户端
# ---------------------------------------------------------------------------


def create_deepseek_client() -> OpenAI:
    """创建 DeepSeek 兼容 OpenAI SDK 的客户端。"""
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("请先设置环境变量 DEEPSEEK_API_KEY")

    return OpenAI(
        api_key=api_key,
        base_url="https://api.deepseek.com",
    )


# 线上 MCP 服务地址（先启动 mcp_server_sse.py）
SSE_URL = "http://127.0.0.1:8000/sse"


# ---------------------------------------------------------------------------
# MCP Tool → OpenAI Tool 转换
# ---------------------------------------------------------------------------


def mcp_tool_to_openai(tool: Tool) -> ChatCompletionToolParam:
    """将 MCP Tool 对象转换为 OpenAI function call 格式。"""
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": cast(dict[str, object], tool.inputSchema),
        },
    }


# ---------------------------------------------------------------------------
# 核心逻辑
# ---------------------------------------------------------------------------


async def run() -> None:
    """连接 SSE MCP 服务端，发起 LLM + MCP 对话。"""
    llm_client = create_deepseek_client()

    async with (
        sse_client(SSE_URL) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()

        # --- 1. 从线上服务发现工具 ---
        tools_result = await session.list_tools()
        tools: list[ChatCompletionToolParam] = [mcp_tool_to_openai(t) for t in tools_result.tools]

        print(f"🔌 已从 SSE 服务端加载 {len(tools)} 个工具:")
        for t in tools:
            print(f"   - {t['function']['name']}")
        print()

        # --- 2. 用户提问 ---
        user_query = "北京今天天气怎么样？顺便帮我算一下 128 * 256 等于多少，再告诉我现在几点了"  # noqa: E501
        print(f"🙋 用户: {user_query}\n")

        messages: list[ChatCompletionMessageParam] = [
            {
                "role": "system",
                "content": (
                    "你是一个有用的助手，可以查询天气、计算数学表达式、获取当前时间。"
                    "请用中文回复用户。当用户同时提出多个问题时，一次性调用所有需要的工具。"
                ),
            },
            {"role": "user", "content": user_query},
        ]

        # --- 3. DeepSeek 判断调用哪些工具 ---
        print("⏳ 发送给 DeepSeek，等待工具调用决策...")
        response = llm_client.chat.completions.create(
            model="deepseek-v4-pro",
            messages=messages,
            tools=tools,
            stream=False,
            reasoning_effort="high",
            extra_body={"thinking": {"type": "enabled"}},
        )

        assistant_message = response.choices[0].message
        tool_calls = assistant_message.tool_calls

        if not tool_calls:
            print(f"🤖 模型直接回复: {assistant_message.content}")
            return

        # --- 4. 通过 SSE 执行 MCP 工具 ---
        messages.append(
            cast(
                ChatCompletionAssistantMessageParam,
                assistant_message.model_dump(exclude_none=True),
            )
        )

        for tool_call in tool_calls:
            if tool_call.type != "function":
                result_text = f"不支持的工具类型: {tool_call.type}"
                print(f"🔧 跳过工具调用: {tool_call.type}")
            else:
                fn_name = tool_call.function.name
                raw_args = json.loads(tool_call.function.arguments)
                if not isinstance(raw_args, dict):
                    result_text = f"函数参数必须是 JSON object: {tool_call.function.arguments}"
                else:
                    fn_args = cast(dict[str, Any], raw_args)

                    print(
                        f"🔧 SSE MCP 调用: {fn_name}({json.dumps(fn_args, ensure_ascii=False)})"  # noqa: E501
                    )

                    mcp_result = await session.call_tool(fn_name, fn_args)
                    result_text = ""
                    if mcp_result.content:
                        first_content = mcp_result.content[0]
                        if first_content.type == "text":
                            result_text = first_content.text

                    print(f"   返回: {result_text}")

            tool_message: ChatCompletionToolMessageParam = {
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result_text,
            }
            messages.append(tool_message)

        # --- 5. 将结果返回 DeepSeek ---
        print("\n⏳ 将工具结果发回 DeepSeek，获取最终回复...\n")
        second_response = llm_client.chat.completions.create(
            model="deepseek-v4-pro",
            messages=messages,
            stream=False,
            reasoning_effort="high",
            extra_body={"thinking": {"type": "enabled"}},
        )

        final = second_response.choices[0].message.content
        print(f"🤖 最终回复:\n{final}")


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    asyncio.run(run())
