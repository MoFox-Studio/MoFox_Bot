from rich.traceback import install

from src.common.logger import get_logger
from src.config.config import global_config, model_config
from src.llm_models.utils_model import LLMRequest

install(extra_lines=3)

logger = get_logger("chat_voice")


async def get_voice_text(voice_base64: str) -> str:
    """获取音频文件转录文本"""
    if not global_config.voice.enable_asr:
        logger.warning("语音识别未启用，无法处理语音消息")
        return "[语音]"

    asr_provider = global_config.voice.asr_provider

    # 如果选择本地识别
    if asr_provider == "local":
        from src.plugin_system.apis import tool_api
        import tempfile
        import base64
        import os

        local_asr_tool = tool_api.get_tool_instance("local_asr")
        if not local_asr_tool:
            logger.error("ASR provider 设置为 'local' 但未找到 'local_asr' 工具，请检查 stt_whisper_plugin 是否已加载。")
            return "[语音(本地识别工具未找到)]"

        audio_path = None
        try:
            audio_data = base64.b64decode(voice_base64)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".amr") as tmp_audio_file:
                tmp_audio_file.write(audio_data)
                audio_path = tmp_audio_file.name

            text = await local_asr_tool.execute(function_args={"audio_path": audio_path})
            if "失败" in text or "出错" in text or "错误" in text:
                logger.warning(f"本地语音识别失败: {text}")
                return f"[语音(本地识别失败)]"
            
            logger.info(f"本地语音识别成功: {text}")
            return f"[语音] {text}"

        except Exception as e:
            logger.error(f"本地语音转文字失败: {e!s}")
            return "[语音(本地识别出错)]"
        finally:
            if audio_path and os.path.exists(audio_path):
                try:
                    os.remove(audio_path)
                except Exception as e:
                    logger.error(f"清理临时音频文件失败: {e}")

    # 默认使用 API 识别
    try:
        _llm = LLMRequest(model_set=model_config.model_task_config.voice, request_type="audio")
        text = await _llm.generate_response_for_voice(voice_base64)
        if text is None:
            logger.warning("未能生成语音文本")
            return "[语音(文本生成失败)]"

        logger.debug(f"描述是{text}")

        return f"[语音：{text}]"
    except Exception as e:
        logger.error(f"语音转文字失败: {e!s}")
        return "[语音]"
