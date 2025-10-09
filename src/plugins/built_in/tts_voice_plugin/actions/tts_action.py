"""
TTS 语音合成 Action
"""
import asyncio
from typing import Tuple

from src.common.logger import get_logger
from src.plugin_system.apis import config_api, generator_api
from src.plugin_system.base.base_action import ActionActivationType, BaseAction, ChatMode

from ..services.manager import get_service

logger = get_logger("tts_voice_plugin.action")


class TTSVoiceAction(BaseAction):
    """
    通过关键词或规划器自动触发 TTS 语音合成
    """

    action_name = "tts_voice_action"
    action_description = "使用GPT-SoVITS将文本转换为语音并发送"

    focus_activation_type = ActionActivationType.KEYWORD
    normal_activation_type = ActionActivationType.KEYWORD
    activation_keywords = [
        "发语音", "语音", "说句话", "用语音说", "听你", "听声音", "想你", "想听声音",
        "讲个话", "说段话", "念一下", "读一下", "用嘴说", "说", "能发语音吗","亲口"
    ]
    mode_enable = ChatMode.ALL
    parallel_action = False

    action_require = [
        "当用户明确请求使用语音进行回复时，例如‘发个语音听听’、‘用语音说’等。",
        "当对话内容适合用语音表达，例如讲故事、念诗、撒娇或进行角色扮演时。",
        "在表达特殊情感（如安慰、鼓励、庆祝）的场景下，可以主动使用语音来增强感染力。",
        "不要在日常的、简短的问答或闲聊中频繁使用语音，避免打扰用户。",
        "文本内容必须是纯粹的对话，不能包含任何括号或方括号括起来的动作、表情、或场景描述（例如，不要出现 '(笑)' 或 '[歪头]'）",
        "必须使用标准、完整的标点符号（如逗号、句号、问号）来进行自然的断句，以确保语音停顿自然，避免生成一长串没有停顿的文本。"
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 关键配置项现在由 TTSService 管理
        self.tts_service = get_service("tts")

    async def execute(self) -> Tuple[bool, str]:
        """
        执行 Action 的核心逻辑
        """
        try:
            if not self.tts_service:
                raise RuntimeError("TTSService 未注册或初始化失败")

            initial_text = self.action_data.get("text", "").strip()
            voice_style = self.action_data.get("voice_style", "default")
            logger.info(f"{self.log_prefix} 接收到规划器的初步文本: '{initial_text[:70]}...'")

            # 1. 请求主回复模型生成高质量文本
            text = await self._generate_final_text(initial_text)
            if not text:
                await self.send_text("❌ 语音合成出错：最终生成的文本为空。")
                return False, "最终生成的文本为空"

            # 2. 调用 TTSService 生成语音
            audio_b64 = await self.tts_service.generate_voice(text, voice_style)

            if audio_b64:
                await self.send_custom(message_type="voice", content=audio_b64)
                logger.info(f"{self.log_prefix} GPT-SoVITS语音发送成功")
                await self.store_action_info(
                    action_prompt_display=f"将文本转换为语音并发送 (风格:{voice_style})",
                    action_done=True
                )
                return True, f"成功生成并发送语音，文本长度: {len(text)}字符"
            else:
                await self._handle_error_and_reply("tts_api_error", Exception("TTS服务未能返回音频数据"))
                return False, "语音合成失败"

        except Exception as e:
            await self._handle_error_and_reply("generic_error", e)
            return False, f"语音合成出错: {str(e)}"

    async def _generate_final_text(self, initial_text: str) -> str:
        """请求主回复模型生成或优化文本"""
        try:
            generation_reason = (
                "这是一个为语音消息（TTS）生成文本的特殊任务。"
                "请基于规划器提供的初步文本，结合对话历史和自己的人设，将它优化成一句自然、富有感情、适合用语音说出的话。"
                "最终指令：请务-必确保文本听起来像真实的、自然的口语对话，而不是书面语。"
            )
            
            logger.info(f"{self.log_prefix} 请求主回复模型(replyer)全新生成TTS文本...")
            success, response_set, _ = await generator_api.rewrite_reply(
                chat_stream=self.chat_stream,
                reply_data={"raw_reply": initial_text, "reason": generation_reason},
                request_type="replyer"
            )

            if success and response_set:
                text = "".join(str(seg[1]) if isinstance(seg, tuple) else str(seg) for seg in response_set).strip()
                logger.info(f"{self.log_prefix} 成功生成高质量TTS文本: {text}")
                return text
            
            if initial_text:
                logger.warning(f"{self.log_prefix} 主模型生成失败，使用规划器原始文本作为兜底。")
                return initial_text
            
            raise Exception("主模型未能生成回复，且规划器也未提供兜底文本。")

        except Exception as e:
            logger.error(f"{self.log_prefix} 生成高质量回复内容时失败: {e}", exc_info=True)
            return ""

    async def _handle_error_and_reply(self, error_context: str, exception: Exception):
        """处理错误并生成一个动态的、拟人化的回复"""
        logger.error(f"{self.log_prefix} 在 {error_context} 阶段出错: {exception}", exc_info=True)

        error_prompts = {
            "generic_error": {
                "raw_reply": "糟糕，我的思路好像缠成一团毛线球了，需要一点时间来解开...你能耐心等我一下吗？",
                "reason": f"客观原因：插件在执行时发生了未知异常。详细信息: {str(exception)}"
            },
            "tts_api_error": {
                "raw_reply": "我的麦克风好像有点小情绪，突然不想工作了...我正在哄它呢，请稍等片刻哦！",
                "reason": f"客观原因：语音合成服务返回了一个错误。详细信息: {str(exception)}"
            }
        }
        prompt_data = error_prompts.get(error_context, error_prompts["generic_error"])

        try:
            success, result_message, _ = await generator_api.rewrite_reply(
                chat_stream=self.chat_stream,
                raw_reply=prompt_data["raw_reply"],
                reason=prompt_data["reason"]
            )
            if success and result_message:
                message_text = "".join(str(seg[1]) if isinstance(seg, tuple) else str(seg) for seg in result_message).strip()
                await self.send_text(message_text)
            else:
                await self.send_text("哎呀，好像出了一点小问题，我稍后再试试吧~")
        except Exception as gen_e:
            logger.error(f"生成动态错误回复时也出错了: {gen_e}", exc_info=True)
            await self.send_text("唔...我的思路好像卡壳了，请稍等一下哦！")

        await self.store_action_info(
            action_prompt_display=f"语音合成失败: {str(exception)}",
            action_done=False
        )
