"""MCP SSE 客户端 —— 通过 HTTP SSE 连接线上 MCP 服务并调用工具。

与 stdio 客户端相比，仅传输层不同，ClientSession 用法完全一致。
"""

import asyncio

from mcp.client.session import ClientSession
from mcp.client.sse import sse_client
from mcp.types import TextContent

# 服务端地址（先启动 mcp_server_sse.py）
SSE_URL = "http://127.0.0.1:8000/sse"


async def main() -> None:
    """连接 SSE 服务端，列出工具并调用。"""

    async with (
        sse_client(SSE_URL) as (read, write),
        ClientSession(read, write) as session,
    ):
        # --- 1. 初始化会话 ---
        await session.initialize()
        print("✅ 已连接到 SSE MCP 服务端\n")

        # --- 2. 列出所有可用工具 ---
        tools_result = await session.list_tools()
        print(f"📋 可用工具 ({len(tools_result.tools)} 个):")
        for tool in tools_result.tools:
            print(f"  - {tool.name}: {tool.description}")
        print()

        # --- 3. 依次调用每个工具 ---
        test_cases: list[tuple[str, dict]] = [
            ("get_weather", {"city": "Beijing"}),
            ("get_weather", {"city": "Shanghai", "unit": "fahrenheit"}),
            ("calculator", {"expression": "2 + 3 * 4"}),
            ("calculator", {"expression": "(100 - 20) / 4"}),
            ("get_current_time", {"timezone_offset": "+8"}),
            ("get_current_time", {"timezone_offset": "-5"}),
        ]

        for name, args in test_cases:
            result = await session.call_tool(name, args)
            content = next(
                (item.text for item in result.content if isinstance(item, TextContent)),
                "(无文本输出)" if result.content else "(无输出)",
            )
            print(f"🔧 {name}({args}) → {content}")

        print(f"\n共调用 {len(test_cases)} 次工具，全部完成。")


if __name__ == "__main__":
    asyncio.run(main())
