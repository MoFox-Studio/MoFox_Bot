"""
情境提取器
从聊天历史中提取当前的情境（situation），用于 StyleLearner 预测
"""

from src.chat.utils.prompt import Prompt, global_prompt_manager
from src.common.logger import get_logger
from src.config.config import global_config, model_config
from src.llm_models.utils_model import LLMRequest

logger = get_logger("situation_extractor")


def init_prompt():
    situation_extraction_prompt = """
以下是正在进行的聊天内容：
{chat_history}

你的名字是{bot_name}{target_message_info}

请分析当前聊天的情境特征，提取出最能描述当前情境的1-3个关键场景描述。

场景描述应该：
1. 简洁明了（每个不超过20个字）
2. 聚焦情绪、话题、氛围
3. 不涉及具体人名
4. 类似于"表示惊讶"、"讨论游戏"、"表达赞同"这样的格式

请以纯文本格式输出，每行一个场景描述，不要有序号、引号或其他格式：

例如：
表示惊讶和意外
讨论技术问题
表达友好的赞同

现在请提取当前聊天的情境：
"""
    Prompt(situation_extraction_prompt, "situation_extraction_prompt")


class SituationExtractor:
    """情境提取器，从聊天历史中提取当前情境"""

    def __init__(self):
        self.llm_model = LLMRequest(
            model_set=model_config.model_task_config.utils_small,
            request_type="expression.situation_extractor"
        )

    async def extract_situations(
        self,
        chat_history: list | str,
        target_message: str | None = None,
        max_situations: int = 3
    ) -> list[str]:
        """
        从聊天历史中提取情境
        
        Args:
            chat_history: 聊天历史（列表或字符串）
            target_message: 目标消息（可选）
            max_situations: 最多提取的情境数量
            
        Returns:
            情境描述列表
        """
        # 转换chat_history为字符串
        if isinstance(chat_history, list):
            chat_info = "\n".join([
                f"{msg.get('sender', 'Unknown')}: {msg.get('content', '')}"
                for msg in chat_history
            ])
        else:
            chat_info = chat_history

        # 构建目标消息信息
        if target_message:
            target_message_info = f"，现在你想要回复消息：{target_message}"
        else:
            target_message_info = ""

        # 构建 prompt
        try:
            prompt = (await global_prompt_manager.get_prompt_async("situation_extraction_prompt")).format(
                bot_name=global_config.bot.nickname,
                chat_history=chat_info,
                target_message_info=target_message_info
            )

            # 调用 LLM
            response, _ = await self.llm_model.generate_response_async(
                prompt=prompt,
                temperature=0.3
            )

            if not response or not response.strip():
                logger.warning("LLM返回空响应，无法提取情境")
                return []

            # 解析响应
            situations = self._parse_situations(response, max_situations)

            if situations:
                logger.debug(f"提取到 {len(situations)} 个情境: {situations}")
            else:
                logger.warning(f"无法从LLM响应中解析出情境。响应:\n{response}")

            return situations

        except Exception as e:
            logger.error(f"提取情境失败: {e}")
            return []

    @staticmethod
    def _parse_situations(response: str, max_situations: int) -> list[str]:
        """
        解析 LLM 返回的情境描述
        
        Args:
            response: LLM 响应
            max_situations: 最多返回的情境数量
            
        Returns:
            情境描述列表
        """
        situations = []

        for line in response.splitlines():
            line = line.strip()
            if not line:
                continue

            # 移除可能的序号、引号等
            line = line.lstrip('0123456789.、-*>）)】] \t"\'""''')
            line = line.rstrip('"\'""''')
            line = line.strip()

            if not line:
                continue

            # 过滤掉明显不是情境描述的内容
            if len(line) > 30:  # 太长
                continue
            if len(line) < 2:   # 太短
                continue
            if any(keyword in line.lower() for keyword in ["例如", "注意", "请", "分析", "总结"]):
                continue

            situations.append(line)

            if len(situations) >= max_situations:
                break

        return situations


# 初始化 prompt
init_prompt()

# 全局单例
situation_extractor = SituationExtractor()
