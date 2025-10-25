import asyncio
import os
import tempfile
from typing import Any
from pathlib import Path
import toml

import whisper

from src.common.logger import get_logger
from src.plugin_system import BasePlugin, ComponentInfo, register_plugin
from src.plugin_system.base.base_tool import BaseTool
from src.plugin_system.base.component_types import ComponentType, ToolInfo

logger = get_logger("stt_whisper_plugin")

# 全局变量来缓存模型，避免重复加载
_whisper_model = None
_is_loading = False

class LocalASRTool(BaseTool):
    """
    本地语音识别工具
    """
    tool_name = "local_asr"
    tool_description = "将本地音频文件路径转换为文字。"
    tool_parameters = [
        {"name": "audio_path", "type": "string", "description": "需要识别的音频文件路径", "required": True}
    ]

    @classmethod
    async def load_model_once(cls, plugin_config: dict):
        """
        一个类方法，用于在插件加载时触发一次模型加载。
        """
        global _whisper_model, _is_loading
        if _whisper_model is None and not _is_loading:
            _is_loading = True
            try:
                model_size = plugin_config.get("whisper", {}).get("model_size", "tiny")
                device = plugin_config.get("whisper", {}).get("device", "cpu")
                logger.info(f"正在预加载 Whisper ASR 模型: {model_size} ({device})")
                
                loop = asyncio.get_running_loop()
                _whisper_model = await loop.run_in_executor(
                    None, whisper.load_model, model_size, device
                )
                logger.info(f"Whisper ASR 模型 '{model_size}' 预加载成功!")
            except Exception as e:
                logger.error(f"预加载 Whisper ASR 模型失败: {e}")
                _whisper_model = None
            finally:
                _is_loading = False

    async def execute(self, function_args: dict) -> str:
        audio_path = function_args.get("audio_path")
        if not audio_path:
            return "错误：缺少 audio_path 参数。"

        global _whisper_model
        # 增强的等待逻辑：只要模型还没准备好，就一直等待后台加载任务完成
        while _is_loading:
            await asyncio.sleep(0.2)
        
        if _whisper_model is None:
            return "Whisper 模型加载失败，无法识别语音。"
        
        try:
            logger.info(f"开始使用 Whisper 识别音频: {audio_path}")
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None, _whisper_model.transcribe, audio_path
            )
            text_result = result.get("text", "")
            text = str(text_result).strip()
            logger.info(f"音频识别成功: {text}")
            return text
        except Exception as e:
            logger.error(f"使用 Whisper 识别音频失败: {e}")
            return f"语音识别出错: {e}"

@register_plugin
class STTWhisperPlugin(BasePlugin):
    plugin_name = "stt_whisper_plugin"
    config_file_name = "config.toml"
    python_dependencies = ["openai-whisper"]

    async def on_plugin_loaded(self):
        """
        插件加载完成后的钩子，用于触发模型预加载。
        """
        try:
            from src.config.config import global_config
            if global_config.voice.asr_provider == "local":
                # 使用 create_task 在后台开始加载，不阻塞主流程
                asyncio.create_task(LocalASRTool.load_model_once(self.config or {}))
        except Exception as e:
            logger.error(f"触发 Whisper 模型预加载时出错: {e}")

    def get_plugin_components(self) -> list[tuple[ComponentInfo, type]]:
        """根据主配置动态注册组件"""
        try:
            from src.config.config import global_config
            if global_config.voice.asr_provider == "local":
                logger.info("ASR provider is 'local', enabling local_asr tool.")
                return [(ToolInfo(
                    name=LocalASRTool.tool_name,
                    description=LocalASRTool.tool_description,
                    component_type=ComponentType.TOOL
                ), LocalASRTool)]
        except Exception as e:
            logger.error(f"检查 ASR provider 配置时出错: {e}")
        
        logger.debug("ASR provider is not 'local', whisper plugin's tool is disabled.")
        return []
