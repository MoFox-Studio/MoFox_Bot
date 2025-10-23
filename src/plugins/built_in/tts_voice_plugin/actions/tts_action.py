"""
TTS 语音合成 Action
"""

from src.common.logger import get_logger
from src.plugin_system.apis import generator_api
from src.plugin_system.base.base_action import ActionActivationType, BaseAction, ChatMode

from ..services.manager import get_service

logger = get_logger("tts_voice_plugin.action")


class TTSVoiceAction(BaseAction):
    """
    通过关键词或规划器自动触发 TTS 语音合成
    """

    action_name = "tts_voice_action"
    action_description = "使用GPT-SoVITS将文本转换为语音并发送"

    mode_enable = ChatMode.ALL
    parallel_action = False

    action_require = [
        "当用户明确请求使用语音进行回复时，例如‘发个语音听听’、‘用语音说’等。",
        "当对话内容适合用语音表达，例如讲故事、念诗、撒嬌或进行角色扮演时。",
        "在表达特殊情感（如安慰、鼓励、庆祝）的场景下，可以主动使用语音来增强感染力。",
        "不要在日常的、简短的问答或闲聊中频繁使用语音，避免打扰用户。",
        "文本内容必须是纯粹的对话，不能包含任何括号或方括号括起来的动作、表情、或场景描述（例如，不要出现 '(笑)' 或 '[歪头]'）",
        "必须使用标准、完整的标点符号（如逗号、句号、问号）来进行自然的断句，以确保语音停顿自然，避免生成一长串没有停顿的文本。"
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 关键配置项现在由 TTSService 管理
        self.tts_service = get_service("tts")

    async def go_activate(self, llm_judge_model=None) -> bool:
        """
        判断此 Action 是否应该被激活。
        满足以下任一条件即可激活：
        1. 55% 的随机概率
        2. 匹配到预设的关键词
        3. LLM 判断当前场景适合发送语音
        """
        # 条件1: 随机激活
        if await self._random_activation(0.25):
            logger.info(f"{self.log_prefix} 随机激活成功 (25%)")
            return True

        # 条件2: 关键词激活
        keywords = [
            "发语音", "语音", "说句话", "用语音说", "听你", "听声音", "想你", "想听声音",
            "讲个话", "说段话", "念一下", "读一下", "用嘴说", "说", "能发语音吗", "亲口"
        ]
        if await self._keyword_match(keywords):
            logger.info(f"{self.log_prefix} 关键词激活成功")
            return True

        # 条件3: LLM 判断激活
        # 注意：这里我们复用 action_require 里的描述，让 LLM 的判断更精准
        if await self._llm_judge_activation(
            llm_judge_model=llm_judge_model
        ):
            logger.info(f"{self.log_prefix} LLM 判断激活成功")
            return True
            
        logger.debug(f"{self.log_prefix} 所有激活条件均未满足，不激活")
        return False

    async def execute(self) -> tuple[bool, str]:
        """
        执行 Action 的核心逻辑
        """
        try:
            if not self.tts_service:
                logger.error(f"{self.log_prefix} TTSService 未注册或初始化失败，静默处理。")
                return False, "TTSService 未注册或初始化失败"

            initial_text = self.action_data.get("text", "").strip()
            voice_style = self.action_data.get("voice_style", "default")
            logger.info(f"{self.log_prefix} 接收到规划器的初步文本: '{initial_text[:70]}...'")

            # 1. 请求主回复模型生成高质量文本
            text = await self._generate_final_text(initial_text)
            if not text:
                logger.warning(f"{self.log_prefix} 最终生成的文本为空，静默处理。")
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
                logger.error(f"{self.log_prefix} TTS服务未能返回音频数据，静默处理。")
                await self.store_action_info(
                    action_prompt_display="语音合成失败: TTS服务未能返回音频数据",
                    action_done=False
                )
                return False, "语音合成失败"

        except Exception as e:
            logger.error(f"{self.log_prefix} 语音合成过程中发生未知错误: {e!s}", exc_info=True)
            await self.store_action_info(
                action_prompt_display=f"语音合成失败: {e!s}",
                action_done=False
            )
            return False, f"语音合成出错: {e!s}"

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
