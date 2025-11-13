from typing import Any

from src.common.logger import get_logger
from src.plugin_system.base.base_tool import BaseTool
from src.plugin_system.base.component_types import ComponentType

logger = get_logger("tool_api")


def get_tool_instance(tool_name: str, chat_stream: Any = None) -> BaseTool | None:
    """获取公开工具实例

    Args:
        tool_name: 工具名称
        chat_stream: 聊天流对象，用于提供上下文信息

    Returns:
        BaseTool: 工具实例，如果工具不存在则返回None
    """
    from src.plugin_system.core import component_registry

    # 获取插件配置
    tool_info = component_registry.get_component_info(tool_name, ComponentType.TOOL)
    if tool_info:
        plugin_config = component_registry.get_plugin_config(tool_info.plugin_name)
    else:
        plugin_config = None

    tool_class: type[BaseTool] = component_registry.get_component_class(tool_name, ComponentType.TOOL)  # type: ignore
    return tool_class(plugin_config, chat_stream) if tool_class else None


def get_llm_available_tool_definitions() -> list[dict[str, Any]]:
    """获取LLM可用的工具定义列表（包括 MCP 工具）

    Returns:
        list[dict[str, Any]]: 工具定义列表
    """
    from src.plugin_system.core import component_registry

    llm_available_tools = component_registry.get_llm_available_tools()
    tool_definitions = []

    # 获取常规工具定义
    for tool_name, tool_class in llm_available_tools.items():
        try:
            # 调用类方法 get_tool_definition 获取定义
            definition = tool_class.get_tool_definition()
            tool_definitions.append(definition)
        except Exception as e:
            logger.error(f"获取工具 {tool_name} 的定义失败: {e}")

    # 获取 MCP 工具定义
    try:
        mcp_tools = component_registry.get_mcp_tools()
        for mcp_tool in mcp_tools:
            try:
                definition = mcp_tool.get_tool_definition()
                tool_definitions.append(definition)
            except Exception as e:
                logger.error(f"获取 MCP 工具 {mcp_tool.name} 的定义失败: {e}")
    except Exception as e:
        logger.debug(f"获取 MCP 工具列表失败（可能未启用）: {e}")

    return tool_definitions

