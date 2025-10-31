import os
from enum import Enum

import tomlkit

from src.common.logger import get_logger

logger = get_logger("napcat_adapter")


class CommandType(Enum):
    """命令类型"""

    GROUP_BAN = "set_group_ban"  # 禁言用户
    GROUP_WHOLE_BAN = "set_group_whole_ban"  # 群全体禁言
    GROUP_KICK = "set_group_kick"  # 踢出群聊
    SEND_POKE = "send_poke"  # 戳一戳
    DELETE_MSG = "delete_msg"  # 撤回消息
    AI_VOICE_SEND = "send_group_ai_record"  # 发送群AI语音
    SET_EMOJI_LIKE = "set_msg_emoji_like"  # 设置表情回应
    SEND_AT_MESSAGE = "send_at_message"  # 艾特用户并发送消息
    SEND_LIKE = "send_like"  # 点赞

    def __str__(self) -> str:
        return self.value


pyproject_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "pyproject.toml")
toml_data = tomlkit.parse(open(pyproject_path, "r", encoding="utf-8").read())
project_data = toml_data.get("project", {})
version = project_data.get("version", "unknown")
logger.info(f"版本\n\nMoFox-Bot-Napcat-Adapter 版本: {version}\n喜欢的话点个star喵~\n")
