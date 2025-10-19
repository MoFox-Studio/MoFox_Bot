from src.common.logger import get_logger
from src.plugin_system import (
    BaseEventHandler,
    BasePlugin,
    ConfigField,
    EventHandlerInfo,
    register_plugin,
)
from src.plugin_system.base.base_plugin import BasePlugin

from .proacive_thinker_event import ProactiveThinkerEventHandler

logger = get_logger(__name__)


@register_plugin
class ProactiveThinkerPlugin(BasePlugin):
    """一个主动思考的插件"""

    plugin_name: str = "proactive_thinker"
    enable_plugin: bool = True
    dependencies: list[str] = []
    python_dependencies: list[str] = []
    config_file_name: str = "config.toml"
    config_schema: dict = {
        "plugin": {
            "config_version": ConfigField(type=str, default="1.1.0", description="配置文件版本"),
        },
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def get_plugin_components(self) -> list[tuple[EventHandlerInfo, type[BaseEventHandler]]]:
        """返回插件的EventHandler组件"""
        components: list[tuple[EventHandlerInfo, type[BaseEventHandler]]] = [
            (ProactiveThinkerEventHandler.get_handler_info(), ProactiveThinkerEventHandler)
        ]
        return components
