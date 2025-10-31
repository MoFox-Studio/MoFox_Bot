"""
消息内容处理模块

负责消息内容的提取、清理和预处理
"""

import re

from src.common.data_models.database_data_model import DatabaseMessages
from src.common.logger import get_logger

logger = get_logger("anti_injector.message_processor")


class MessageProcessor:
    """消息内容处理器"""

    def extract_text_content(self, message: DatabaseMessages) -> str:
        """提取消息中的文本内容，过滤掉引用的历史内容

        Args:
            message: 接收到的消息对象

        Returns:
            提取的文本内容
        """
        # 主要检测处理后的纯文本
        processed_text = message.processed_plain_text
        logger.debug(f"原始processed_plain_text: '{processed_text}'")

        # 检查是否包含引用消息，提取用户新增内容
        new_content = self.extract_new_content_from_reply(processed_text)
        logger.debug(f"提取的新内容: '{new_content}'")

        # 只返回用户新增的内容，避免重复
        return new_content

    @staticmethod
    def extract_new_content_from_reply(full_text: str) -> str:
        """从包含引用的完整消息中提取用户新增的内容

        Args:
            full_text: 完整的消息文本

        Returns:
            用户新增的内容（去除引用部分）
        """
        # 引用消息的格式：[回复<用户昵称:用户ID> 的消息：引用的消息内容]
        # 使用正则表达式匹配引用部分
        reply_pattern = r"\[回复<[^>]*> 的消息：[^\]]*\]"

        # 移除所有引用部分
        new_content = re.sub(reply_pattern, "", full_text).strip()

        # 如果移除引用后内容为空，说明这是一个纯引用消息，返回一个标识
        if not new_content:
            logger.debug("检测到纯引用消息，无用户新增内容")
            return "[纯引用消息]"

        # 记录处理结果
        if new_content != full_text:
            logger.debug(f"从引用消息中提取新内容: '{new_content}' (原始: '{full_text}')")

        return new_content

    @staticmethod
    def check_whitelist(message: DatabaseMessages, whitelist: list) -> tuple | None:
        """检查用户白名单

        Args:
            message: 消息对象
            whitelist: 白名单配置

        Returns:
            如果在白名单中返回结果元组，否则返回None
        """
        user_id = message.user_info.user_id
        platform = message.chat_info.platform

        # 检查用户白名单：格式为 [[platform, user_id], ...]
        for whitelist_entry in whitelist:
            if len(whitelist_entry) == 2 and whitelist_entry[0] == platform and whitelist_entry[1] == user_id:
                logger.debug(f"用户 {platform}:{user_id} 在白名单中，跳过检测")
                return True, None, "用户白名单"

        return None

    @staticmethod
    def check_whitelist_dict(user_id: str, platform: str, whitelist: list) -> bool:
        """检查用户是否在白名单中（字典格式）

        Args:
            user_id: 用户ID
            platform: 平台
            whitelist: 白名单配置

        Returns:
            如果在白名单中返回True，否则返回False
        """
        if not whitelist or not user_id or not platform:
            return False

        # 检查用户白名单：格式为 [[platform, user_id], ...]
        for whitelist_entry in whitelist:
            if len(whitelist_entry) == 2 and whitelist_entry[0] == platform and whitelist_entry[1] == user_id:
                logger.debug(f"用户 {platform}:{user_id} 在白名单中，跳过检测")
                return True

        return False

    def extract_text_content_from_dict(self, message_data: dict) -> str:
        """从字典格式消息中提取文本内容

        Args:
            message_data: 消息数据字典

        Returns:
            提取的文本内容
        """
        processed_plain_text = message_data.get("processed_plain_text", "")
        return self.extract_new_content_from_reply(processed_plain_text)
