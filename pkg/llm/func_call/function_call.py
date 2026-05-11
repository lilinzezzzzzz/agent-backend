"""DeepSeek function call 示例。

演示如何定义 tools、让模型自动选择调用函数、执行本地函数后
将结果返回模型获得最终答复。
"""

import json
import os
from collections.abc import Callable
from typing import Any, cast

from openai import OpenAI
from openai.types.chat import (
    ChatCompletionAssistantMessageParam,
    ChatCompletionMessageParam,
    ChatCompletionToolMessageParam,
    ChatCompletionToolParam,
)

# ---------------------------------------------------------------------------
# 1. 客户端初始化
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


# ---------------------------------------------------------------------------
# 2. 定义 tools（函数签名）
# ---------------------------------------------------------------------------

TOOLS: list[ChatCompletionToolParam] = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "获取指定城市的天气信息",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "城市名称，例如 Beijing、Shanghai",
                    },
                    "unit": {
                        "type": "string",
                        "enum": ["celsius", "fahrenheit"],
                        "description": "温度单位，默认 celsius",
                    },
                },
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_email",
            "description": "发送一封邮件",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {
                        "type": "string",
                        "description": "收件人邮箱地址",
                    },
                    "subject": {
                        "type": "string",
                        "description": "邮件主题",
                    },
                    "body": {
                        "type": "string",
                        "description": "邮件正文",
                    },
                },
                "required": ["to", "subject", "body"],
            },
        },
    },
]

# ---------------------------------------------------------------------------
# 3. 本地函数实现（mock）
# ---------------------------------------------------------------------------


FunctionResult = dict[str, Any]
ToolFunction = Callable[..., FunctionResult]


def get_weather(city: str, unit: str = "celsius") -> FunctionResult:
    """Mock 天气查询，实际项目中替换为真实 API 调用。"""
    return {
        "city": city,
        "temperature": 22 if unit == "celsius" else 72,
        "unit": unit,
        "condition": "晴天",
    }


def send_email(to: str, subject: str, body: str) -> FunctionResult:
    """Mock 邮件发送，实际项目中替换为真实 SMTP 调用。"""
    print(f"\n[模拟发送邮件] → {to}")
    print(f"  主题: {subject}")
    return {"status": "success", "recipient": to, "subject": subject}


# 工具名 → 可调用函数映射
AVAILABLE_FUNCTIONS: dict[str, ToolFunction] = {
    "get_weather": get_weather,
    "send_email": send_email,
}

# ---------------------------------------------------------------------------
# 4. 核心执行逻辑
# ---------------------------------------------------------------------------


def run_conversation() -> None:
    """发起一次完整的 function call 对话。"""
    client = create_deepseek_client()
    messages: list[ChatCompletionMessageParam] = [
        {
            "role": "system",
            "content": "你是一个有用的助手，可以查询天气和发送邮件。请用中文回复用户。",
        },
        {"role": "user", "content": "北京今天天气怎么样？"},
    ]

    # --- 第一次请求：模型判断是否需要调用函数 ---
    response = client.chat.completions.create(
        model="deepseek-v4-pro",
        messages=messages,
        tools=TOOLS,
        stream=False,
        reasoning_effort="high",
        extra_body={"thinking": {"type": "enabled"}},
    )

    assistant_message = response.choices[0].message
    tool_calls = assistant_message.tool_calls

    # 如果没有 tool_calls，直接输出
    if not tool_calls:
        print("模型回复:", assistant_message.content)
        return

    # --- 执行函数调用 ---
    messages.append(
        cast(
            ChatCompletionAssistantMessageParam,
            assistant_message.model_dump(exclude_none=True),
        )
    )  # 将 assistant 消息加入上下文

    for tool_call in tool_calls:
        if tool_call.type != "function":
            result: FunctionResult = {"error": f"不支持的工具类型: {tool_call.type}"}
            print(f"\n🔧 跳过工具调用: {tool_call.type}")
        else:
            fn_name = tool_call.function.name
            raw_args = json.loads(tool_call.function.arguments)
            if not isinstance(raw_args, dict):
                result = {"error": f"函数参数必须是 JSON object: {tool_call.function.arguments}"}
            else:
                fn_args = cast(dict[str, Any], raw_args)

                print(f"\n调用函数: {fn_name}({json.dumps(fn_args, ensure_ascii=False)})")

                fn = AVAILABLE_FUNCTIONS.get(fn_name)
                result = {"error": f"未知函数: {fn_name}"} if fn is None else fn(**fn_args)

        print(f"   返回结果: {json.dumps(result, ensure_ascii=False)}")

        tool_message: ChatCompletionToolMessageParam = {
            "role": "tool",
            "tool_call_id": tool_call.id,
            "content": json.dumps(result, ensure_ascii=False),
        }
        messages.append(tool_message)

    # --- 第二次请求：将函数结果发回模型，获取最终回复 ---
    second_response = client.chat.completions.create(
        model="deepseek-v4-pro",
        messages=messages,
        stream=False,
        reasoning_effort="high",
        extra_body={"thinking": {"type": "enabled"}},
    )

    final = second_response.choices[0].message.content
    print(f"\n🤖 最终回复: {final}")


# ---------------------------------------------------------------------------
# 5. 入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    run_conversation()
