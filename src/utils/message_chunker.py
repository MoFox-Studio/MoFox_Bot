"""
MoFox-Bot 端的消息切片处理模块
用于接收和重组来自 Napcat-Adapter 的切片消息
"""

import asyncio
import time
from typing import Any

import orjson

from src.common.logger import get_logger

logger = get_logger("message_chunker")


class MessageReassembler:
    """消息重组器，用于重组来自 Ada 的切片消息"""

    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self.chunk_buffers: dict[str, dict[str, Any]] = {}
        self._cleanup_task = None

    async def start_cleanup_task(self):
        """启动清理任务"""
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._cleanup_expired_chunks())
            logger.info("消息重组器清理任务已启动")

    async def stop_cleanup_task(self):
        """停止清理任务"""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None
            logger.info("消息重组器清理任务已停止")

    async def _cleanup_expired_chunks(self):
        """清理过期的切片缓冲区"""
        while True:
            try:
                await asyncio.sleep(10)  # 每10秒检查一次
                current_time = time.time()

                expired_chunks = []
                for chunk_id, buffer_info in self.chunk_buffers.items():
                    if current_time - buffer_info["timestamp"] > self.timeout:
                        expired_chunks.append(chunk_id)

                for chunk_id in expired_chunks:
                    logger.warning(f"清理过期的切片缓冲区: {chunk_id}")
                    del self.chunk_buffers[chunk_id]

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"清理过期切片时出错: {e}")

    @staticmethod
    def is_chunk_message(message: dict[str, Any]) -> bool:
        """检查是否是来自 Ada 的切片消息"""
        return (
            isinstance(message, dict)
            and "__mmc_chunk_info__" in message
            and "__mmc_chunk_data__" in message
            and "__mmc_is_chunked__" in message
        )

    async def process_chunk(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """
        处理切片消息，如果切片完整则返回重组后的消息

        Args:
            message: 可能的切片消息

        Returns:
            如果切片完整则返回重组后的原始消息，否则返回None
        """
        # 如果不是切片消息，直接返回
        if not self.is_chunk_message(message):
            return message

        try:
            chunk_info = message["__mmc_chunk_info__"]
            chunk_content = message["__mmc_chunk_data__"]

            chunk_id = chunk_info["chunk_id"]
            chunk_index = chunk_info["chunk_index"]
            total_chunks = chunk_info["total_chunks"]
            chunk_timestamp = chunk_info.get("timestamp", time.time())

            # 初始化缓冲区
            if chunk_id not in self.chunk_buffers:
                self.chunk_buffers[chunk_id] = {
                    "chunks": {},
                    "total_chunks": total_chunks,
                    "received_chunks": 0,
                    "timestamp": chunk_timestamp,
                }
                logger.debug(f"初始化切片缓冲区: {chunk_id} (总计 {total_chunks} 个切片)")

            buffer = self.chunk_buffers[chunk_id]

            # 检查切片是否已经接收过
            if chunk_index in buffer["chunks"]:
                logger.warning(f"重复接收切片: {chunk_id}#{chunk_index}")
                return None

            # 添加切片
            buffer["chunks"][chunk_index] = chunk_content
            buffer["received_chunks"] += 1
            buffer["timestamp"] = time.time()  # 更新时间戳

            logger.debug(f"接收切片: {chunk_id}#{chunk_index} ({buffer['received_chunks']}/{total_chunks})")

            # 检查是否接收完整
            if buffer["received_chunks"] == total_chunks:
                # 重组消息
                reassembled_message = ""
                for i in range(total_chunks):
                    if i not in buffer["chunks"]:
                        logger.error(f"切片 {chunk_id}#{i} 缺失，无法重组")
                        return None
                    reassembled_message += buffer["chunks"][i]

                # 清理缓冲区
                del self.chunk_buffers[chunk_id]

                logger.info(f"消息重组完成: {chunk_id} ({len(reassembled_message)} chars)")

                # 尝试反序列化重组后的消息
                try:
                    return orjson.loads(reassembled_message)
                except orjson.JSONDecodeError as e:
                    logger.error(f"重组消息反序列化失败: {e}")
                    return None

            # 还没收集完所有切片，返回None表示继续等待
            return None

        except (KeyError, TypeError, ValueError) as e:
            logger.error(f"处理切片消息时出错: {e}")
            return None

    def get_pending_chunks_info(self) -> dict[str, Any]:
        """获取待处理切片信息"""
        info = {}
        for chunk_id, buffer in self.chunk_buffers.items():
            info[chunk_id] = {
                "received": buffer["received_chunks"],
                "total": buffer["total_chunks"],
                "progress": f"{buffer['received_chunks']}/{buffer['total_chunks']}",
                "age_seconds": time.time() - buffer["timestamp"],
            }
        return info


# 全局重组器实例
reassembler = MessageReassembler()
