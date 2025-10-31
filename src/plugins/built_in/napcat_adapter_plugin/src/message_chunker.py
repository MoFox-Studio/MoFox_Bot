"""
消息切片处理模块
用于在 Ada 发送给 MMC 时进行消息切片，利用 WebSocket 协议的自动重组特性
仅在 Ada -> MMC 方向进行切片，其他方向（MMC -> Ada，Ada <-> Napcat）不切片
"""

import asyncio
import json
import time
import uuid
from typing import Any, Dict, List, Optional, Union

from src.common.logger import get_logger
from src.plugin_system.apis import config_api

logger = get_logger("napcat_adapter")


class MessageChunker:
    """消息切片器，用于处理大消息的分片发送"""

    def __init__(self):
        self.max_chunk_size = 64 * 1024  # 默认值，将在设置配置时更新
        self.plugin_config = None

    def set_plugin_config(self, plugin_config: dict):
        """设置插件配置"""
        self.plugin_config = plugin_config
        if plugin_config:
            max_frame_size = config_api.get_plugin_config(plugin_config, "slicing.max_frame_size", 64)
            self.max_chunk_size = max_frame_size * 1024

    def should_chunk_message(self, message: Union[str, Dict[str, Any]]) -> bool:
        """判断消息是否需要切片"""
        try:
            if isinstance(message, dict):
                message_str = json.dumps(message, ensure_ascii=False)
            else:
                message_str = message
            return len(message_str.encode("utf-8")) > self.max_chunk_size
        except Exception as e:
            logger.error(f"检查消息大小时出错: {e}")
            return False

    def chunk_message(
        self, message: Union[str, Dict[str, Any]], chunk_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        将消息切片

        Args:
            message: 要切片的消息（字符串或字典）
            chunk_id: 切片组ID，如果不提供则自动生成

        Returns:
            切片后的消息字典列表
        """
        try:
            # 统一转换为字符串
            if isinstance(message, dict):
                message_str = json.dumps(message, ensure_ascii=False)
            else:
                message_str = message

            if not self.should_chunk_message(message_str):
                # 不需要切片的情况，如果输入是字典则返回字典，如果是字符串则包装成非切片标记的字典
                if isinstance(message, dict):
                    return [message]
                else:
                    return [{"_original_message": message_str}]

            if chunk_id is None:
                chunk_id = str(uuid.uuid4())

            message_bytes = message_str.encode("utf-8")
            total_size = len(message_bytes)

            # 计算需要多少个切片
            num_chunks = (total_size + self.max_chunk_size - 1) // self.max_chunk_size

            chunks = []
            for i in range(num_chunks):
                start_pos = i * self.max_chunk_size
                end_pos = min(start_pos + self.max_chunk_size, total_size)

                chunk_data = message_bytes[start_pos:end_pos]

                # 构建切片消息
                chunk_message = {
                    "__mmc_chunk_info__": {
                        "chunk_id": chunk_id,
                        "chunk_index": i,
                        "total_chunks": num_chunks,
                        "chunk_size": len(chunk_data),
                        "total_size": total_size,
                        "timestamp": time.time(),
                    },
                    "__mmc_chunk_data__": chunk_data.decode("utf-8", errors="ignore"),
                    "__mmc_is_chunked__": True,
                }

                chunks.append(chunk_message)

            logger.debug(f"消息切片完成: {total_size} bytes -> {num_chunks} chunks (ID: {chunk_id})")
            return chunks

        except Exception as e:
            logger.error(f"消息切片时出错: {e}")
            # 出错时返回原消息
            if isinstance(message, dict):
                return [message]
            else:
                return [{"_original_message": message}]

    def is_chunk_message(self, message: Union[str, Dict[str, Any]]) -> bool:
        """判断是否是切片消息"""
        try:
            if isinstance(message, str):
                data = json.loads(message)
            else:
                data = message

            return (
                isinstance(data, dict)
                and "__mmc_chunk_info__" in data
                and "__mmc_chunk_data__" in data
                and "__mmc_is_chunked__" in data
            )
        except (json.JSONDecodeError, TypeError):
            return False


class MessageReassembler:
    """消息重组器，用于重组接收到的切片消息"""

    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self.chunk_buffers: Dict[str, Dict[str, Any]] = {}
        self._cleanup_task = None

    async def start_cleanup_task(self):
        """启动清理任务"""
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._cleanup_expired_chunks())

    async def stop_cleanup_task(self):
        """停止清理任务"""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None

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

    async def add_chunk(self, message: Union[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """
        添加切片，如果切片完整则返回重组后的消息

        Args:
            message: 切片消息（字符串或字典）

        Returns:
            如果切片完整则返回重组后的原始消息字典，否则返回None
        """
        try:
            # 统一转换为字典
            if isinstance(message, str):
                chunk_data = json.loads(message)
            else:
                chunk_data = message

            # 检查是否是切片消息
            if not chunker.is_chunk_message(chunk_data):
                # 不是切片消息，直接返回
                if "_original_message" in chunk_data:
                    # 这是一个被包装的非切片消息，解包返回
                    try:
                        return json.loads(chunk_data["_original_message"])
                    except json.JSONDecodeError:
                        return {"text_message": chunk_data["_original_message"]}
                else:
                    return chunk_data

            chunk_info = chunk_data["__mmc_chunk_info__"]
            chunk_content = chunk_data["__mmc_chunk_data__"]

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

                logger.debug(f"消息重组完成: {chunk_id} ({len(reassembled_message)} chars)")

                # 尝试反序列化重组后的消息
                try:
                    return json.loads(reassembled_message)
                except json.JSONDecodeError:
                    # 如果不能反序列化为JSON，则作为文本消息返回
                    return {"text_message": reassembled_message}

            return None

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.error(f"处理切片消息时出错: {e}")
            return None

    def get_pending_chunks_info(self) -> Dict[str, Any]:
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


# 全局实例
chunker = MessageChunker()
reassembler = MessageReassembler()
