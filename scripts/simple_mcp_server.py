"""
简单的 MCP 测试服务器

使用 fastmcp 创建一个简单的 MCP 服务器供测试使用。

安装依赖:
    pip install fastmcp uvicorn

运行服务器:
    python scripts/simple_mcp_server.py

服务器将在 http://localhost:8000/mcp 提供 MCP 服务
"""

from fastmcp import FastMCP

# 创建 MCP 服务器实例
mcp = FastMCP("Demo Server")


@mcp.tool()
def add(a: int, b: int) -> int:
    """将两个数字相加

    Args:
        a: 第一个数字
        b: 第二个数字

    Returns:
        两个数字的和
    """
    return a + b


@mcp.tool()
def multiply(a: float, b: float) -> float:
    """将两个数字相乘

    Args:
        a: 第一个数字
        b: 第二个数字

    Returns:
        两个数字的乘积
    """
    return a * b


@mcp.tool()
def get_weather(city: str) -> str:
    """获取指定城市的天气信息(模拟)

    Args:
        city: 城市名称

    Returns:
        天气信息字符串
    """
    # 这是一个模拟实现
    weather_data = {
        "beijing": "北京今天晴朗，温度 20°C",
        "shanghai": "上海今天多云，温度 18°C",
        "guangzhou": "广州今天有雨，温度 25°C",
    }

    city_lower = city.lower()
    return weather_data.get(
        city_lower,
        f"{city}的天气信息暂不可用"
    )


@mcp.tool()
def echo(message: str, repeat: int = 1) -> str:
    """重复输出一条消息

    Args:
        message: 要重复的消息
        repeat: 重复次数，默认为 1

    Returns:
        重复后的消息
    """
    return (message + "\n") * repeat


@mcp.tool()
def check_prime(number: int) -> bool:
    """检查一个数字是否为质数

    Args:
        number: 要检查的数字

    Returns:
        如果是质数返回 True，否则返回 False
    """
    if number < 2:
        return False

    for i in range(2, int(number ** 0.5) + 1):
        if number % i == 0:
            return False

    return True


if __name__ == "__main__":
    print("=" * 60)
    print("简单 MCP 测试服务器")
    print("=" * 60)
    print("\n服务器配置:")
    print("  - 传输协议: Streamable HTTP")
    print("  - 地址: http://localhost:8000/mcp")
    print("\n可用工具:")
    print("  1. add(a: int, b: int) -> int")
    print("  2. multiply(a: float, b: float) -> float")
    print("  3. get_weather(city: str) -> str")
    print("  4. echo(message: str, repeat: int = 1) -> str")
    print("  5. check_prime(number: int) -> bool")
    print("\n配置示例 (config/mcp.json):")
    print("""
{
  "$schema": "./mcp.schema.json",
  "mcpServers": {
    "demo_server": {
      "enabled": true,
      "transport": {
        "type": "streamable-http",
        "url": "http://localhost:8000/mcp"
      },
      "timeout": 30
    }
  }
}
    """)
    print("=" * 60)
    print("\n正在启动服务器...")
    print("请参考 fastmcp 官方文档了解如何运行此服务器。")
    print("文档: https://github.com/jlowin/fastmcp")
    print("\n基本命令:")
    print("  fastmcp run simple_mcp_server:mcp")
    print("=" * 60)
