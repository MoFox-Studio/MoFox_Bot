"""
重构后的聊天上下文管理器
提供统一、稳定的聊天上下文管理功能
每个 context_manager 实例只管理一个 stream 的上下文
"""

import asyncio
import time
from typing import Any

from src.chat.energy_system import energy_manager
from src.common.data_models.database_data_model import DatabaseMessages
from src.common.data_models.message_manager_data_model import StreamContext
from src.common.logger import get_logger
from src.config.config import global_config
from src.plugin_system.base.component_types import ChatType

from .distribution_manager import stream_loop_manager

logger = get_logger("context_manager")


class SingleStreamContextManager:
    """单流上下文管理器 - 每个实例只管理一个 stream 的上下文"""

    def __init__(self, stream_id: str, context: StreamContext, max_context_size: int | None = None):
        self.stream_id = stream_id
        self.context = context

        # 配置参数
        self.max_context_size = max_context_size or getattr(global_config.chat, "max_context_size", 100)
        self.context_ttl = getattr(global_config.chat, "context_ttl", 24 * 3600)  # 24小时

        # 元数据
        self.created_time = time.time()
        self.last_access_time = time.time()
        self.access_count = 0
        self.total_messages = 0

        logger.debug(f"单流上下文管理器初始化: {stream_id}")

    def get_context(self) -> StreamContext:
        """获取流上下文"""
        self._update_access_stats()
        return self.context

    async def add_message(self, message: DatabaseMessages, skip_energy_update: bool = False) -> bool:
        """添加消息到上下文

        Args:
            message: 消息对象
                skip_energy_update: 是否跳过能量更新（兼容参数，当前忽略）

        Returns:
            bool: 是否成功添加
        """
        try:
            # 使用MessageManager的内置缓存系统
            try:
                from .message_manager import message_manager

                # 如果MessageManager正在运行，使用缓存系统
                if message_manager.is_running:
                    # 先计算兴趣值（需要在缓存前计算）
                    await self._calculate_message_interest(message)
                    message.is_read = False

                    # 添加到缓存而不是直接添加到未读消息
                    cache_success = message_manager.add_message_to_cache(self.stream_id, message)

                    if cache_success:
                        # 自动检测和更新chat type
                        self._detect_chat_type(message)

                        self.total_messages += 1
                        self.last_access_time = time.time()

                        # 检查当前是否正在处理消息
                        is_processing = message_manager.get_stream_processing_status(self.stream_id)

                        if not is_processing:
                            # 如果当前没有在处理，立即刷新缓存到未读消息
                            cached_messages = message_manager.flush_cached_messages(self.stream_id)
                            for cached_msg in cached_messages:
                                self.context.unread_messages.append(cached_msg)
                            logger.debug(f"立即刷新缓存到未读消息: stream={self.stream_id}, 数量={len(cached_messages)}")
                        else:
                            logger.debug(f"消息已缓存，等待当前处理完成: stream={self.stream_id}")

                        # 启动流的循环任务（如果还未启动）
                        asyncio.create_task(stream_loop_manager.start_stream_loop(self.stream_id))
                        logger.debug(f"添加消息到缓存系统: {self.stream_id}")
                        return True
                    else:
                        logger.warning(f"消息缓存系统添加失败，回退到直接添加: {self.stream_id}")

            except ImportError:
                logger.debug("MessageManager不可用，使用直接添加模式")
            except Exception as e:
                logger.warning(f"消息缓存系统异常，回退到直接添加: {self.stream_id}, error={e}")

            # 回退方案：直接添加到未读消息
            message.is_read = False
            self.context.unread_messages.append(message)

            # 自动检测和更新chat type
            self._detect_chat_type(message)

            # 在上下文管理器中计算兴趣值
            await self._calculate_message_interest(message)
            self.total_messages += 1
            self.last_access_time = time.time()
            # 启动流的循环任务（如果还未启动）
            asyncio.create_task(stream_loop_manager.start_stream_loop(self.stream_id))
            logger.debug(f"添加消息{message.processed_plain_text}到单流上下文: {self.stream_id}")
            return True
        except Exception as e:
            logger.error(f"添加消息到单流上下文失败 {self.stream_id}: {e}", exc_info=True)
            return False

    async def update_message(self, message_id: str, updates: dict[str, Any]) -> bool:
        """更新上下文中的消息

        Args:
            message_id: 消息ID
            updates: 更新的属性

        Returns:
            bool: 是否成功更新
        """
        try:
            # 直接在未读消息中查找并更新
            for message in self.context.unread_messages:
                if message.message_id == message_id:
                    if "interest_value" in updates:
                        message.interest_value = updates["interest_value"]
                    if "actions" in updates:
                        message.actions = updates["actions"]
                    if "should_reply" in updates:
                        message.should_reply = updates["should_reply"]
                    break

            # 在历史消息中查找并更新
            for message in self.context.history_messages:
                if message.message_id == message_id:
                    if "interest_value" in updates:
                        message.interest_value = updates["interest_value"]
                    if "actions" in updates:
                        message.actions = updates["actions"]
                    if "should_reply" in updates:
                        message.should_reply = updates["should_reply"]
                    break

            logger.debug(f"更新单流上下文消息: {self.stream_id}/{message_id}")
            return True
        except Exception as e:
            logger.error(f"更新单流上下文消息失败 {self.stream_id}/{message_id}: {e}", exc_info=True)
            return False

    def get_messages(self, limit: int | None = None, include_unread: bool = True) -> list[DatabaseMessages]:
        """获取上下文消息

        Args:
            limit: 消息数量限制
            include_unread: 是否包含未读消息

        Returns:
            List[DatabaseMessages]: 消息列表
        """
        try:
            messages = []
            if include_unread:
                messages.extend(self.context.get_unread_messages())

            if limit:
                messages.extend(self.context.get_history_messages(limit=limit))
            else:
                messages.extend(self.context.get_history_messages())

            # 按时间排序
            messages.sort(key=lambda msg: getattr(msg, "time", 0))

            # 应用限制
            if limit and len(messages) > limit:
                messages = messages[-limit:]

            return messages

        except Exception as e:
            logger.error(f"获取单流上下文消息失败 {self.stream_id}: {e}", exc_info=True)
            return []

    def get_unread_messages(self) -> list[DatabaseMessages]:
        """获取未读消息"""
        try:
            return self.context.get_unread_messages()
        except Exception as e:
            logger.error(f"获取单流未读消息失败 {self.stream_id}: {e}", exc_info=True)
            return []

    def mark_messages_as_read(self, message_ids: list[str]) -> bool:
        """标记消息为已读"""
        try:
            if not hasattr(self.context, "mark_message_as_read"):
                logger.error(f"上下文对象缺少 mark_message_as_read 方法: {self.stream_id}")
                return False

            marked_count = 0
            for message_id in message_ids:
                try:
                    self.context.mark_message_as_read(message_id)
                    marked_count += 1
                except Exception as e:
                    logger.warning(f"标记消息已读失败 {message_id}: {e}")

            logger.debug(f"标记消息为已读: {self.stream_id} ({marked_count}/{len(message_ids)}条)")
            return marked_count > 0

        except Exception as e:
            logger.error(f"标记消息已读失败 {self.stream_id}: {e}", exc_info=True)
            return False

    async def clear_context(self) -> bool:
        """清空上下文"""
        try:
            if hasattr(self.context, "unread_messages"):
                self.context.unread_messages.clear()
            if hasattr(self.context, "history_messages"):
                self.context.history_messages.clear()
            reset_attrs = ["interruption_count", "afc_threshold_adjustment", "last_check_time"]
            for attr in reset_attrs:
                if hasattr(self.context, attr):
                    if attr in ["interruption_count", "afc_threshold_adjustment"]:
                        setattr(self.context, attr, 0)
                    else:
                        setattr(self.context, attr, time.time())
            await self._update_stream_energy()
            logger.info(f"清空单流上下文: {self.stream_id}")
            return True
        except Exception as e:
            logger.error(f"清空单流上下文失败 {self.stream_id}: {e}", exc_info=True)
            return False

    def get_statistics(self) -> dict[str, Any]:
        """获取流统计信息"""
        try:
            current_time = time.time()
            uptime = current_time - self.created_time

            unread_messages = getattr(self.context, "unread_messages", [])
            history_messages = getattr(self.context, "history_messages", [])

            return {
                "stream_id": self.stream_id,
                "context_type": type(self.context).__name__,
                "total_messages": len(history_messages) + len(unread_messages),
                "unread_messages": len(unread_messages),
                "history_messages": len(history_messages),
                "is_active": getattr(self.context, "is_active", True),
                "last_check_time": getattr(self.context, "last_check_time", current_time),
                "interruption_count": getattr(self.context, "interruption_count", 0),
                "afc_threshold_adjustment": getattr(self.context, "afc_threshold_adjustment", 0.0),
                "created_time": self.created_time,
                "last_access_time": self.last_access_time,
                "access_count": self.access_count,
                "uptime_seconds": uptime,
                "idle_seconds": current_time - self.last_access_time,
            }
        except Exception as e:
            logger.error(f"获取单流统计失败 {self.stream_id}: {e}", exc_info=True)
            return {}

    def validate_integrity(self) -> bool:
        """验证上下文完整性"""
        try:
            # 检查基本属性
            required_attrs = ["stream_id", "unread_messages", "history_messages"]
            for attr in required_attrs:
                if not hasattr(self.context, attr):
                    logger.warning(f"上下文缺少必要属性: {attr}")
                    return False

            # 检查消息ID唯一性
            all_messages = getattr(self.context, "unread_messages", []) + getattr(self.context, "history_messages", [])
            message_ids = [msg.message_id for msg in all_messages if hasattr(msg, "message_id")]
            if len(message_ids) != len(set(message_ids)):
                logger.warning(f"上下文中存在重复消息ID: {self.stream_id}")
                return False

            return True

        except Exception as e:
            logger.error(f"验证单流上下文完整性失败 {self.stream_id}: {e}")
            return False

    def _update_access_stats(self):
        """更新访问统计"""
        self.last_access_time = time.time()
        self.access_count += 1

    async def _calculate_message_interest(self, message: DatabaseMessages) -> float:
        """
        在上下文管理器中计算消息的兴趣度
        """
        try:
            from src.chat.interest_system.interest_manager import get_interest_manager

            interest_manager = get_interest_manager()

            if interest_manager.has_calculator():
                # 使用兴趣值计算组件计算
                result = await interest_manager.calculate_interest(message)

                if result.success:
                    # 更新消息对象的兴趣值相关字段
                    message.interest_value = result.interest_value
                    message.should_reply = result.should_reply
                    message.should_act = result.should_act

                    logger.debug(
                        f"消息 {message.message_id} 兴趣值已更新: {result.interest_value:.3f}, "
                        f"should_reply: {result.should_reply}, should_act: {result.should_act}"
                    )
                    return result.interest_value
                else:
                    logger.warning(f"消息 {message.message_id} 兴趣值计算失败: {result.error_message}")
                    return 0.5
            else:
                logger.debug("未找到兴趣值计算器，使用默认兴趣值")
                return 0.5

        except Exception as e:
            logger.error(f"计算消息兴趣度时发生错误: {e}", exc_info=True)
            return 0.5

    def _detect_chat_type(self, message: DatabaseMessages):
        """根据消息内容自动检测聊天类型"""
        # 只有在第一次添加消息时才检测聊天类型，避免后续消息改变类型
        if len(self.context.unread_messages) == 1:  # 只有这条消息
            # 如果消息包含群组信息，则为群聊
            if message.chat_info.group_info:
                self.context.chat_type = ChatType.GROUP
            else:
                self.context.chat_type = ChatType.PRIVATE

    async def clear_context_async(self) -> bool:
        """异步实现的 clear_context：清空消息并 await 能量重算。"""
        try:
            if hasattr(self.context, "unread_messages"):
                self.context.unread_messages.clear()
            if hasattr(self.context, "history_messages"):
                self.context.history_messages.clear()

            reset_attrs = ["interruption_count", "afc_threshold_adjustment", "last_check_time"]
            for attr in reset_attrs:
                if hasattr(self.context, attr):
                    if attr in ["interruption_count", "afc_threshold_adjustment"]:
                        setattr(self.context, attr, 0)
                    else:
                        setattr(self.context, attr, time.time())

            await self._update_stream_energy()
            logger.info(f"清空单流上下文(异步): {self.stream_id}")
            return True
        except Exception as e:
            logger.error(f"清空单流上下文失败 (async) {self.stream_id}: {e}", exc_info=True)
            return False

    async def refresh_focus_energy_from_history(self) -> None:
        """基于历史消息刷新聚焦能量"""
        await self._update_stream_energy(include_unread=False)

    async def _update_stream_energy(self, include_unread: bool = False) -> None:
        """更新流能量"""
        try:
            history_messages = self.context.get_history_messages(limit=self.max_context_size)
            messages: list[DatabaseMessages] = list(history_messages)

            if include_unread:
                messages.extend(self.get_unread_messages())

            # 获取用户ID（优先使用最新历史消息）
            user_id = None
            if messages:
                last_message = messages[-1]
                if hasattr(last_message, "user_info") and last_message.user_info:
                    user_id = last_message.user_info.user_id

            await energy_manager.calculate_focus_energy(
                stream_id=self.stream_id,
                messages=messages,
                user_id=user_id,
            )

        except Exception as e:
            logger.error(f"更新单流能量失败 {self.stream_id}: {e}")
