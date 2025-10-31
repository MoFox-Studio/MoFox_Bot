import base64
import time
from abc import ABCMeta, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional, Union

import urllib3
from maim_message import BaseMessageInfo, MessageBase, Seg, UserInfo
from rich.traceback import install

from src.chat.message_receive.chat_stream import ChatStream
from src.chat.utils.self_voice_cache import consume_self_voice_text
from src.chat.utils.utils_image import get_image_manager
from src.chat.utils.utils_video import get_video_analyzer, is_video_analysis_available
from src.chat.utils.utils_voice import get_voice_text
from src.common.data_models.database_data_model import DatabaseMessages
from src.common.logger import get_logger
from src.config.config import global_config

install(extra_lines=3)


logger = get_logger("chat_message")

# 禁用SSL警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 这个类是消息数据类，用于存储和管理消息数据。
# 它定义了消息的属性，包括群组ID、用户ID、消息ID、原始消息内容、纯文本内容和时间戳。
# 它还定义了两个辅助属性：keywords用于提取消息的关键词，is_plain_text用于判断消息是否为纯文本。


@dataclass
class Message(MessageBase, metaclass=ABCMeta):
    chat_stream: Optional["ChatStream"] = None
    reply: Optional["Message"] = None
    processed_plain_text: str = ""
    memorized_times: int = 0

    def __init__(
        self,
        message_id: str,
        chat_stream: "ChatStream",
        user_info: UserInfo,
        message_segment: Seg | None = None,
        timestamp: float | None = None,
        reply: Optional["DatabaseMessages"] = None,
        processed_plain_text: str = "",
    ):
        # 使用传入的时间戳或当前时间
        current_timestamp = timestamp if timestamp is not None else round(time.time(), 3)
        # 构造基础消息信息
        message_info = BaseMessageInfo(
            platform=chat_stream.platform,
            message_id=message_id,
            time=current_timestamp,
            group_info=chat_stream.group_info,
            user_info=user_info,
        )

        # 调用父类初始化
        super().__init__(message_info=message_info, message_segment=message_segment, raw_message=None)  # type: ignore

        self.chat_stream = chat_stream
        # 文本处理相关属性
        self.processed_plain_text = processed_plain_text

        # 回复消息
        self.reply = reply

    async def _process_message_segments(self, segment: Seg) -> str:
        # sourcery skip: remove-unnecessary-else, swap-if-else-branches
        """递归处理消息段，转换为文字描述

        Args:
            segment: 要处理的消息段

        Returns:
            str: 处理后的文本
        """
        if segment.type == "seglist":
            # 处理消息段列表
            segments_text = []
            for seg in segment.data:
                processed = await self._process_message_segments(seg)  # type: ignore
                if processed:
                    segments_text.append(processed)
            return " ".join(segments_text)
        else:
            # 处理单个消息段
            return await self._process_single_segment(segment)  # type: ignore

    @abstractmethod
    async def _process_single_segment(self, segment):
        pass


@dataclass

# MessageRecv 类已被完全移除，现在统一使用 DatabaseMessages
# 如需从消息字典创建 DatabaseMessages，请使用：
# from src.chat.message_receive.message_processor import process_message_from_dict
#
# 迁移完成日期: 2025-10-31


@dataclass
class MessageProcessBase(Message):
    """消息处理基类，用于处理中和发送中的消息"""

    def __init__(
        self,
        message_id: str,
        chat_stream: "ChatStream",
        bot_user_info: UserInfo,
        message_segment: Seg | None = None,
        reply: Optional["DatabaseMessages"] = None,
        thinking_start_time: float = 0,
        timestamp: float | None = None,
    ):
        # 调用父类初始化，传递时间戳
        super().__init__(
            message_id=message_id,
            timestamp=timestamp,
            chat_stream=chat_stream,
            user_info=bot_user_info,
            message_segment=message_segment,
            reply=reply,
        )

        # 处理状态相关属性
        self.thinking_start_time = thinking_start_time
        self.thinking_time = 0

    def update_thinking_time(self) -> float:
        """更新思考时间"""
        self.thinking_time = round(time.time() - self.thinking_start_time, 2)
        return self.thinking_time

    async def _process_single_segment(self, seg: Seg) -> str | None:
        """处理单个消息段

        Args:
            seg: 要处理的消息段

        Returns:
            str: 处理后的文本
        """
        try:
            if seg.type == "text":
                return seg.data  # type: ignore
            elif seg.type == "image":
                # 如果是base64图片数据
                if isinstance(seg.data, str):
                    return await get_image_manager().get_image_description(seg.data)
                return "[图片，网卡了加载不出来]"
            elif seg.type == "emoji":
                if isinstance(seg.data, str):
                    return await get_image_manager().get_emoji_tag(seg.data)
                return "[表情，网卡了加载不出来]"
            elif seg.type == "voice":
                # 检查消息是否由机器人自己发送
                # 检查消息是否由机器人自己发送
                if self.message_info and self.message_info.user_info and str(self.message_info.user_info.user_id) == str(global_config.bot.qq_account):
                    logger.info(f"检测到机器人自身发送的语音消息 (User ID: {self.message_info.user_info.user_id})，尝试从缓存获取文本。")
                    if isinstance(seg.data, str):
                        cached_text = consume_self_voice_text(seg.data)
                        if cached_text:
                            logger.info(f"成功从缓存中获取语音文本: '{cached_text[:70]}...'")
                            return f"[语音：{cached_text}]"
                        else:
                            logger.warning("机器人自身语音消息缓存未命中，将回退到标准语音识别。")

                # 标准语音识别流程 (也作为缓存未命中的后备方案)
                if isinstance(seg.data, str):
                    return await get_voice_text(seg.data)
                return "[发了一段语音，网卡了加载不出来]"
            elif seg.type == "at":
                # 处理at消息，格式为"昵称:QQ号"
                if isinstance(seg.data, str) and ":" in seg.data:
                    nickname, qq_id = seg.data.split(":", 1)
                    return f"@{nickname}"
                return f"@{seg.data}" if isinstance(seg.data, str) else "@未知用户"
            elif seg.type == "reply":
                if self.reply and hasattr(self.reply, "processed_plain_text"):
                    # print(f"self.reply.processed_plain_text: {self.reply.processed_plain_text}")
                    # print(f"reply: {self.reply}")
                    return f"[回复<{self.reply.message_info.user_info.user_nickname}({self.reply.message_info.user_info.user_id})> 的消息：{self.reply.processed_plain_text}]"  # type: ignore
                return None
            else:
                return f"[{seg.type}:{seg.data!s}]"
        except Exception as e:
            logger.error(f"处理消息段失败: {e!s}, 类型: {seg.type}, 数据: {seg.data}")
            return f"[处理失败的{seg.type}消息]"

    def _generate_detailed_text(self) -> str:
        """生成详细文本，包含时间和用户信息"""
        # time_str = time.strftime("%m-%d %H:%M:%S", time.localtime(self.message_info.time))
        timestamp = self.message_info.time
        user_info = self.message_info.user_info

        name = f"<{self.message_info.platform}:{user_info.user_id}:{user_info.user_nickname}:{user_info.user_cardname}>"  # type: ignore
        return f"[{timestamp}]，{name} 说：{self.processed_plain_text}\n"


@dataclass
class MessageSending(MessageProcessBase):
    """发送状态的消息类"""

    def __init__(
        self,
        message_id: str,
        chat_stream: "ChatStream",
        bot_user_info: UserInfo,
        sender_info: UserInfo | None,  # 用来记录发送者信息
        message_segment: Seg,
        display_message: str = "",
        reply: Optional["DatabaseMessages"] = None,
        is_head: bool = False,
        is_emoji: bool = False,
        thinking_start_time: float = 0,
        apply_set_reply_logic: bool = False,
        reply_to: str | None = None,
    ):
        # 调用父类初始化
        super().__init__(
            message_id=message_id,
            chat_stream=chat_stream,
            bot_user_info=bot_user_info,
            message_segment=message_segment,
            reply=reply,
            thinking_start_time=thinking_start_time,
        )

        # 发送状态特有属性
        self.sender_info = sender_info
        # 从 DatabaseMessages 获取 message_id
        if reply:
            self.reply_to_message_id = reply.message_id
        else:
            self.reply_to_message_id = None
        self.is_head = is_head
        self.is_emoji = is_emoji
        self.apply_set_reply_logic = apply_set_reply_logic

        self.reply_to = reply_to

        # 用于显示发送内容与显示不一致的情况
        self.display_message = display_message

        self.interest_value = 0.0

    def build_reply(self):
        """设置回复消息"""
        if self.reply:
            # 从 DatabaseMessages 获取 message_id
            message_id = self.reply.message_id
            
            if message_id:
                self.reply_to_message_id = message_id
                self.message_segment = Seg(
                    type="seglist",
                    data=[
                        Seg(type="reply", data=message_id),  # type: ignore
                        self.message_segment,
                    ],
                )

    async def process(self) -> None:
        """处理消息内容，生成纯文本和详细文本"""
        if self.message_segment:
            self.processed_plain_text = await self._process_message_segments(self.message_segment)

    def to_dict(self):
        ret = super().to_dict()
        if self.chat_stream and self.chat_stream.user_info:
            ret["message_info"]["user_info"] = self.chat_stream.user_info.to_dict()
        return ret

    def is_private_message(self) -> bool:
        """判断是否为私聊消息"""
        return self.message_info.group_info is None or self.message_info.group_info.group_id is None


# message_recv_from_dict 和 message_from_db_dict 函数已被移除
# 请使用: from src.chat.message_receive.message_processor import process_message_from_dict
