from typing import Any
from src.common.logger import get_logger
from src.plugin_system.base.base_tool import BaseTool
from src.plugin_system.base.component_types import ComponentType

logger = get_logger("tool_api")


def get_tool_instance(tool_name: str) -> BaseTool | None:
    """获取公开工具实例"""
    from src.plugin_system.core import component_registry

    # 获取插件配置
    tool_info = component_registry.get_component_info(tool_name, ComponentType.TOOL)
    if tool_info:
        plugin_config = component_registry.get_plugin_config(tool_info.plugin_name)
    else:
        plugin_config = None

    tool_class: type[BaseTool] = component_registry.get_component_class(tool_name, ComponentType.TOOL)  # type: ignore
    return tool_class(plugin_config) if tool_class else None


def get_llm_available_tool_definitions() -> list[dict[str, Any]]:
    """获取LLM可用的工具定义列表

    Returns:
        list[dict[str, Any]]: 工具定义列表
    """
    from src.plugin_system.core import component_registry

    llm_available_tools = component_registry.get_llm_available_tools()
    tool_definitions = []
    for tool_name, tool_class in llm_available_tools.items():
        try:
            # 调用类方法 get_tool_definition 获取定义
            definition = tool_class.get_tool_definition()
            tool_definitions.append(definition)
        except Exception as e:
            logger.error(f"获取工具 {tool_name} 的定义失败: {e}")
    return tool_definitions

