"""
测试记忆系统插件集成

验证：
1. 插件能否正常加载
2. 工具能否被识别为 LLM 可用工具
3. 工具能否正常执行
"""

import asyncio
import sys
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


async def test_plugin_integration():
    """测试插件集成"""
    print("=" * 60)
    print("测试记忆系统插件集成")
    print("=" * 60)
    print()

    # 1. 测试导入插件工具
    print("[1] 测试导入插件工具...")
    try:
        from src.memory_graph.plugin_tools.memory_plugin_tools import (
            CreateMemoryTool,
            LinkMemoriesTool,
            SearchMemoriesTool,
        )

        print(f"  ✅ CreateMemoryTool: {CreateMemoryTool.name}")
        print(f"  ✅ LinkMemoriesTool: {LinkMemoriesTool.name}")
        print(f"  ✅ SearchMemoriesTool: {SearchMemoriesTool.name}")
    except Exception as e:
        print(f"  ❌ 导入失败: {e}")
        return False

    # 2. 测试工具定义
    print("\n[2] 测试工具定义...")
    try:
        create_def = CreateMemoryTool.get_tool_definition()
        link_def = LinkMemoriesTool.get_tool_definition()
        search_def = SearchMemoriesTool.get_tool_definition()

        print(f"  ✅ create_memory: {len(create_def['parameters'])} 个参数")
        print(f"  ✅ link_memories: {len(link_def['parameters'])} 个参数")
        print(f"  ✅ search_memories: {len(search_def['parameters'])} 个参数")
    except Exception as e:
        print(f"  ❌ 获取工具定义失败: {e}")
        return False

    # 3. 测试初始化 MemoryManager
    print("\n[3] 测试初始化 MemoryManager...")
    try:
        from src.memory_graph.manager_singleton import (
            get_memory_manager,
            initialize_memory_manager,
        )

        # 初始化
        manager = await initialize_memory_manager(data_dir="data/test_plugin_integration")
        print(f"  ✅ MemoryManager 初始化成功")

        # 获取单例
        manager2 = get_memory_manager()
        assert manager is manager2, "单例模式失败"
        print(f"  ✅ 单例模式正常")

    except Exception as e:
        print(f"  ❌ 初始化失败: {e}")
        import traceback

        traceback.print_exc()
        return False

    # 4. 测试工具执行
    print("\n[4] 测试工具执行...")
    try:
        # 创建记忆
        create_tool = CreateMemoryTool()
        result = await create_tool.execute(
            {
                "subject": "我",
                "memory_type": "事件",
                "topic": "测试记忆系统插件",
                "attributes": {"时间": "今天"},
                "importance": 0.8,
            }
        )
        print(f"  ✅ create_memory: {result['content']}")

        # 搜索记忆
        search_tool = SearchMemoriesTool()
        result = await search_tool.execute({"query": "测试", "top_k": 5})
        print(f"  ✅ search_memories: 找到记忆")

    except Exception as e:
        print(f"  ❌ 工具执行失败: {e}")
        import traceback

        traceback.print_exc()
        return False

    # 5. 测试关闭
    print("\n[5] 测试关闭...")
    try:
        from src.memory_graph.manager_singleton import shutdown_memory_manager

        await shutdown_memory_manager()
        print(f"  ✅ MemoryManager 关闭成功")
    except Exception as e:
        print(f"  ❌ 关闭失败: {e}")
        return False

    print("\n" + "=" * 60)
    print("[SUCCESS] 所有测试通过！")
    print("=" * 60)
    return True


if __name__ == "__main__":
    result = asyncio.run(test_plugin_integration())
    sys.exit(0 if result else 1)
