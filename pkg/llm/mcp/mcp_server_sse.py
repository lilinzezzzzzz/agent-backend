"""MCP SSE 服务端 —— 线上部署模式，基于 HTTP + Server-Sent Events。

启动后监听 http://127.0.0.1:8000/sse，客户端通过 HTTP 连接。
"""

from datetime import datetime, timezone

from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# 1. 创建 FastMCP 服务端实例
# ---------------------------------------------------------------------------

mcp = FastMCP("Demo MCP Server (SSE)")


# ---------------------------------------------------------------------------
# 2. 注册工具
# ---------------------------------------------------------------------------


@mcp.tool()
def get_weather(city: str, unit: str = "celsius") -> str:
    """获取指定城市的天气信息。

    Args:
        city: 城市名称，例如 Beijing、Shanghai
        unit: 温度单位，celsius 或 fahrenheit
    """
    weather_data = {
        "beijing": {"celsius": "22°C 晴天", "fahrenheit": "72°F 晴天"},
        "shanghai": {"celsius": "25°C 多云", "fahrenheit": "77°F 多云"},
        "shenzhen": {"celsius": "28°C 阵雨", "fahrenheit": "82°F 阵雨"},
    }
    key = city.lower()
    if key not in weather_data:
        return f"未找到城市 {city} 的天气数据"
    return weather_data[key].get(unit, weather_data[key]["celsius"])


@mcp.tool()
def calculator(expression: str) -> str:
    """安全地计算数学表达式。

    Args:
        expression: 数学表达式，例如 2 + 3 * 4
    """
    allowed = set("0123456789+-*/().% ")
    if not all(ch in allowed for ch in expression):
        return "错误：表达式包含不允许的字符"
    try:
        result = eval(expression, {"__builtins__": {}}, {})
        return f"{expression} = {result}"
    except Exception as e:
        return f"计算错误：{e}"


@mcp.tool()
def get_current_time(timezone_offset: str = "+8") -> str:
    """获取当前时间。

    Args:
        timezone_offset: 时区偏移，例如 +8（东八区）、-5（西五区）
    """
    try:
        offset_hours = int(timezone_offset)
        tz = timezone(offset_hours * 3600)  # type: ignore[arg-type]
        now = datetime.now(tz)
        return now.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# 3. 启动 SSE 服务（HTTP 部署）
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("🚀 MCP SSE 服务启动: http://127.0.0.1:8000/sse")
    mcp.run(transport="sse")
