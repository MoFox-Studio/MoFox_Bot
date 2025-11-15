import asyncio
import re
import time
import traceback
from collections import deque
from typing import Optional

import orjson
from sqlalchemy import desc, select, update

from src.common.data_models.database_data_model import DatabaseMessages
from src.common.database.core import get_db_session
from src.common.database.core.models import Images, Messages
from src.common.logger import get_logger

from .chat_stream import ChatStream
from .message import MessageSending

logger = get_logger("message_storage")


class MessageStorageBatcher:
    """
    消息存储批处理器

    优化: 将消息缓存一段时间后批量写入数据库，减少数据库连接池压力
    """

    def __init__(self, batch_size: int = 50, flush_interval: float = 5.0):
        """
        初始化批处理器

        Args:
            batch_size: 批量大小，达到此数量立即写入
            flush_interval: 自动刷新间隔（秒）
        """
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.pending_messages: deque = deque()
        self._lock = asyncio.Lock()
        self._flush_task = None
        self._running = False

    async def start(self):
        """启动自动刷新任务"""
        if self._flush_task is None and not self._running:
            self._running = True
            self._flush_task = asyncio.create_task(self._auto_flush_loop())
            logger.info(f"消息存储批处理器已启动 (批量大小: {self.batch_size}, 刷新间隔: {self.flush_interval}秒)")

    async def stop(self):
        """停止批处理器"""
        self._running = False

        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
            self._flush_task = None

        # 刷新剩余的消息
        await self.flush()
        logger.info("消息存储批处理器已停止")

    async def add_message(self, message_data: dict):
        """
        添加消息到批处理队列

        Args:
            message_data: 包含消息对象和chat_stream的字典
                {
                    'message': DatabaseMessages | MessageSending,
                    'chat_stream': ChatStream
                }
        """
        async with self._lock:
            self.pending_messages.append(message_data)

            # 如果达到批量大小，立即刷新
            if len(self.pending_messages) >= self.batch_size:
                logger.debug(f"达到批量大小 {self.batch_size}，立即刷新")
                await self.flush()

    async def flush(self):
        """执行批量写入"""
        async with self._lock:
            if not self.pending_messages:
                return

            messages_to_store = list(self.pending_messages)
            self.pending_messages.clear()

        if not messages_to_store:
            return

        start_time = time.time()
        success_count = 0

        try:
            # 🔧 优化：准备字典数据而不是ORM对象，使用批量INSERT
            messages_dicts = []

            for msg_data in messages_to_store:
                try:
                    message_dict = await self._prepare_message_dict(
                        msg_data["message"],
                        msg_data["chat_stream"]
                    )
                    if message_dict:
                        messages_dicts.append(message_dict)
                except Exception as e:
                    logger.error(f"准备消息数据失败: {e}")
                    continue

            # 批量写入数据库 - 使用高效的批量INSERT
            if messages_dicts:
                from sqlalchemy import insert
                async with get_db_session() as session:
                    stmt = insert(Messages).values(messages_dicts)
                    await session.execute(stmt)
                    await session.commit()
                    success_count = len(messages_dicts)

            elapsed = time.time() - start_time
            logger.info(
                f"批量存储了 {success_count}/{len(messages_to_store)} 条消息 "
                f"(耗时: {elapsed:.3f}秒, 平均 {elapsed/max(success_count,1)*1000:.2f}ms/条)"
            )

        except Exception as e:
            logger.error(f"批量存储消息失败: {e}", exc_info=True)

    async def _prepare_message_dict(self, message, chat_stream):
        """准备消息字典数据（用于批量INSERT）

        这个方法准备字典而不是ORM对象，性能更高
        """
        message_obj = await self._prepare_message_object(message, chat_stream)
        if message_obj is None:
            return None

        # 将ORM对象转换为字典（只包含列字段）
        message_dict = {}
        for column in Messages.__table__.columns:
            message_dict[column.name] = getattr(message_obj, column.name)

        return message_dict

    async def _prepare_message_object(self, message, chat_stream):
        """准备消息对象（从原 store_message 逻辑提取）"""
        try:
            # 过滤敏感信息的正则模式
            pattern = r"<MainRule>.*?</MainRule>|<schedule>.*?</schedule>|<UserMessage>.*?</UserMessage>"

            # 如果是 DatabaseMessages，直接使用它的字段
            if isinstance(message, DatabaseMessages):
                processed_plain_text = message.processed_plain_text
                if processed_plain_text:
                    processed_plain_text = await MessageStorage.replace_image_descriptions(processed_plain_text)
                    safe_processed_plain_text = processed_plain_text or ""
                    filtered_processed_plain_text = re.sub(pattern, "", safe_processed_plain_text, flags=re.DOTALL)
                else:
                    filtered_processed_plain_text = ""

                display_message = message.display_message or message.processed_plain_text or ""
                filtered_display_message = re.sub(pattern, "", display_message, flags=re.DOTALL)

                msg_id = message.message_id
                msg_time = message.time
                chat_id = message.chat_id
                reply_to = ""
                is_mentioned = message.is_mentioned
                interest_value = message.interest_value or 0.0
                priority_mode = ""
                priority_info_json = None
                is_emoji = message.is_emoji or False
                is_picid = message.is_picid or False
                is_notify = message.is_notify or False
                is_command = message.is_command or False
                is_public_notice = message.is_public_notice or False
                notice_type = message.notice_type
                # 序列化actions列表为JSON字符串
                actions = orjson.dumps(message.actions).decode("utf-8") if message.actions else None
                should_reply = message.should_reply
                should_act = message.should_act
                additional_config = message.additional_config
                # 确保关键词字段是字符串格式（如果不是，则序列化）
                key_words = MessageStorage._serialize_keywords(message.key_words)
                key_words_lite = MessageStorage._serialize_keywords(message.key_words_lite)
                memorized_times = 0

                user_platform = message.user_info.platform if message.user_info else ""
                user_id = message.user_info.user_id if message.user_info else ""
                user_nickname = message.user_info.user_nickname if message.user_info else ""
                user_cardname = message.user_info.user_cardname if message.user_info else None

                chat_info_stream_id = message.chat_info.stream_id if message.chat_info else ""
                chat_info_platform = message.chat_info.platform if message.chat_info else ""
                chat_info_create_time = message.chat_info.create_time if message.chat_info else 0.0
                chat_info_last_active_time = message.chat_info.last_active_time if message.chat_info else 0.0
                chat_info_user_platform = message.chat_info.user_info.platform if message.chat_info and message.chat_info.user_info else ""
                chat_info_user_id = message.chat_info.user_info.user_id if message.chat_info and message.chat_info.user_info else ""
                chat_info_user_nickname = message.chat_info.user_info.user_nickname if message.chat_info and message.chat_info.user_info else ""
                chat_info_user_cardname = message.chat_info.user_info.user_cardname if message.chat_info and message.chat_info.user_info else None
                chat_info_group_platform = message.group_info.group_platform if message.group_info else None
                chat_info_group_id = message.group_info.group_id if message.group_info else None
                chat_info_group_name = message.group_info.group_name if message.group_info else None

            else:
                # MessageSending 处理逻辑
                processed_plain_text = message.processed_plain_text

                if processed_plain_text:
                    processed_plain_text = await MessageStorage.replace_image_descriptions(processed_plain_text)
                    safe_processed_plain_text = processed_plain_text or ""
                    filtered_processed_plain_text = re.sub(pattern, "", safe_processed_plain_text, flags=re.DOTALL)
                else:
                    filtered_processed_plain_text = ""

                if isinstance(message, MessageSending):
                    display_message = message.display_message
                    if display_message:
                        filtered_display_message = re.sub(pattern, "", display_message, flags=re.DOTALL)
                    else:
                        filtered_display_message = re.sub(pattern, "", (message.processed_plain_text or ""), flags=re.DOTALL)
                    interest_value = 0
                    is_mentioned = False
                    reply_to = message.reply_to
                    priority_mode = ""
                    priority_info = {}
                    is_emoji = False
                    is_picid = False
                    is_notify = False
                    is_command = False
                    is_public_notice = False
                    notice_type = None
                    actions = None
                    should_reply = None
                    should_act = None
                    additional_config = None
                    key_words = ""
                    key_words_lite = ""
                else:
                    filtered_display_message = ""
                    interest_value = message.interest_value
                    is_mentioned = message.is_mentioned
                    reply_to = ""
                    priority_mode = message.priority_mode
                    priority_info = message.priority_info
                    is_emoji = message.is_emoji
                    is_picid = message.is_picid
                    is_notify = message.is_notify
                    is_command = message.is_command
                    is_public_notice = getattr(message, "is_public_notice", False)
                    notice_type = getattr(message, "notice_type", None)
                    # 序列化actions列表为JSON字符串
                    actions = orjson.dumps(getattr(message, "actions", None)).decode("utf-8") if getattr(message, "actions", None) else None
                    should_reply = getattr(message, "should_reply", None)
                    should_act = getattr(message, "should_act", None)
                    additional_config = getattr(message, "additional_config", None)
                    key_words = MessageStorage._serialize_keywords(message.key_words)
                    key_words_lite = MessageStorage._serialize_keywords(message.key_words_lite)

                chat_info_dict = chat_stream.to_dict()
                user_info_dict = message.message_info.user_info.to_dict()

                msg_id = message.message_info.message_id
                msg_time = float(message.message_info.time or time.time())
                chat_id = chat_stream.stream_id
                memorized_times = message.memorized_times

                group_info_from_chat = chat_info_dict.get("group_info") or {}
                user_info_from_chat = chat_info_dict.get("user_info") or {}

                priority_info_json = orjson.dumps(priority_info).decode("utf-8") if priority_info else None

                user_platform = user_info_dict.get("platform")
                user_id = user_info_dict.get("user_id")
                # 将机器人自己的user_id标记为"SELF"，增强对自我身份的识别
                user_nickname = user_info_dict.get("user_nickname")
                user_cardname = user_info_dict.get("user_cardname")

                chat_info_stream_id = chat_info_dict.get("stream_id")
                chat_info_platform = chat_info_dict.get("platform")
                chat_info_create_time = float(chat_info_dict.get("create_time", 0.0))
                chat_info_last_active_time = float(chat_info_dict.get("last_active_time", 0.0))
                chat_info_user_platform = user_info_from_chat.get("platform")
                chat_info_user_id = user_info_from_chat.get("user_id")
                chat_info_user_nickname = user_info_from_chat.get("user_nickname")
                chat_info_user_cardname = user_info_from_chat.get("user_cardname")
                chat_info_group_platform = group_info_from_chat.get("platform")
                chat_info_group_id = group_info_from_chat.get("group_id")
                chat_info_group_name = group_info_from_chat.get("group_name")

            # 创建消息对象
            return Messages(
                message_id=msg_id,
                time=msg_time,
                chat_id=chat_id,
                reply_to=reply_to,
                is_mentioned=is_mentioned,
                chat_info_stream_id=chat_info_stream_id,
                chat_info_platform=chat_info_platform,
                chat_info_user_platform=chat_info_user_platform,
                chat_info_user_id=chat_info_user_id,
                chat_info_user_nickname=chat_info_user_nickname,
                chat_info_user_cardname=chat_info_user_cardname,
                chat_info_group_platform=chat_info_group_platform,
                chat_info_group_id=chat_info_group_id,
                chat_info_group_name=chat_info_group_name,
                chat_info_create_time=chat_info_create_time,
                chat_info_last_active_time=chat_info_last_active_time,
                user_platform=user_platform,
                user_id=user_id,
                user_nickname=user_nickname,
                user_cardname=user_cardname,
                processed_plain_text=filtered_processed_plain_text,
                display_message=filtered_display_message,
                memorized_times=memorized_times,
                interest_value=interest_value,
                priority_mode=priority_mode,
                priority_info=priority_info_json,
                additional_config=additional_config,
                is_emoji=is_emoji,
                is_picid=is_picid,
                is_notify=is_notify,
                is_command=is_command,
                is_public_notice=is_public_notice,
                notice_type=notice_type,
                actions=actions,
                should_reply=should_reply,
                should_act=should_act,
                key_words=key_words,
                key_words_lite=key_words_lite,
            )

        except Exception as e:
            logger.error(f"准备消息对象失败: {e}")
            return None

    async def _auto_flush_loop(self):
        """自动刷新循环"""
        while self._running:
            try:
                await asyncio.sleep(self.flush_interval)
                await self.flush()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"自动刷新失败: {e}")


# 全局批处理器实例
_message_storage_batcher: MessageStorageBatcher | None = None
_message_update_batcher: Optional["MessageUpdateBatcher"] = None


def get_message_storage_batcher() -> MessageStorageBatcher:
    """获取消息存储批处理器单例"""
    global _message_storage_batcher
    if _message_storage_batcher is None:
        _message_storage_batcher = MessageStorageBatcher(
            batch_size=50,  # 批量大小：50条消息
            flush_interval=5.0  # 刷新间隔：5秒
        )
    return _message_storage_batcher


class MessageUpdateBatcher:
    """
    消息更新批处理器

    优化: 将多个消息ID更新操作批量处理，减少数据库连接次数
    """

    def __init__(self, batch_size: int = 20, flush_interval: float = 2.0):
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.pending_updates: deque = deque()
        self._lock = asyncio.Lock()
        self._flush_task = None

    async def start(self):
        """启动自动刷新任务"""
        if self._flush_task is None:
            self._flush_task = asyncio.create_task(self._auto_flush_loop())
            logger.debug("消息更新批处理器已启动")

    async def stop(self):
        """停止批处理器"""
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
            self._flush_task = None

        # 刷新剩余的更新
        await self.flush()
        logger.debug("消息更新批处理器已停止")

    async def add_update(self, mmc_message_id: str, qq_message_id: str):
        """添加消息ID更新到批处理队列"""
        async with self._lock:
            self.pending_updates.append((mmc_message_id, qq_message_id))

            # 如果达到批量大小，立即刷新
            if len(self.pending_updates) >= self.batch_size:
                await self.flush()

    async def flush(self):
        """执行批量更新"""
        async with self._lock:
            if not self.pending_updates:
                return

            updates = list(self.pending_updates)
            self.pending_updates.clear()

        try:
            async with get_db_session() as session:
                updated_count = 0
                for mmc_id, qq_id in updates:
                    result = await session.execute(
                        update(Messages)
                        .where(Messages.message_id == mmc_id)
                        .values(message_id=qq_id)
                    )
                    if result.rowcount > 0:
                        updated_count += 1

                await session.commit()

                if updated_count > 0:
                    logger.debug(f"批量更新了 {updated_count}/{len(updates)} 条消息ID")

        except Exception as e:
            logger.error(f"批量更新消息ID失败: {e}")

    async def _auto_flush_loop(self):
        """自动刷新循环"""
        while True:
            try:
                await asyncio.sleep(self.flush_interval)
                await self.flush()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"自动刷新出错: {e}")


def get_message_update_batcher() -> MessageUpdateBatcher:
    """获取全局消息更新批处理器"""
    global _message_update_batcher
    if _message_update_batcher is None:
        _message_update_batcher = MessageUpdateBatcher()
    return _message_update_batcher


class MessageStorage:
    @staticmethod
    def _serialize_keywords(keywords) -> str:
        """将关键词列表序列化为JSON字符串"""
        if isinstance(keywords, list):
            return orjson.dumps(keywords).decode("utf-8")
        return "[]"

    @staticmethod
    def _deserialize_keywords(keywords_str: str) -> list:
        """将JSON字符串反序列化为关键词列表"""
        if not keywords_str:
            return []
        try:
            return orjson.loads(keywords_str)
        except (orjson.JSONDecodeError, TypeError):
            return []

    @staticmethod
    async def store_message(message: DatabaseMessages | MessageSending, chat_stream: ChatStream, use_batch: bool = True) -> None:
        """
        存储消息到数据库

        Args:
            message: 消息对象
            chat_stream: 聊天流对象
            use_batch: 是否使用批处理（默认True，推荐）。设为False时立即写入数据库。
        """
        # 使用批处理器（推荐）
        if use_batch:
            batcher = get_message_storage_batcher()
            await batcher.add_message({
                "message": message,
                "chat_stream": chat_stream
            })
            return

        # 直接写入模式（保留用于特殊场景）
        try:
            # 过滤敏感信息的正则模式
            pattern = r"<MainRule>.*?</MainRule>|<schedule>.*?</schedule>|<UserMessage>.*?</UserMessage>"

            # 如果是 DatabaseMessages，直接使用它的字段
            if isinstance(message, DatabaseMessages):
                processed_plain_text = message.processed_plain_text
                if processed_plain_text:
                    processed_plain_text = await MessageStorage.replace_image_descriptions(processed_plain_text)
                    safe_processed_plain_text = processed_plain_text or ""
                    filtered_processed_plain_text = re.sub(pattern, "", safe_processed_plain_text, flags=re.DOTALL)
                else:
                    filtered_processed_plain_text = ""

                display_message = message.display_message or message.processed_plain_text or ""
                filtered_display_message = re.sub(pattern, "", display_message, flags=re.DOTALL)

                # 直接从 DatabaseMessages 获取所有字段
                msg_id = message.message_id
                msg_time = message.time
                chat_id = message.chat_id
                reply_to = ""  # DatabaseMessages 没有 reply_to 字段
                is_mentioned = message.is_mentioned
                interest_value = message.interest_value or 0.0
                priority_mode = ""  # DatabaseMessages 没有 priority_mode
                priority_info_json = None  # DatabaseMessages 没有 priority_info
                is_emoji = message.is_emoji or False
                is_picid = message.is_picid or False
                is_notify = message.is_notify or False
                is_command = message.is_command or False
                key_words = ""  # DatabaseMessages 没有 key_words
                key_words_lite = ""
                memorized_times = 0  # DatabaseMessages 没有 memorized_times

                # 使用 DatabaseMessages 中的嵌套对象信息
                user_platform = message.user_info.platform if message.user_info else ""
                user_id = message.user_info.user_id if message.user_info else ""
                user_nickname = message.user_info.user_nickname if message.user_info else ""
                user_cardname = message.user_info.user_cardname if message.user_info else None

                chat_info_stream_id = message.chat_info.stream_id if message.chat_info else ""
                chat_info_platform = message.chat_info.platform if message.chat_info else ""
                chat_info_create_time = message.chat_info.create_time if message.chat_info else 0.0
                chat_info_last_active_time = message.chat_info.last_active_time if message.chat_info else 0.0
                chat_info_user_platform = message.chat_info.user_info.platform if message.chat_info and message.chat_info.user_info else ""
                chat_info_user_id = message.chat_info.user_info.user_id if message.chat_info and message.chat_info.user_info else ""
                chat_info_user_nickname = message.chat_info.user_info.user_nickname if message.chat_info and message.chat_info.user_info else ""
                chat_info_user_cardname = message.chat_info.user_info.user_cardname if message.chat_info and message.chat_info.user_info else None
                chat_info_group_platform = message.group_info.group_platform if message.group_info else None
                chat_info_group_id = message.group_info.group_id if message.group_info else None
                chat_info_group_name = message.group_info.group_name if message.group_info else None

            else:
                # MessageSending 处理逻辑
                processed_plain_text = message.processed_plain_text

                if processed_plain_text:
                    processed_plain_text = await MessageStorage.replace_image_descriptions(processed_plain_text)
                    # 增加对None的防御性处理
                    safe_processed_plain_text = processed_plain_text or ""
                    filtered_processed_plain_text = re.sub(pattern, "", safe_processed_plain_text, flags=re.DOTALL)
                else:
                    filtered_processed_plain_text = ""

                if isinstance(message, MessageSending):
                    display_message = message.display_message
                    if display_message:
                        filtered_display_message = re.sub(pattern, "", display_message, flags=re.DOTALL)
                    else:
                        # 如果没有设置display_message，使用processed_plain_text作为显示消息
                        filtered_display_message = (
                            re.sub(pattern, "", (message.processed_plain_text or ""), flags=re.DOTALL)
                        )
                    interest_value = 0
                    is_mentioned = False
                    reply_to = message.reply_to
                    priority_mode = ""
                    priority_info = {}
                    is_emoji = False
                    is_picid = False
                    is_notify = False
                    is_command = False
                    is_public_notice = False
                    notice_type = None
                    actions = None
                    should_reply = False
                    should_act = False
                    key_words = ""
                    key_words_lite = ""
                else:
                    filtered_display_message = ""
                    interest_value = message.interest_value
                    is_mentioned = message.is_mentioned
                    reply_to = ""
                    priority_mode = message.priority_mode
                    priority_info = message.priority_info
                    is_emoji = message.is_emoji
                    is_picid = message.is_picid
                    is_notify = message.is_notify
                    is_command = message.is_command
                    is_public_notice = getattr(message, "is_public_notice", False)
                    notice_type = getattr(message, "notice_type", None)
                    # 序列化actions列表为JSON字符串
                    actions = orjson.dumps(getattr(message, "actions", None)).decode("utf-8") if getattr(message, "actions", None) else None
                    should_reply = getattr(message, "should_reply", False)
                    should_act = getattr(message, "should_act", False)
                    # 序列化关键词列表为JSON字符串
                    key_words = MessageStorage._serialize_keywords(message.key_words)
                    key_words_lite = MessageStorage._serialize_keywords(message.key_words_lite)

                chat_info_dict = chat_stream.to_dict()
                user_info_dict = message.message_info.user_info.to_dict()  # type: ignore

                # message_id 现在是 TextField，直接使用字符串值
                msg_id = message.message_info.message_id
                msg_time = float(message.message_info.time or time.time())
                chat_id = chat_stream.stream_id
                memorized_times = message.memorized_times

                # 安全地获取 group_info, 如果为 None 则视为空字典
                group_info_from_chat = chat_info_dict.get("group_info") or {}
                # 安全地获取 user_info, 如果为 None 则视为空字典 (以防万一)
                user_info_from_chat = chat_info_dict.get("user_info") or {}

                # 将priority_info字典序列化为JSON字符串，以便存储到数据库的Text字段
                priority_info_json = orjson.dumps(priority_info).decode("utf-8") if priority_info else None

                user_platform = user_info_dict.get("platform")
                user_id = user_info_dict.get("user_id")
                user_nickname = user_info_dict.get("user_nickname")
                user_cardname = user_info_dict.get("user_cardname")

                chat_info_stream_id = chat_info_dict.get("stream_id")
                chat_info_platform = chat_info_dict.get("platform")
                chat_info_create_time = float(chat_info_dict.get("create_time", 0.0))
                chat_info_last_active_time = float(chat_info_dict.get("last_active_time", 0.0))
                chat_info_user_platform = user_info_from_chat.get("platform")
                chat_info_user_id = user_info_from_chat.get("user_id")
                chat_info_user_nickname = user_info_from_chat.get("user_nickname")
                chat_info_user_cardname = user_info_from_chat.get("user_cardname")
                chat_info_group_platform = group_info_from_chat.get("platform")
                chat_info_group_id = group_info_from_chat.get("group_id")
                chat_info_group_name = group_info_from_chat.get("group_name")

            # 获取数据库会话
            new_message = Messages(
                message_id=msg_id,
                time=msg_time,
                chat_id=chat_id,
                reply_to=reply_to,
                is_mentioned=is_mentioned,
                chat_info_stream_id=chat_info_stream_id,
                chat_info_platform=chat_info_platform,
                chat_info_user_platform=chat_info_user_platform,
                chat_info_user_id=chat_info_user_id,
                chat_info_user_nickname=chat_info_user_nickname,
                chat_info_user_cardname=chat_info_user_cardname,
                chat_info_group_platform=chat_info_group_platform,
                chat_info_group_id=chat_info_group_id,
                chat_info_group_name=chat_info_group_name,
                chat_info_create_time=chat_info_create_time,
                chat_info_last_active_time=chat_info_last_active_time,
                user_platform=user_platform,
                user_id=user_id,
                user_nickname=user_nickname,
                user_cardname=user_cardname,
                processed_plain_text=filtered_processed_plain_text,
                display_message=filtered_display_message,
                memorized_times=memorized_times,
                interest_value=interest_value,
                priority_mode=priority_mode,
                priority_info=priority_info_json,
                is_emoji=is_emoji,
                is_picid=is_picid,
                is_notify=is_notify,
                is_command=is_command,
                is_public_notice=is_public_notice,
                notice_type=notice_type,
                actions=actions,
                should_reply=should_reply,
                should_act=should_act,
                key_words=key_words,
                key_words_lite=key_words_lite,
            )
            async with get_db_session() as session:
                session.add(new_message)
                await session.commit()

        except Exception:
            logger.exception("存储消息失败")
            logger.error(f"消息：{message}")
            traceback.print_exc()

    @staticmethod
    async def update_message(message_data: dict, use_batch: bool = True):
        """
        更新消息ID（从消息字典）

        优化: 添加批处理选项，将多个更新操作合并，减少数据库连接

        Args:
            message_data: 消息数据字典
            use_batch: 是否使用批处理（默认True）
        """
        try:
            # 从字典中提取信息
            message_info = message_data.get("message_info", {})
            mmc_message_id = message_info.get("message_id")

            message_segment = message_data.get("message_segment", {})
            segment_type = message_segment.get("type") if isinstance(message_segment, dict) else None
            segment_data = message_segment.get("data", {}) if isinstance(message_segment, dict) else {}

            qq_message_id = None

            logger.debug(f"尝试更新消息ID: {mmc_message_id}, 消息段类型: {segment_type}")

            # 根据消息段类型提取message_id
            if segment_type == "notify":
                qq_message_id = segment_data.get("id")
            elif segment_type == "text":
                qq_message_id = segment_data.get("id")
            elif segment_type == "reply":
                qq_message_id = segment_data.get("id")
                if qq_message_id:
                    logger.debug(f"从reply消息段获取到消息ID: {qq_message_id}")
            elif segment_type == "adapter_response":
                logger.debug("适配器响应消息，不需要更新ID")
                return
            elif segment_type == "adapter_command":
                logger.debug("适配器命令消息，不需要更新ID")
                return
            else:
                logger.debug(f"未知的消息段类型: {segment_type}，跳过ID更新")
                return

            if not qq_message_id:
                logger.debug(f"消息段类型 {segment_type} 中未找到有效的message_id，跳过更新")
                logger.debug(f"消息段数据: {segment_data}")
                return

            # 优化: 使用批处理器减少数据库连接
            if use_batch:
                batcher = get_message_update_batcher()
                await batcher.add_update(mmc_message_id, qq_message_id)
                logger.debug(f"消息ID更新已加入批处理队列: {mmc_message_id} -> {qq_message_id}")
            else:
                # 直接更新（保留原有逻辑用于特殊情况）
                from src.common.database.core import get_db_session

                async with get_db_session() as session:
                    matched_message = (
                        await session.execute(
                            select(Messages).where(Messages.message_id == mmc_message_id).order_by(desc(Messages.time))
                        )
                    ).scalar()

                    if matched_message:
                        await session.execute(
                            update(Messages).where(Messages.id == matched_message.id).values(message_id=qq_message_id)
                        )
                        logger.debug(f"更新消息ID成功: {matched_message.message_id} -> {qq_message_id}")
                    else:
                        logger.warning(f"未找到匹配的消息记录: {mmc_message_id}")

        except Exception as e:
            logger.error(f"更新消息ID失败: {e}")
            logger.error(
                f"消息信息: message_id={message_data.get('message_info', {}).get('message_id', 'N/A')}, "
                f"segment_type={message_data.get('message_segment', {}).get('type', 'N/A')}"
            )

    @staticmethod
    async def replace_image_descriptions(text: str) -> str:
        """异步地将文本中的所有[图片：描述]标记替换为[picid:image_id]"""
        pattern = r"\[图片：([^\]]+)\]"

        # 如果没有匹配项，提前返回以提高效率
        if not re.search(pattern, text):
            return text

        # re.sub不支持异步替换函数，所以我们需要手动迭代和替换
        new_text = []
        last_end = 0
        for match in re.finditer(pattern, text):
            # 添加上一个匹配到当前匹配之间的文本
            new_text.append(text[last_end:match.start()])

            description = match.group(1).strip()
            replacement = match.group(0) # 默认情况下，替换为原始匹配文本
            try:
                async with get_db_session() as session:
                    # 查询数据库以找到具有该描述的最新图片记录
                    result = await session.execute(
                        select(Images.image_id)
                        .where(Images.description == description)
                        .order_by(desc(Images.timestamp))
                        .limit(1)
                    )
                    image_id = result.scalar_one_or_none()

                    if image_id:
                        replacement = f"[picid:{image_id}]"
                        logger.debug(f"成功将描述 '{description[:20]}...' 替换为 picid '{image_id}'")
                    else:
                        logger.warning(f"无法为描述 '{description[:20]}...' 找到对应的picid，将保留原始标记")
            except Exception as e:
                logger.error(f"替换图片描述时查询数据库失败: {e}", exc_info=True)

            new_text.append(replacement)
            last_end = match.end()

        # 添加最后一个匹配到字符串末尾的文本
        new_text.append(text[last_end:])

        return "".join(new_text)

    @staticmethod
    async def update_message_interest_value(
        message_id: str,
        interest_value: float,
        should_reply: bool | None = None,
    ) -> None:
        """
        更新数据库中消息的interest_value字段

        Args:
            message_id: 消息ID
            interest_value: 兴趣度值
        """
        try:
            async with get_db_session() as session:
                # 更新消息的interest_value字段
                values = {"interest_value": interest_value}
                if should_reply is not None:
                    values["should_reply"] = should_reply

                stmt = update(Messages).where(Messages.message_id == message_id).values(**values)
                result = await session.execute(stmt)
                await session.commit()

                if result.rowcount > 0:
                    logger.debug(f"成功更新消息 {message_id} 的interest_value为 {interest_value}")
                else:
                    logger.warning(f"未找到消息 {message_id}，无法更新interest_value")

        except Exception as e:
            logger.error(f"更新消息 {message_id} 的interest_value失败: {e}")
            raise

    @staticmethod
    async def bulk_update_interest_values(
        interest_map: dict[str, float],
        reply_map: dict[str, bool] | None = None,
    ) -> None:
        """批量更新消息的兴趣度与回复标记"""
        if not interest_map:
            return

        try:
            async with get_db_session() as session:
                for message_id, interest_value in interest_map.items():
                    values = {"interest_value": interest_value}
                    if reply_map and message_id in reply_map:
                        values["should_reply"] = reply_map[message_id]

                    stmt = update(Messages).where(Messages.message_id == message_id).values(**values)
                    await session.execute(stmt)

                await session.commit()
                logger.debug(f"批量更新兴趣度 {len(interest_map)} 条记录")
        except Exception as e:
            logger.error(f"批量更新消息兴趣度失败: {e}")
            raise

    @staticmethod
    async def fix_zero_interest_values(chat_id: str, since_time: float) -> int:
        """
        修复指定聊天中interest_value为0或null的历史消息记录

        Args:
            chat_id: 聊天ID
            since_time: 从指定时间开始修复（时间戳）

        Returns:
            修复的记录数量
        """
        try:
            async with get_db_session() as session:
                from sqlalchemy import select, update

                from src.common.database.core.models import Messages

                # 查找需要修复的记录：interest_value为0、null或很小的值
                query = (
                    select(Messages)
                    .where(
                        (Messages.chat_id == chat_id)
                        & (Messages.time >= since_time)
                        & (
                            (Messages.interest_value == 0)
                            | (Messages.interest_value.is_(None))
                            | (Messages.interest_value < 0.1)
                        )
                    )
                    .limit(50)
                )  # 限制每次修复的数量，避免性能问题

                result = await session.execute(query)
                messages_to_fix = result.scalars().all()
                fixed_count = 0

                for msg in messages_to_fix:
                    # 为这些消息设置一个合理的默认兴趣度
                    # 可以基于消息长度、内容或其他因素计算
                    default_interest = 0.3  # 默认中等兴趣度

                    # 如果消息内容较长，可能是重要消息，兴趣度稍高
                    if hasattr(msg, "processed_plain_text") and msg.processed_plain_text:
                        text_length = len(msg.processed_plain_text)
                        if text_length > 50:  # 长消息
                            default_interest = 0.4
                        elif text_length > 20:  # 中等长度消息
                            default_interest = 0.35

                    # 如果是被@的消息，兴趣度更高
                    if getattr(msg, "is_mentioned", False):
                        default_interest = min(default_interest + 0.2, 0.8)

                    # 执行更新
                    update_stmt = (
                        update(Messages)
                        .where(Messages.message_id == msg.message_id)
                        .values(interest_value=default_interest)
                    )

                    result = await session.execute(update_stmt)
                    if result.rowcount > 0:
                        fixed_count += 1
                        logger.debug(f"修复消息 {msg.message_id} 的interest_value为 {default_interest}")

                await session.commit()
                logger.info(f"共修复了 {fixed_count} 条历史消息的interest_value值")
                return fixed_count

        except Exception as e:
            logger.error(f"修复历史消息interest_value失败: {e}")
            return 0
