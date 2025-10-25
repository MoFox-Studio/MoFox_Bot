"""
消息集合处理器
负责收集消息、创建集合并将其存入向量存储。
"""

import asyncio
from collections import deque
from typing import Any

from src.chat.memory_system.memory_chunk import MessageCollection
from src.chat.memory_system.message_collection_storage import MessageCollectionStorage
from src.common.logger import get_logger

logger = get_logger(__name__)


class MessageCollectionProcessor:
    """处理消息集合的创建和存储"""

    def __init__(self, storage: MessageCollectionStorage, buffer_size: int = 5):
        self.storage = storage
        self.buffer_size = buffer_size
        self.message_buffers: dict[str, deque[str]] = {}
        self._lock = asyncio.Lock()

    async def add_message(self, message_text: str, chat_id: str):
        """添加一条新消息到指定聊天的缓冲区，并在满时触发处理"""
        async with self._lock:
            if not isinstance(message_text, str) or not message_text.strip():
                return

            if chat_id not in self.message_buffers:
                self.message_buffers[chat_id] = deque(maxlen=self.buffer_size)

            buffer = self.message_buffers[chat_id]
            buffer.append(message_text)
            logger.debug(f"消息已添加到聊天 '{chat_id}' 的缓冲区，当前数量: {len(buffer)}/{self.buffer_size}")

            if len(buffer) == self.buffer_size:
                await self._process_buffer(chat_id)

    async def _process_buffer(self, chat_id: str):
        """处理指定聊天缓冲区中的消息，创建并存储一个集合"""
        buffer = self.message_buffers.get(chat_id)
        if not buffer or len(buffer) < self.buffer_size:
            return

        messages_to_process = list(buffer)
        buffer.clear()

        logger.info(f"聊天 '{chat_id}' 的消息缓冲区已满，开始创建消息集合...")

        try:
            combined_text = "\n".join(messages_to_process)

            collection = MessageCollection(
                chat_id=chat_id,
                messages=messages_to_process,
                combined_text=combined_text,
            )

            await self.storage.add_collection(collection)
            logger.info(f"成功为聊天 '{chat_id}' 创建并存储了新的消息集合: {collection.collection_id}")

        except Exception as e:
            logger.error(f"处理聊天 '{chat_id}' 的消息缓冲区失败: {e}", exc_info=True)

    def get_stats(self) -> dict[str, Any]:
        """获取处理器统计信息"""
        total_buffered_messages = sum(len(buf) for buf in self.message_buffers.values())
        return {
            "active_buffers": len(self.message_buffers),
            "total_buffered_messages": total_buffered_messages,
            "buffer_capacity_per_chat": self.buffer_size,
        }