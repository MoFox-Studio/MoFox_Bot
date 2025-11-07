"""
记忆系统插件主类
"""

from typing import ClassVar

from src.common.logger import get_logger
from src.plugin_system import BasePlugin, register_plugin

logger = get_logger("memory_graph_plugin")

# 用于存储后台任务引用
_background_tasks = set()


@register_plugin
class MemoryGraphPlugin(BasePlugin):
    """记忆图系统插件"""

    plugin_name = "memory_graph_plugin"
    enable_plugin = True
    dependencies: ClassVar = []
    python_dependencies: ClassVar = []
    config_file_name = "config.toml"
    config_schema: ClassVar = {}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        logger.info(f"{self.log_prefix} 插件已加载")

    def get_plugin_components(self):
        """返回插件组件列表"""
        from src.memory_graph.plugin_tools.memory_plugin_tools import (
            CreateMemoryTool,
            LinkMemoriesTool,
            SearchMemoriesTool,
        )

        components = []

        # 添加工具组件
        for tool_class in [CreateMemoryTool, LinkMemoriesTool, SearchMemoriesTool]:
            tool_info = tool_class.get_tool_info()
            components.append((tool_info, tool_class))

        return components

    async def on_plugin_loaded(self):
        """插件加载后的回调"""
        try:
            from src.memory_graph.manager_singleton import initialize_memory_manager

            logger.info(f"{self.log_prefix} 正在初始化记忆系统...")
            await initialize_memory_manager()
            logger.info(f"{self.log_prefix} ✅ 记忆系统初始化成功")

        except Exception as e:
            logger.error(f"{self.log_prefix} 初始化记忆系统失败: {e}", exc_info=True)
            raise

    def on_unload(self):
        """插件卸载时的回调"""
        try:
            import asyncio

            from src.memory_graph.manager_singleton import shutdown_memory_manager

            logger.info(f"{self.log_prefix} 正在关闭记忆系统...")

            # 在事件循环中运行异步关闭
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # 如果循环正在运行，创建任务
                task = asyncio.create_task(shutdown_memory_manager())
                # 存储引用以防止任务被垃圾回收
                _background_tasks.add(task)
                task.add_done_callback(_background_tasks.discard)
            else:
                # 如果循环未运行，直接运行
                loop.run_until_complete(shutdown_memory_manager())

            logger.info(f"{self.log_prefix} ✅ 记忆系统已关闭")

        except Exception as e:
            logger.error(f"{self.log_prefix} 关闭记忆系统时出错: {e}", exc_info=True)
