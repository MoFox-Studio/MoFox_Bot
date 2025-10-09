"""
TTS Voice 插件 - 重构版
"""
import toml
from pathlib import Path
from typing import Any, List, Tuple, Type, Dict

from src.common.logger import get_logger
from src.plugin_system import BasePlugin, ComponentInfo, register_plugin
from src.plugin_system.base.component_types import PermissionNodeField
from src.plugin_system.base.config_types import ConfigField

from .actions.tts_action import TTSVoiceAction
from .commands.tts_command import TTSVoiceCommand
from .services.manager import register_service
from .services.tts_service import TTSService

logger = get_logger("tts_voice_plugin")


@register_plugin
class TTSVoicePlugin(BasePlugin):
    """
    GPT-SoVITS 语音合成插件 - 重构版
    """

    plugin_name = "tts_voice_plugin"
    plugin_description = "基于GPT-SoVITS的文本转语音插件（重构版）"
    plugin_version = "3.1.2"
    plugin_author = "Kilo Code & 靚仔"
    config_file_name = "config.toml"
    dependencies = []
    python_dependencies = ["aiohttp"]

    permission_nodes: list[PermissionNodeField] = [
        PermissionNodeField(node_name="command.use", description="是否可以使用 /tts 命令"),
    ]

    config_schema = {}

    config_section_descriptions = {
        "plugin": "插件基本配置",
        "components": "组件启用控制",
        "tts": "TTS语音合成基础配置",
        "tts_advanced": "TTS高级参数配置（语速、采样、批处理等）",
        "tts_styles": "TTS风格参数配置（每个分组为一种风格）"
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tts_service = None

    def _get_config_wrapper(self, key: str, default: Any = None) -> Any:
        """
        配置获取的包装器，用于解决 get_config 无法直接获取动态表（如 tts_styles）和未在 schema 中定义的节的问题。
        由于插件系统的 schema 为空时不会加载未定义的键，这里手动读取配置文件以获取所需配置。
        """
        # 需要手动加载的顶级配置节
        manual_load_keys = ["tts_styles", "spatial_effects", "tts_advanced", "tts"]
        top_key = key.split('.')[0]

        if top_key in manual_load_keys:
            try:
                plugin_file = Path(__file__).resolve()
                bot_root = plugin_file.parent.parent.parent
                config_file = bot_root / "config" / "plugins" / self.plugin_name / self.config_file_name
                
                if not config_file.is_file():
                    logger.error(f"TTS config file not found at robustly constructed path: {config_file}")
                    return default
                
                full_config = toml.loads(config_file.read_text(encoding="utf-8"))

                # 支持点状路径访问
                value = full_config
                for k in key.split('.'):
                    if isinstance(value, dict):
                        value = value.get(k)
                    else:
                        return default
                
                return value if value is not None else default
                
            except Exception as e:
                logger.error(f"Failed to manually load '{key}' from config: {e}", exc_info=True)
                return default
        
        return self.get_config(key, default)

    async def on_plugin_loaded(self):
        """
        插件加载完成后的回调，初始化并注册服务。
        """
        logger.info("初始化 TTSVoicePlugin...")

        # 实例化 TTSService，并传入 get_config 方法
        self.tts_service = TTSService(self._get_config_wrapper)

        # 注册服务
        register_service("tts", self.tts_service)
        logger.info("TTSService 已成功初始化并注册。")

    def get_plugin_components(self) -> List[Tuple[ComponentInfo, Type]]:
        """
        返回插件包含的组件列表。
        """
        components = []
        if self.get_config("components.action_enabled", True):
            components.append((TTSVoiceAction.get_action_info(), TTSVoiceAction))
        if self.get_config("components.command_enabled", True):
            components.append((TTSVoiceCommand.get_plus_command_info(), TTSVoiceCommand))
        return components
