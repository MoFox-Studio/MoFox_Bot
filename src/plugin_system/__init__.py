"""
MoFox-Bot 插件系统

提供统一的插件开发和管理框架
"""

# 导出主要的公共接口
from .apis import (
    chat_api,
    component_manage_api,
    config_api,
    database_api,
    emoji_api,
    generator_api,
    get_logger,
    llm_api,
    message_api,
    mood_api,
    person_api,
    plugin_manage_api,
    register_plugin,
    send_api,
    tool_api,
)
from .base import (
    ActionActivationType,
    ActionInfo,
    BaseAction,
    BaseCommand,
    BaseEventHandler,
    BasePlugin,
    BasePrompt,
    BaseTool,
    ChatMode,
    ChatType,
    CommandArgs,
    CommandInfo,
    ComponentInfo,
    ComponentType,
    ConfigField,
    EventHandlerInfo,
    EventType,
    MaiMessages,
    PluginInfo,
    # 新增的增强命令系统
    PlusCommand,
    BaseRouterComponent,
    PythonDependency,
    ToolInfo,
    ToolParamType,
    create_plus_command_adapter,
)
from .utils.dependency_config import configure_dependency_settings, get_dependency_config

# 导入依赖管理模块
from .utils.dependency_manager import configure_dependency_manager, get_dependency_manager

__version__ = "2.0.0"

__all__ = [  # noqa: RUF022
    "ActionActivationType",
    "ActionInfo",
    "BaseAction",
    "BaseCommand",
    "BaseEventHandler",
    # 基础类
    "BasePlugin",
    "BasePrompt",
    "BaseTool",
    "ChatMode",
    "ChatType",
    "CommandArgs",
    "CommandInfo",
    "ComponentInfo",
    # 类型定义
    "ComponentType",
    "ConfigField",
    "EventHandlerInfo",
    "EventType",
    # 消息
    "MaiMessages",
    # 工具函数
    "PluginInfo",
    # 增强命令系统
    "PlusCommand",
    "BaseRouterComponent"
    "PythonDependency",
    "ToolInfo",
    "ToolParamType",
    "chat_api",
    "component_manage_api",
    "config_api",
    "configure_dependency_manager",
    "configure_dependency_settings",
    "create_plus_command_adapter",
    "create_plus_command_adapter",
    "database_api",
    "emoji_api",
    "generator_api",
    "get_dependency_config",
    # 依赖管理
    "get_dependency_manager",
    "get_logger",
    "get_logger",
    "llm_api",
    "message_api",
    # API 模块
    "mood_api",
    "person_api",
    "plugin_manage_api",
    "register_plugin",
    # 装饰器
    "register_plugin",
    "send_api",
    "tool_api",
    # "ManifestGenerator",
    # "validate_plugin_manifest",
    # "generate_plugin_manifest",
] # type: ignore
