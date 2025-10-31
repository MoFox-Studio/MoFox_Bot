"""
亲和力聊天处理器插件（包含兴趣计算器功能）
"""

from src.common.logger import get_logger
from src.plugin_system.apis.plugin_register_api import register_plugin
from src.plugin_system.base.base_plugin import BasePlugin
from src.plugin_system.base.component_types import ComponentInfo

logger = get_logger("affinity_chatter_plugin")


@register_plugin
class AffinityChatterPlugin(BasePlugin):
    """亲和力聊天处理器插件

    - 延迟导入 `AffinityChatter` 并通过组件注册器注册为聊天处理器
    - 延迟导入 `AffinityInterestCalculator` 并通过组件注册器注册为兴趣计算器
    - 提供 `get_plugin_components` 以兼容插件注册机制
    """

    plugin_name: str = "affinity_chatter"
    enable_plugin: bool = True
    dependencies: list[str] = []
    python_dependencies: list[str] = []
    config_file_name: str = ""

    # 简单的 config_schema 占位（如果将来需要配置可扩展）
    config_schema = {}

    def get_plugin_components(self) -> list[tuple[ComponentInfo, type]]:
        """返回插件包含的组件列表

        这里采用延迟导入以避免循环依赖和启动顺序问题。
        如果导入失败则返回空列表以让注册过程继续而不崩溃。
        """
        components = []

        try:
            # 延迟导入 AffinityChatter
            from .affinity_chatter import AffinityChatter

            components.append((AffinityChatter.get_chatter_info(), AffinityChatter))
        except Exception as e:
            logger.error(f"加载 AffinityChatter 时出错: {e}")

        try:
            # 延迟导入 AffinityInterestCalculator
            from .affinity_interest_calculator import AffinityInterestCalculator

            components.append((AffinityInterestCalculator.get_interest_calculator_info(), AffinityInterestCalculator))
        except Exception as e:
            logger.error(f"加载 AffinityInterestCalculator 时出错: {e}")

        try:
            # 延迟导入 UserProfileTool
            from .user_profile_tool import UserProfileTool

            components.append((UserProfileTool.get_tool_info(), UserProfileTool))
        except Exception as e:
            logger.error(f"加载 UserProfileTool 时出错: {e}")

        try:
            # 延迟导入 ChatStreamImpressionTool
            from .chat_stream_impression_tool import ChatStreamImpressionTool

            components.append((ChatStreamImpressionTool.get_tool_info(), ChatStreamImpressionTool))
        except Exception as e:
            logger.error(f"加载 ChatStreamImpressionTool 时出错: {e}")

        try:
            # 延迟导入 ProactiveThinkingReplyHandler
            from .proactive_thinking_event import ProactiveThinkingReplyHandler

            components.append((ProactiveThinkingReplyHandler.get_handler_info(), ProactiveThinkingReplyHandler))
        except Exception as e:
            logger.error(f"加载 ProactiveThinkingReplyHandler 时出错: {e}")

        try:
            # 延迟导入 ProactiveThinkingMessageHandler
            from .proactive_thinking_event import ProactiveThinkingMessageHandler

            components.append((ProactiveThinkingMessageHandler.get_handler_info(), ProactiveThinkingMessageHandler))
        except Exception as e:
            logger.error(f"加载 ProactiveThinkingMessageHandler 时出错: {e}")

        return components
