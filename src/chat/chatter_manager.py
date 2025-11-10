import asyncio
import time
from typing import Any

from src.chat.planner_actions.action_manager import ChatterActionManager
from src.common.data_models.message_manager_data_model import StreamContext
from src.common.logger import get_logger
from src.plugin_system.base.base_chatter import BaseChatter
from src.plugin_system.base.component_types import ChatType

logger = get_logger("chatter_manager")


class ChatterManager:
    def __init__(self, action_manager: ChatterActionManager):
        self.action_manager = action_manager
        self.chatter_classes: dict[ChatType, list[type]] = {}
        self.instances: dict[str, BaseChatter] = {}

        # 管理器统计
        self.stats = {
            "chatters_registered": 0,
            "streams_processed": 0,
            "successful_executions": 0,
            "failed_executions": 0,
        }

    def _auto_register_from_component_registry(self):
        """从组件注册表自动注册已注册的chatter组件"""
        try:
            from src.plugin_system.core.component_registry import component_registry

            # 获取所有CHATTER类型的组件
            chatter_components = component_registry.get_enabled_chatter_registry()
            for chatter_name, chatter_class in chatter_components.items():
                self.register_chatter(chatter_class)
                logger.info(f"自动注册chatter组件: {chatter_name}")
        except Exception as e:
            logger.warning(f"自动注册chatter组件时发生错误: {e}")

    def register_chatter(self, chatter_class: type):
        """注册聊天处理器类"""
        for chat_type in chatter_class.chat_types:
            if chat_type not in self.chatter_classes:
                self.chatter_classes[chat_type] = []
            self.chatter_classes[chat_type].append(chatter_class)
            logger.info(f"注册聊天处理器 {chatter_class.__name__} 支持 {chat_type.value} 聊天类型")

        self.stats["chatters_registered"] += 1

    def get_chatter_class(self, chat_type: ChatType) -> type | None:
        """获取指定聊天类型的聊天处理器类"""
        if chat_type in self.chatter_classes:
            return self.chatter_classes[chat_type][0]
        return None

    def get_supported_chat_types(self) -> list[ChatType]:
        """获取支持的聊天类型列表"""
        return list(self.chatter_classes.keys())

    def get_registered_chatters(self) -> dict[ChatType, list[type]]:
        """获取已注册的聊天处理器"""
        return self.chatter_classes.copy()

    def get_stream_instance(self, stream_id: str) -> BaseChatter | None:
        """获取指定流的聊天处理器实例"""
        return self.instances.get(stream_id)

    def cleanup_inactive_instances(self, max_inactive_minutes: int = 60):
        """清理不活跃的实例"""
        current_time = time.time()
        max_inactive_seconds = max_inactive_minutes * 60

        inactive_streams = []
        for stream_id, instance in self.instances.items():
            if hasattr(instance, "get_activity_time"):
                activity_time = instance.get_activity_time()
                if (current_time - activity_time) > max_inactive_seconds:
                    inactive_streams.append(stream_id)

        for stream_id in inactive_streams:
            del self.instances[stream_id]
            logger.info(f"清理不活跃聊天流实例: {stream_id}")

    async def process_stream_context(self, stream_id: str, context: StreamContext) -> dict:
        """处理流上下文"""
        chat_type = context.chat_type
        logger.debug(f"处理流 {stream_id}，聊天类型: {chat_type.value}")
        if not self.chatter_classes:
            self._auto_register_from_component_registry()

        # 获取适合该聊天类型的chatter
        chatter_class = self.get_chatter_class(chat_type)
        if not chatter_class:
            # 如果没有找到精确匹配，尝试查找支持ALL类型的chatter
            from src.plugin_system.base.component_types import ChatType

            all_chatter_class = self.get_chatter_class(ChatType.ALL)
            if all_chatter_class:
                chatter_class = all_chatter_class
                logger.info(f"流 {stream_id} 使用通用chatter (类型: {chat_type.value})")
            else:
                raise ValueError(f"No chatter registered for chat type {chat_type}")

        if stream_id not in self.instances:
            self.instances[stream_id] = chatter_class(stream_id=stream_id, action_manager=self.action_manager)
            logger.info(f"创建新的聊天流实例: {stream_id} 使用 {chatter_class.__name__} (类型: {chat_type.value})")

        self.stats["streams_processed"] += 1
        try:
            result = await self.instances[stream_id].execute(context)

            # 检查执行结果是否真正成功
            success = result.get("success", False)

            if success:
                self.stats["successful_executions"] += 1

                # 只有真正成功时才清空未读消息
                try:
                    from src.chat.message_manager.message_manager import message_manager
                    await message_manager.clear_stream_unread_messages(stream_id)
                    logger.debug(f"流 {stream_id} 处理成功，已清空未读消息")
                except Exception as clear_e:
                    logger.error(f"清除流 {stream_id} 未读消息时发生错误: {clear_e}")
            else:
                self.stats["failed_executions"] += 1
                logger.warning(f"流 {stream_id} 处理失败，不清空未读消息")

            # 从 mood_manager 获取最新的 chat_stream 并同步回 StreamContext
            try:
                from src.mood.mood_manager import mood_manager

                mood = mood_manager.get_mood_by_chat_id(stream_id)
                if mood and mood.chat_stream:
                    context.chat_stream = mood.chat_stream
                    logger.debug(f"已将最新的 chat_stream 同步回流 {stream_id} 的 StreamContext")
            except Exception as sync_e:
                logger.error(f"同步 chat_stream 回 StreamContext 失败: {sync_e}")

            # 记录处理结果
            actions_count = result.get("actions_count", 0)
            logger.debug(f"流 {stream_id} 处理完成: 成功={success}, 动作数={actions_count}")

            return result
        except asyncio.CancelledError:
            self.stats["failed_executions"] += 1
            logger.info(f"流 {stream_id} 处理被取消")
            context.triggering_user_id = None  # 清除触发用户ID
            # 确保清理 processing_message_id 以防止重复回复检测失效
            context.processing_message_id = None
            raise
        except Exception as e:
            self.stats["failed_executions"] += 1
            logger.error(f"处理流 {stream_id} 时发生错误: {e}")
            context.triggering_user_id = None  # 清除触发用户ID
            # 确保清理 processing_message_id
            context.processing_message_id = None
            raise
        finally:
            # 清除触发用户ID（所有情况下都需要）
            context.triggering_user_id = None
    def get_stats(self) -> dict[str, Any]:
        """获取管理器统计信息"""
        stats = self.stats.copy()
        stats["active_instances"] = len(self.instances)
        stats["registered_chatter_types"] = len(self.chatter_classes)
        return stats

    def reset_stats(self):
        """重置统计信息"""
        self.stats = {
            "chatters_registered": 0,
            "streams_processed": 0,
            "successful_executions": 0,
            "failed_executions": 0,
        }
