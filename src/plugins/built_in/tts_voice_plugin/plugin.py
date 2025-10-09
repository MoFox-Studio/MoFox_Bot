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
        # 新增配置缓存
        self._config_cache = None

    def _get_config_wrapper(self, key: str, default: Any = None) -> Any:
        """
        配置获取的包装器，用于解决 get_config 无法直接获取动态表（如 tts_styles）和未在 schema 中定义的节的问题。
        由于插件系统的 schema 为空时不会加载未定义的键，这里手动读取配置文件以获取所需配置。
        """
        # 需要手动加载的顶级配置节（移除未定义的 spatial_effects）
        manual_load_keys = ["tts_styles", "tts_advanced", "tts"]
        top_key = key.split('.')[0]

        # 仅当需要手动加载且缓存为空时加载配置
        if top_key in manual_load_keys and self._config_cache is None:
            try:
                # 优化：使用标准插件路径
                config_path = Path(__file__).parent / "config" / self.config_file_name
                
                if not config_path.exists():
                    logger.error(f"TTS config file not found at {config_path}")
                    self._config_cache = {}
                else:
                    # 使用 toml.load 代替 toml.loads 以提高性能
                    self._config_cache = toml.load(config_path)
            except toml.TomlDecodeError as e:
                logger.error(f"Invalid TOML syntax in config: {e}")
                self._config_cache = {}
            except FileNotFoundError:
                logger.error(f"Config file missing: {config_path}")
                self._config_cache = {}
            except Exception as e:
                logger.error(f"Unexpected config error: {e}", exc_info=True)
                self._config_cache = {}

        # 从缓存中获取配置
        if top_key in manual_load_keys:
            value = self._config_cache
            for k in key.split('.'):
                if isinstance(value, dict):
                    value = value.get(k)
                else:
                    return default
            return value if value is not None else default
        
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
            components.appendTTSVoiceAction.get_action_info(), TTSVoiceAction))
        if self.get_config("components.command_enabled", True):
            components.append((TTSVoiceCommand.get_plus_command_info(), TTSVoiceCommand))
        return components
