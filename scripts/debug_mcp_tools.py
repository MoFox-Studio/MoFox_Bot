"""
调试 MCP 工具列表获取

直接测试 MCP 客户端是否能获取工具
"""

import asyncio

from fastmcp.client import Client, StreamableHttpTransport


async def test_direct_connection():
    """直接连接 MCP 服务器并获取工具列表"""
    print("=" * 60)
    print("直接测试 MCP 服务器连接")
    print("=" * 60)

    url = "http://localhost:8000/mcp"
    print(f"\n连接到: {url}")

    try:
        # 创建传输层
        transport = StreamableHttpTransport(url)
        print("✓ 传输层创建成功")

        # 创建客户端
        async with Client(transport) as client:
            print("✓ 客户端连接成功")

            # 获取工具列表
            print("\n正在获取工具列表...")
            tools_result = await client.list_tools()

            print(f"\n获取结果类型: {type(tools_result)}")
            print(f"结果内容: {tools_result}")

            # 检查是否有 tools 属性
            if hasattr(tools_result, "tools"):
                tools = tools_result.tools
                print(f"\n✓ 找到 tools 属性，包含 {len(tools)} 个工具")

                for i, tool in enumerate(tools, 1):
                    print(f"\n工具 {i}:")
                    print(f"  名称: {tool.name}")
                    print(f"  描述: {tool.description}")
                    if hasattr(tool, "inputSchema"):
                        print(f"  参数 Schema: {tool.inputSchema}")
            else:
                print("\n✗ 结果中没有 tools 属性")
                print(f"可用属性: {dir(tools_result)}")

    except Exception as e:
        print(f"\n✗ 连接失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_direct_connection())
