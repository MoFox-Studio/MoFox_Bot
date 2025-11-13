"""
插件系统API模块

提供了插件开发所需的各种API
"""

# 导入所有API模块
from src.plugin_system.apis import (
    chat_api,
    component_manage_api,
    config_api,
    database_api,
    emoji_api,
    generator_api,
    llm_api,
    message_api,
    mood_api,
    permission_api,
    person_api,
    plugin_manage_api,
    schedule_api,
    send_api,
    tool_api,
)
from src.plugin_system.apis.chat_api import ChatManager as context_api

from .logging_api import get_logger
from .plugin_register_api import register_plugin

# 导出所有API模块，使它们可以通过 apis.xxx 方式访问
__all__ = [
    "chat_api",
    "component_manage_api",
    "config_api",
    "context_api",
    "database_api",
    "emoji_api",
    "generator_api",
    "get_logger",
    "llm_api",
    "message_api",
    "mood_api",
    "permission_api",
    "person_api",
    "plugin_manage_api",
    "register_plugin",
    "schedule_api",
    "send_api",
    "tool_api",
]
