"""
消息管理模块数据模型
定义消息管理器使用的数据结构
"""

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Optional

from src.common.logger import get_logger
from src.plugin_system.base.component_types import ChatMode, ChatType

from . import BaseDataModel

if TYPE_CHECKING:
    from .database_data_model import DatabaseMessages

logger = get_logger("stream_context")


class MessageStatus(Enum):
    """消息状态枚举"""

    UNREAD = "unread"  # 未读消息
    READ = "read"  # 已读消息
    PROCESSING = "processing"  # 处理中


@dataclass
class StreamContext(BaseDataModel):
    """聊天流上下文信息"""

    stream_id: str
    chat_type: ChatType = ChatType.PRIVATE  # 聊天类型，默认为私聊
    chat_mode: ChatMode = ChatMode.NORMAL  # 聊天模式，默认为普通模式
    unread_messages: list["DatabaseMessages"] = field(default_factory=list)
    history_messages: list["DatabaseMessages"] = field(default_factory=list)
    last_check_time: float = field(default_factory=time.time)
    is_active: bool = True
    processing_task: asyncio.Task | None = None
    interruption_count: int = 0  # 打断计数器
    last_interruption_time: float = 0.0  # 上次打断时间

    # 独立分发周期字段
    next_check_time: float = field(default_factory=time.time)  # 下次检查时间
    distribution_interval: float = 5.0  # 当前分发周期（秒）

    # 新增字段以替代ChatMessageContext功能
    current_message: Optional["DatabaseMessages"] = None
    priority_mode: str | None = None
    priority_info: dict | None = None

    def add_action_to_message(self, message_id: str, action: str):
        """
        向指定消息添加执行的动作

        Args:
            message_id: 消息ID
            action: 要添加的动作名称
        """
        # 在未读消息中查找并更新
        for message in self.unread_messages:
            if message.message_id == message_id:
                message.add_action(action)
                break

        # 在历史消息中查找并更新
        for message in self.history_messages:
            if message.message_id == message_id:
                message.add_action(action)
                break

    def mark_message_as_read(self, message_id: str):
        """标记消息为已读"""
        for msg in self.unread_messages:
            if msg.message_id == message_id:
                msg.is_read = True
                self.history_messages.append(msg)
                self.unread_messages.remove(msg)
                break

    def get_unread_messages(self) -> list["DatabaseMessages"]:
        """获取未读消息"""
        return [msg for msg in self.unread_messages if not msg.is_read]

    def get_history_messages(self, limit: int = 20) -> list["DatabaseMessages"]:
        """获取历史消息"""
        # 优先返回最近的历史消息和所有未读消息
        recent_history = self.history_messages[-limit:] if len(self.history_messages) > limit else self.history_messages
        return recent_history

    def calculate_interruption_probability(self, max_limit: int, min_probability: float = 0.1, probability_factor: float | None = None) -> float:
        """计算打断概率 - 使用反比例函数模型

        Args:
            max_limit: 最大打断次数
            min_probability: 最低打断概率
            probability_factor: 已废弃的参数，保留是为了向后兼容，不再使用

        Returns:
            float: 打断概率 (0.0 - 1.0)
        """
        if max_limit <= 0:
            return 0.0

        # 如果已达到或超过最大次数，完全禁止打断
        if self.interruption_count >= max_limit:
            return 0.0

        # 反比例函数概率计算：前期高概率，快速衰减到低概率
        # 公式：概率 = a / (count + b) + min_probability
        # 参数设计：
        # - a = 1.4 (反比例系数)
        # - b = 2.0 (平移参数)
        # 这确保了：
        # - 第1次打断：80% 概率 (count=0)
        # - 第2次打断：35% 概率 (count=1)
        # - 第3次打断：15% 概率 (count=2)
        # - 第4次及以后：约10% 概率 (趋近于min_probability)
        # - 达到max_limit：0% 概率 (达到上限)

        a = 1.4  # 反比例系数
        b = 2.0  # 平移参数

        probability = a / (self.interruption_count + b) + min_probability

        # 确保概率在合理范围内
        probability = max(min_probability, probability)
        return max(0.0, min(1.0, probability))

    async def increment_interruption_count(self):
        """增加打断计数"""
        self.interruption_count += 1
        self.last_interruption_time = time.time()

        # 同步打断计数到ChatStream
        await self._sync_interruption_count_to_stream()

    async def reset_interruption_count(self):
        """重置打断计数"""
        self.interruption_count = 0
        self.last_interruption_time = 0.0

        # 同步打断计数到ChatStream
        await self._sync_interruption_count_to_stream()


    async def _sync_interruption_count_to_stream(self):
        """同步打断计数到ChatStream"""
        try:
            from src.chat.message_receive.chat_stream import get_chat_manager

            chat_manager = get_chat_manager()
            if chat_manager:
                chat_stream = await chat_manager.get_stream(self.stream_id)
                if chat_stream and hasattr(chat_stream, "interruption_count"):
                    # 在这里我们只是标记需要保存，实际的保存会在下次save时进行
                    chat_stream.saved = False
                    logger.debug(
                        f"已同步StreamContext {self.stream_id} 的打断计数 {self.interruption_count} 到ChatStream"
                    )
        except Exception as e:
            logger.warning(f"同步打断计数到ChatStream失败: {e}")

    def set_current_message(self, message: "DatabaseMessages"):
        """设置当前消息"""
        self.current_message = message

    def get_template_name(self) -> str | None:
        """获取模板名称"""
        if (
            self.current_message
            and hasattr(self.current_message, "additional_config")
            and self.current_message.additional_config
        ):
            try:
                import json

                config = json.loads(self.current_message.additional_config)
                if config.get("template_info") and not config.get("template_default", True):
                    return config.get("template_name")
            except (json.JSONDecodeError, AttributeError):
                pass
        return None

    def get_last_message(self) -> Optional["DatabaseMessages"]:
        """获取最后一条消息"""
        if self.current_message:
            return self.current_message
        if self.unread_messages:
            return self.unread_messages[-1]
        if self.history_messages:
            return self.history_messages[-1]
        return None

    def check_types(self, types: list) -> bool:
        """
        检查当前消息是否支持指定的类型

        Args:
            types: 需要检查的消息类型列表，如 ["text", "image", "emoji"]

        Returns:
            bool: 如果消息支持所有指定的类型则返回True，否则返回False
        """
        if not self.current_message:
            return False

        if not types:
            # 如果没有指定类型要求，默认为支持
            return True

        # 优先从additional_config中获取format_info
        if hasattr(self.current_message, "additional_config") and self.current_message.additional_config:
            try:
                import orjson

                config = orjson.loads(self.current_message.additional_config)

                # 检查format_info结构
                if "format_info" in config:
                    format_info = config["format_info"]

                    # 方法1: 直接检查accept_format字段
                    if "accept_format" in format_info:
                        accept_format = format_info["accept_format"]
                        # 确保accept_format是列表类型
                        if isinstance(accept_format, str):
                            accept_format = [accept_format]
                        elif isinstance(accept_format, list):
                            pass
                        else:
                            # 如果accept_format不是字符串或列表，尝试转换为列表
                            accept_format = list(accept_format) if hasattr(accept_format, "__iter__") else []

                        # 检查所有请求的类型是否都被支持
                        for requested_type in types:
                            if requested_type not in accept_format:
                                logger.debug(f"消息不支持类型 '{requested_type}'，支持的类型: {accept_format}")
                                return False
                        return True

                    # 方法2: 检查content_format字段（向后兼容）
                    elif "content_format" in format_info:
                        content_format = format_info["content_format"]
                        # 确保content_format是列表类型
                        if isinstance(content_format, str):
                            content_format = [content_format]
                        elif isinstance(content_format, list):
                            pass
                        else:
                            content_format = list(content_format) if hasattr(content_format, "__iter__") else []

                        # 检查所有请求的类型是否都被支持
                        for requested_type in types:
                            if requested_type not in content_format:
                                logger.debug(f"消息不支持类型 '{requested_type}'，支持的内容格式: {content_format}")
                                return False
                        return True

            except (orjson.JSONDecodeError, AttributeError, TypeError) as e:
                logger.debug(f"解析消息格式信息失败: {e}")

        # 备用方案：如果无法从additional_config获取格式信息，使用默认支持的类型
        # 大多数消息至少支持text类型
        default_supported_types = ["text", "emoji"]
        for requested_type in types:
            if requested_type not in default_supported_types:
                logger.debug(f"使用默认类型检查，消息可能不支持类型 '{requested_type}'")
                # 对于非基础类型，返回False以避免错误
                if requested_type not in ["text", "emoji", "reply"]:
                    return False
        return True

    def get_priority_mode(self) -> str | None:
        """获取优先级模式"""
        return self.priority_mode

    def get_priority_info(self) -> dict | None:
        """获取优先级信息"""
        return self.priority_info

    def __deepcopy__(self, memo):
        """自定义深拷贝，跳过不可序列化的 asyncio.Task (processing_task)。

        deepcopy 在内部可能会尝试 pickle 某些对象（如 asyncio.Task），
        这会在多线程或运行时事件循环中导致 TypeError。这里我们手动复制
        __dict__ 中的字段，确保 processing_task 被设置为 None，其他字段使用
        copy.deepcopy 递归复制。
        """
        import copy

        # 如果已经复制过，直接返回缓存结果
        obj_id = id(self)
        if obj_id in memo:
            return memo[obj_id]

        # 创建一个未初始化的新实例，然后逐个字段深拷贝
        cls = self.__class__
        new = cls.__new__(cls)
        memo[obj_id] = new

        for k, v in self.__dict__.items():
            if k == "processing_task":
                # 不复制 asyncio.Task，避免无法 pickling
                setattr(new, k, None)
            else:
                try:
                    setattr(new, k, copy.deepcopy(v, memo))
                except Exception:
                    # 如果某个字段无法深拷贝，退回到原始引用（安全性谨慎）
                    setattr(new, k, v)

        return new


@dataclass
class MessageManagerStats(BaseDataModel):
    """消息管理器统计信息"""

    total_streams: int = 0
    active_streams: int = 0
    total_unread_messages: int = 0
    total_processed_messages: int = 0
    start_time: float = field(default_factory=time.time)

    @property
    def uptime(self) -> float:
        """运行时间"""
        return time.time() - self.start_time


@dataclass
class StreamStats(BaseDataModel):
    """聊天流统计信息"""

    stream_id: str
    is_active: bool
    unread_count: int
    history_count: int
    last_check_time: float
    has_active_task: bool
