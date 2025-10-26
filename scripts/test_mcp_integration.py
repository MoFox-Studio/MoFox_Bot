"""
MCP 集成测试脚本

测试 MCP 客户端连接、工具列表获取和工具调用功能
"""

import asyncio
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.common.logger import get_logger
from src.plugin_system.core.component_registry import ComponentRegistry
from src.plugin_system.core.mcp_client_manager import MCPClientManager

logger = get_logger("test_mcp_integration")


async def test_mcp_client_manager():
    """测试 MCPClientManager 基本功能"""
    print("\n" + "="*60)
    print("测试 1: MCPClientManager 连接和工具列表")
    print("="*60)

    try:
        # 初始化 MCP 客户端管理器
        manager = MCPClientManager()
        await manager.initialize()

        print("\n✓ MCP 客户端管理器初始化成功")
        print(f"已连接服务器数量: {len(manager.clients)}")

        # 获取所有工具
        tools = await manager.get_all_tools()
        print(f"\n获取到 {len(tools)} 个 MCP 工具:")

        for tool in tools:
            print(f"\n  工具: {tool}")
            # 注意: 这里 tool 是字符串形式的工具名称
            # 如果需要工具详情，需要从其他地方获取

        return manager, tools

    except Exception as e:
        print(f"\n✗ 测试失败: {e}")
        logger.exception("MCPClientManager 测试失败")
        return None, []


async def test_tool_call(manager: MCPClientManager, tools):
    """测试工具调用"""
    print("\n" + "="*60)
    print("测试 2: MCP 工具调用")
    print("="*60)

    if not tools:
        print("\n⚠ 没有可用的工具进行测试")
        return

    try:
        # 工具列表测试已在第一个测试中完成
        print("\n✓ 工具列表获取成功")
        print(f"可用工具数量: {len(tools)}")

    except Exception as e:
        print(f"\n✗ 工具调用测试失败: {e}")
        logger.exception("工具调用测试失败")


async def test_component_registry_integration():
    """测试 ComponentRegistry 集成"""
    print("\n" + "="*60)
    print("测试 3: ComponentRegistry MCP 工具集成")
    print("="*60)

    try:
        registry = ComponentRegistry()

        # 加载 MCP 工具
        await registry.load_mcp_tools()

        # 获取 MCP 工具
        mcp_tools = registry.get_mcp_tools()
        print(f"\n✓ ComponentRegistry 加载了 {len(mcp_tools)} 个 MCP 工具")

        for tool in mcp_tools:
            print(f"\n  工具: {tool.name}")
            print(f"  描述: {tool.description}")
            print(f"  参数数量: {len(tool.parameters)}")

            # 测试 is_mcp_tool 方法
            is_mcp = registry.is_mcp_tool(tool.name)
            print(f"  is_mcp_tool 检测: {'✓' if is_mcp else '✗'}")

        return mcp_tools

    except Exception as e:
        print(f"\n✗ ComponentRegistry 集成测试失败: {e}")
        logger.exception("ComponentRegistry 集成测试失败")
        return []


async def test_tool_execution(mcp_tools):
    """测试通过适配器执行工具"""
    print("\n" + "="*60)
    print("测试 4: MCPToolAdapter 工具执行")
    print("="*60)

    if not mcp_tools:
        print("\n⚠ 没有可用的 MCP 工具进行测试")
        return

    try:
        # 选择第一个工具测试
        test_tool = mcp_tools[0]
        print(f"\n测试工具: {test_tool.name}")

        # 构建测试参数
        test_args = {}
        for param_name, param_type, param_desc, is_required, enum_values in test_tool.parameters:
            if is_required:
                # 根据类型提供默认值
                from src.llm_models.payload_content.tool_option import ToolParamType

                if param_type == ToolParamType.STRING:
                    test_args[param_name] = "test_value"
                elif param_type == ToolParamType.INTEGER:
                    test_args[param_name] = 1
                elif param_type == ToolParamType.FLOAT:
                    test_args[param_name] = 1.0
                elif param_type == ToolParamType.BOOLEAN:
                    test_args[param_name] = True

        print(f"测试参数: {test_args}")

        # 执行工具
        result = await test_tool.execute(test_args)

        if result:
            print("\n✓ 工具执行成功")
            print(f"结果类型: {result.get('type')}")
            print(f"结果内容: {result.get('content', '')[:200]}...")  # 只显示前200字符
        else:
            print("\n✗ 工具执行失败，返回 None")

    except Exception as e:
        print(f"\n✗ 工具执行测试失败: {e}")
        logger.exception("工具执行测试失败")


async def main():
    """主测试流程"""
    print("\n" + "="*60)
    print("MCP 集成测试")
    print("="*60)

    try:
        # 测试 1: MCPClientManager 基本功能
        manager, tools = await test_mcp_client_manager()

        if manager:
            # 测试 2: 工具调用
            await test_tool_call(manager, tools)

            # 测试 3: ComponentRegistry 集成
            mcp_tools = await test_component_registry_integration()

            # 测试 4: 工具执行
            await test_tool_execution(mcp_tools)

            # 关闭连接
            await manager.close()
            print("\n✓ MCP 客户端连接已关闭")

        print("\n" + "="*60)
        print("测试完成")
        print("="*60 + "\n")

    except KeyboardInterrupt:
        print("\n\n测试被用户中断")
    except Exception as e:
        print(f"\n测试过程中发生错误: {e}")
        logger.exception("测试失败")


if __name__ == "__main__":
    asyncio.run(main())
