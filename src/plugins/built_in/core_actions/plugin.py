"""
核心动作插件

将系统核心动作（reply、no_reply、emoji）转换为新插件系统格式
这是系统的内置插件，提供基础的聊天交互功能
"""

# 导入依赖的系统组件
from typing import ClassVar

from src.common.logger import get_logger

# 导入新插件系统
from src.plugin_system import BasePlugin, ComponentInfo, register_plugin
from src.plugin_system.base.config_types import ConfigField

# 导入API模块 - 标准Python包方式
from src.plugins.built_in.core_actions.emoji import EmojiAction
from src.plugins.built_in.core_actions.reply import ReplyAction, RespondAction

logger = get_logger("core_actions")


@register_plugin
class CoreActionsPlugin(BasePlugin):
    """核心动作插件

    系统内置插件，提供基础的聊天交互功能：
    - Reply: 回复动作
    - NoReply: 不回复动作
    - Emoji: 表情动作

    注意：插件基本信息优先从_manifest.json文件中读取
    """

    # 插件基本信息
    plugin_name: str = "core_actions"  # 内部标识符
    enable_plugin: bool = True
    dependencies: ClassVar[list[str]] = []  # 插件依赖列表
    python_dependencies: ClassVar[list[str]] = []  # Python包依赖列表
    config_file_name: str = "config.toml"

    # 配置节描述
    config_section_descriptions: ClassVar = {
        "plugin": "插件启用配置",
        "components": "核心组件启用配置",
    }

    # 配置Schema定义
    config_schema: ClassVar[dict] = {
        "plugin": {
            "enabled": ConfigField(type=bool, default=True, description="是否启用插件"),
            "config_version": ConfigField(type=str, default="0.6.0", description="配置文件版本"),
        },
        "components": {
            "enable_reply": ConfigField(type=bool, default=True, description="是否启用 reply 动作（s4u模板）"),
            "enable_respond": ConfigField(type=bool, default=True, description="是否启用 respond 动作（normal模板）"),
            "enable_emoji": ConfigField(type=bool, default=True, description="是否启用发送表情/图片动作"),
        },
    }

    def get_plugin_components(self) -> list[tuple[ComponentInfo, type]]:
        """返回插件包含的组件列表"""

        # --- 根据配置注册组件 ---
        components: ClassVar = []

        # 注册 reply 动作
        if self.get_config("components.enable_reply", True):
            components.append((ReplyAction.get_action_info(), ReplyAction))

        # 注册 respond 动作
        if self.get_config("components.enable_respond", True):
            components.append((RespondAction.get_action_info(), RespondAction))

        # 注册 emoji 动作
        if self.get_config("components.enable_emoji", True):
            components.append((EmojiAction.get_action_info(), EmojiAction))

        return components
