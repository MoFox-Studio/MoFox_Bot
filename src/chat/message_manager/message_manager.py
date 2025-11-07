"""
消息管理模块
管理每个聊天流的上下文信息，包含历史记录和未读消息，定期检查并处理新消息
"""

import asyncio
import time
from collections import defaultdict, deque
from typing import TYPE_CHECKING, Any

from src.chat.chatter_manager import ChatterManager
from src.chat.planner_actions.action_manager import ChatterActionManager
from src.common.data_models.database_data_model import DatabaseMessages
from src.common.data_models.message_manager_data_model import MessageManagerStats, StreamStats
from src.common.logger import get_logger
from src.config.config import global_config
from src.plugin_system.apis.chat_api import get_chat_manager

from .global_notice_manager import NoticeScope, global_notice_manager
from .scheduler_dispatcher import scheduler_dispatcher

if TYPE_CHECKING:
    pass

logger = get_logger("message_manager")


class MessageManager:
    """消息管理器"""

    def __init__(self, check_interval: float = 5.0):
        self.check_interval = check_interval  # 检查间隔（秒）
        self.is_running = False
        self.manager_task: asyncio.Task | None = None

        # 统计信息
        self.stats = MessageManagerStats()

        # 初始化chatter manager
        self.action_manager = ChatterActionManager()
        self.chatter_manager = ChatterManager(self.action_manager)

        # 消息缓存系统 - 直接集成到消息管理器
        self.message_caches: dict[str, deque] = defaultdict(deque)  # 每个流的消息缓存
        self.stream_processing_status: dict[str, bool] = defaultdict(bool)  # 流的处理状态
        self.cache_stats = {
            "total_cached_messages": 0,
            "total_flushed_messages": 0,
        }

        # 不再需要全局上下文管理器，直接通过 ChatManager 访问各个 ChatStream 的 context_manager

        # 全局Notice管理器
        self.notice_manager = global_notice_manager

    async def start(self):
        """启动消息管理器"""
        if self.is_running:
            logger.warning("消息管理器已经在运行")
            return

        self.is_running = True

        # 启动批量数据库写入器
        try:
            from src.chat.message_manager.batch_database_writer import init_batch_writer

            await init_batch_writer()
        except Exception as e:
            logger.error(f"启动批量数据库写入器失败: {e}")

        # 启动消息缓存系统（内置）
        logger.debug("消息缓存系统已启动")

        # 启动基于 scheduler 的消息分发器
        await scheduler_dispatcher.start()
        scheduler_dispatcher.set_chatter_manager(self.chatter_manager)

        # 保留旧的流循环管理器（暂时）以便平滑过渡
        # TODO: 在确认新机制稳定后移除
        # await stream_loop_manager.start()
        # stream_loop_manager.set_chatter_manager(self.chatter_manager)

        logger.info("消息管理器已启动（使用 Scheduler 分发器）")

    async def stop(self):
        """停止消息管理器"""
        if not self.is_running:
            return

        self.is_running = False

        # 停止批量数据库写入器
        try:
            from src.chat.message_manager.batch_database_writer import shutdown_batch_writer

            await shutdown_batch_writer()
            logger.debug("批量数据库写入器已停止")
        except Exception as e:
            logger.error(f"停止批量数据库写入器失败: {e}")

        # 停止消息缓存系统（内置）
        self.message_caches.clear()
        self.stream_processing_status.clear()
        logger.debug("消息缓存系统已停止")

        # 停止基于 scheduler 的消息分发器
        await scheduler_dispatcher.stop()

        # 停止旧的流循环管理器（如果启用）
        # await stream_loop_manager.stop()

        logger.info("消息管理器已停止")

    async def add_message(self, stream_id: str, message: DatabaseMessages):
        """添加消息到指定聊天流

        新的流程：
        1. 检查 notice 消息
        2. 将消息添加到上下文（缓存）
        3. 通知 scheduler_dispatcher 处理（检查打断、创建/更新 schedule）
        """

        try:
            # 硬编码过滤表情包消息
            if message.processed_plain_text and message.processed_plain_text.startswith("[表情包"):
                logger.info(f"检测到表情包消息，已过滤: {message.processed_plain_text}")
                return

            # 检查是否为notice消息
            if self._is_notice_message(message):
                # Notice消息处理 - 添加到全局管理器
                logger.debug(f"检测到notice消息: notice_type={getattr(message, 'notice_type', None)}")
                await self._handle_notice_message(stream_id, message)

                # 根据配置决定是否继续处理（触发聊天流程）
                if not global_config.notice.enable_notice_trigger_chat:
                    logger.debug(f"Notice消息将被忽略，不触发聊天流程: {stream_id}")
                    return  # 停止处理，不进入未读消息队列
                else:
                    logger.debug(f"Notice消息将触发聊天流程: {stream_id}")
                    # 继续执行，将消息添加到未读队列

            # 普通消息处理
            chat_manager = get_chat_manager()
            chat_stream = await chat_manager.get_stream(stream_id)
            if not chat_stream:
                logger.warning(f"MessageManager.add_message: 聊天流 {stream_id} 不存在")
                return

            # 将消息添加到上下文
            await chat_stream.context_manager.add_message(message)

            # 通知 scheduler_dispatcher 处理消息接收事件
            # dispatcher 会检查是否需要打断、创建或更新 schedule
            await scheduler_dispatcher.on_message_received(stream_id)

        except Exception as e:
            logger.error(f"添加消息到聊天流 {stream_id} 时发生错误: {e}")

    async def update_message(
        self,
        stream_id: str,
        message_id: str,
        interest_value: float | None = None,
        actions: list | None = None,
        should_reply: bool | None = None,
    ):
        """更新消息信息"""
        try:
            chat_manager = get_chat_manager()
            chat_stream = await chat_manager.get_stream(stream_id)
            if not chat_stream:
                logger.warning(f"MessageManager.update_message: 聊天流 {stream_id} 不存在")
                return
            updates = {}
            if interest_value is not None:
                updates["interest_value"] = interest_value
            if actions is not None:
                updates["actions"] = actions
            if should_reply is not None:
                updates["should_reply"] = should_reply
            if updates:
                success = await chat_stream.context_manager.update_message(message_id, updates)
                if success:
                    logger.debug(f"更新消息 {message_id} 成功")
                else:
                    logger.warning(f"更新消息 {message_id} 失败")
        except Exception as e:
            logger.error(f"更新消息 {message_id} 时发生错误: {e}")


    async def add_action(self, stream_id: str, message_id: str, action: str):
        """添加动作到消息"""
        try:
            chat_manager = get_chat_manager()
            chat_stream = await chat_manager.get_stream(stream_id)
            if not chat_stream:
                logger.warning(f"MessageManager.add_action: 聊天流 {stream_id} 不存在")
                return
            success = await chat_stream.context_manager.update_message(message_id, {"actions": [action]})
            if success:
                logger.debug(f"为消息 {message_id} 添加动作 {action} 成功")
            else:
                logger.warning(f"为消息 {message_id} 添加动作 {action} 失败")
        except Exception as e:
            logger.error(f"为消息 {message_id} 添加动作时发生错误: {e}")

    async def deactivate_stream(self, stream_id: str):
        """停用聊天流"""
        try:
            # 通过 ChatManager 获取 ChatStream
            chat_manager = get_chat_manager()
            chat_stream = await chat_manager.get_stream(stream_id)
            if not chat_stream:
                logger.warning(f"停用流失败: 聊天流 {stream_id} 不存在")
                return

            context = chat_stream.context_manager.context
            context.is_active = False

            # 取消处理任务
            if hasattr(context, "processing_task") and context.processing_task and not context.processing_task.done():
                context.processing_task.cancel()

            logger.debug(f"停用聊天流: {stream_id}")

        except Exception as e:
            logger.error(f"停用聊天流 {stream_id} 时发生错误: {e}")

    async def activate_stream(self, stream_id: str):
        """激活聊天流"""
        try:
            # 通过 ChatManager 获取 ChatStream
            chat_manager = get_chat_manager()
            chat_stream = await chat_manager.get_stream(stream_id)
            if not chat_stream:
                logger.warning(f"激活流失败: 聊天流 {stream_id} 不存在")
                return

            context = chat_stream.context_manager.context
            context.is_active = True
            logger.debug(f"激活聊天流: {stream_id}")

        except Exception as e:
            logger.error(f"激活聊天流 {stream_id} 时发生错误: {e}")

    async def get_stream_stats(self, stream_id: str) -> StreamStats | None:
        """获取聊天流统计"""
        try:
            # 通过 ChatManager 获取 ChatStream
            chat_manager = get_chat_manager()
            chat_stream = await chat_manager.get_stream(stream_id)
            if not chat_stream:
                return None

            context = chat_stream.context_manager.context
            unread_count = len(chat_stream.context_manager.get_unread_messages())

            return StreamStats(
                stream_id=stream_id,
                is_active=context.is_active,
                unread_count=unread_count,
                history_count=len(context.history_messages),
                last_check_time=context.last_check_time,
                has_active_task=bool(
                    hasattr(context, "processing_task")
                    and context.processing_task
                    and not context.processing_task.done()
                ),
            )

        except Exception as e:
            logger.error(f"获取聊天流 {stream_id} 统计时发生错误: {e}")
            return None

    def get_manager_stats(self) -> dict[str, Any]:
        """获取管理器统计"""
        return {
            "total_streams": self.stats.total_streams,
            "active_streams": self.stats.active_streams,
            "total_unread_messages": self.stats.total_unread_messages,
            "total_processed_messages": self.stats.total_processed_messages,
            "uptime": self.stats.uptime,
            "start_time": self.stats.start_time,
        }

    async def cleanup_inactive_streams(self, max_inactive_hours: int = 24):
        """清理不活跃的聊天流"""
        try:
            chat_manager = get_chat_manager()
            current_time = time.time()
            max_inactive_seconds = max_inactive_hours * 3600
            inactive_streams = []
            for stream_id, chat_stream in chat_manager.streams.items():
                if current_time - chat_stream.last_active_time > max_inactive_seconds:
                    inactive_streams.append(stream_id)
            for stream_id in inactive_streams:
                try:
                    # 在使用之前重新从 chat_manager 中获取 chat_stream，避免引用未定义或过期的变量
                    chat_stream = chat_manager.streams.get(stream_id)
                    if not chat_stream:
                        logger.debug(f"聊天流 {stream_id} 在清理时已不存在，跳过")
                        continue

                    await chat_stream.context_manager.clear_context()

                    # 安全删除流（若已被其他地方删除则捕获）
                    try:
                        del chat_manager.streams[stream_id]
                    except KeyError:
                        logger.debug(f"删除聊天流 {stream_id} 时未找到，可能已被移除")

                    logger.info(f"清理不活跃聊天流: {stream_id}")
                except Exception as e:
                    logger.error(f"清理聊天流 {stream_id} 失败: {e}")
            if inactive_streams:
                logger.info(f"已清理 {len(inactive_streams)} 个不活跃聊天流")
            else:
                logger.debug("没有需要清理的不活跃聊天流")
        except Exception as e:
            logger.error(f"清理不活跃聊天流时发生错误: {e}")

    # === 已废弃的方法已移除 ===
    # _check_and_handle_interruption 和 _trigger_reprocess 已由 scheduler_dispatcher 接管
    # 如需查看历史代码，请参考 git 历史记录

    async def clear_all_unread_messages(self, stream_id: str):
        """清除指定上下文中的所有未读消息，在消息处理完成后调用"""
        try:
            # 通过 ChatManager 获取 ChatStream
            chat_manager = get_chat_manager()
            chat_stream = await chat_manager.get_stream(stream_id)
            if not chat_stream:
                logger.warning(f"清除消息失败: 聊天流 {stream_id} 不存在")
                return

            # 获取未读消息
            unread_messages = chat_stream.context_manager.get_unread_messages()
            if not unread_messages:
                return

            logger.warning(f"正在清除 {len(unread_messages)} 条未读消息")

            # 将所有未读消息标记为已读
            message_ids = [msg.message_id for msg in unread_messages]
            success = chat_stream.context_manager.mark_messages_as_read(message_ids)

            if success:
                self.stats.total_processed_messages += len(unread_messages)
                logger.debug(f"强制清除 {len(unread_messages)} 条消息，标记为已读")
            else:
                logger.error("标记未读消息为已读失败")

        except Exception as e:
            logger.error(f"清除未读消息时发生错误: {e}")

    async def clear_stream_unread_messages(self, stream_id: str):
        """清除指定聊天流的所有未读消息"""
        try:
            chat_manager = get_chat_manager()
            chat_stream = await chat_manager.get_stream(stream_id)
            if not chat_stream:
                logger.warning(f"clear_stream_unread_messages: 聊天流 {stream_id} 不存在")
                return

            context = chat_stream.context_manager.context
            if hasattr(context, "unread_messages") and context.unread_messages:
                logger.debug(f"正在为流 {stream_id} 清除 {len(context.unread_messages)} 条未读消息")
                context.unread_messages.clear()
            else:
                logger.debug(f"流 {stream_id} 没有需要清除的未读消息")

        except Exception as e:
            logger.error(f"清除流 {stream_id} 的未读消息时发生错误: {e}")

    # ===== 消息缓存系统方法 =====

    def add_message_to_cache(self, stream_id: str, message: DatabaseMessages) -> bool:
        """添加消息到缓存

        Args:
            stream_id: 流ID
            message: 消息对象

        Returns:
            bool: 是否成功添加到缓存
        """
        try:
            if not self.is_running:
                return False

            self.message_caches[stream_id].append(message)
            self.cache_stats["total_cached_messages"] += 1

            if message.processed_plain_text:
                logger.debug(f"消息已添加到缓存: stream={stream_id}, content={message.processed_plain_text[:50]}...")
            return True
        except Exception as e:
            logger.error(f"添加消息到缓存失败: stream={stream_id}, error={e}")
            return False

    def flush_cached_messages(self, stream_id: str) -> list[DatabaseMessages]:
        """刷新缓存消息到未读消息列表

        Args:
            stream_id: 流ID

        Returns:
            List[DatabaseMessages]: 缓存的消息列表
        """
        try:
            if stream_id not in self.message_caches:
                return []

            cached_messages = list(self.message_caches[stream_id])
            self.message_caches[stream_id].clear()

            self.cache_stats["total_flushed_messages"] += len(cached_messages)

            logger.debug(f"刷新缓存消息: stream={stream_id}, 数量={len(cached_messages)}")
            return cached_messages
        except Exception as e:
            logger.error(f"刷新缓存消息失败: stream={stream_id}, error={e}")
            return []

    def set_stream_processing_status(self, stream_id: str, is_processing: bool):
        """设置流的处理状态

        Args:
            stream_id: 流ID
            is_processing: 是否正在处理
        """
        try:
            self.stream_processing_status[stream_id] = is_processing
            logger.debug(f"设置流处理状态: stream={stream_id}, processing={is_processing}")
        except Exception as e:
            logger.error(f"设置流处理状态失败: stream={stream_id}, error={e}")

    def get_stream_processing_status(self, stream_id: str) -> bool:
        """获取流的处理状态

        Args:
            stream_id: 流ID

        Returns:
            bool: 是否正在处理
        """
        return self.stream_processing_status.get(stream_id, False)

    def has_cached_messages(self, stream_id: str) -> bool:
        """检查流是否有缓存消息

        Args:
            stream_id: 流ID

        Returns:
            bool: 是否有缓存消息
        """
        return stream_id in self.message_caches and len(self.message_caches[stream_id]) > 0

    def get_cached_messages_count(self, stream_id: str) -> int:
        """获取流的缓存消息数量

        Args:
            stream_id: 流ID

        Returns:
            int: 缓存消息数量
        """
        return len(self.message_caches.get(stream_id, []))

    def get_cache_stats(self) -> dict[str, Any]:
        """获取缓存统计信息

        Returns:
            Dict[str, Any]: 缓存统计信息
        """
        return {
            "total_cached_messages": self.cache_stats["total_cached_messages"],
            "total_flushed_messages": self.cache_stats["total_flushed_messages"],
            "active_caches": len(self.message_caches),
            "cached_streams": len([s for s in self.message_caches.keys() if self.message_caches[s]]),
            "processing_streams": len([s for s in self.stream_processing_status.keys() if self.stream_processing_status[s]]),
        }

    # ===== Notice管理相关方法 =====

    def _is_notice_message(self, message: DatabaseMessages) -> bool:
        """检查消息是否为notice类型"""
        try:
            # 首先检查消息的is_notify字段
            if hasattr(message, "is_notify") and message.is_notify:
                return True

            # 检查消息的附加配置
            if hasattr(message, "additional_config") and message.additional_config:
                if isinstance(message.additional_config, dict):
                    return message.additional_config.get("is_notice", False)
                elif isinstance(message.additional_config, str):
                    # 兼容JSON字符串格式
                    import json
                    config = json.loads(message.additional_config)
                    return config.get("is_notice", False)

            return False

        except Exception as e:
            logger.debug(f"检查notice类型失败: {e}")
            return False

    async def _handle_notice_message(self, stream_id: str, message: DatabaseMessages) -> None:
        """处理notice消息，将其添加到全局notice管理器"""
        try:
            # 获取notice作用域
            scope = self._determine_notice_scope(message, stream_id)

            # 添加到全局notice管理器
            success = self.notice_manager.add_notice(
                message=message,
                scope=scope,
                target_stream_id=stream_id if scope == NoticeScope.STREAM else None,
                ttl=self._get_notice_ttl(message)
            )

            if success:
                logger.info(f"✅ Notice消息已添加到全局管理器: message_id={message.message_id}, scope={scope.value}, stream={stream_id}, ttl={self._get_notice_ttl(message)}s")
            else:
                logger.warning(f"❌ Notice消息添加失败: message_id={message.message_id}")

        except Exception as e:
            logger.error(f"处理notice消息失败: {e}")

    def _determine_notice_scope(self, message: DatabaseMessages, stream_id: str) -> NoticeScope:
        """确定notice的作用域

        作用域完全由 additional_config 中的 is_public_notice 字段决定：
        - is_public_notice=True: 公共notice，所有聊天流可见
        - is_public_notice=False 或未设置: 特定聊天流notice
        """
        try:
            # 检查附加配置中的公共notice标志
            if hasattr(message, "additional_config") and message.additional_config:
                if isinstance(message.additional_config, dict):
                    is_public = message.additional_config.get("is_public_notice", False)
                elif isinstance(message.additional_config, str):
                    import json
                    config = json.loads(message.additional_config)
                    is_public = config.get("is_public_notice", False)
                else:
                    is_public = False

                if is_public:
                    logger.debug(f"Notice被标记为公共: message_id={message.message_id}")
                    return NoticeScope.PUBLIC

            # 默认为特定聊天流notice
            return NoticeScope.STREAM

        except Exception as e:
            logger.debug(f"确定notice作用域失败: {e}")
            return NoticeScope.STREAM

    def _get_notice_type(self, message: DatabaseMessages) -> str | None:
        """获取notice类型"""
        try:
            if hasattr(message, "additional_config") and message.additional_config:
                if isinstance(message.additional_config, dict):
                    return message.additional_config.get("notice_type")
                elif isinstance(message.additional_config, str):
                    import json
                    config = json.loads(message.additional_config)
                    return config.get("notice_type")
            return None
        except Exception:
            return None

    def _get_notice_ttl(self, message: DatabaseMessages) -> int:
        """获取notice的生存时间"""
        try:
            # 根据notice类型设置不同的TTL
            notice_type = self._get_notice_type(message)
            if notice_type is None:
                return 3600

            ttl_mapping = {
                "poke": 1800,  # 戳一戳30分钟
                "emoji_like": 3600,  # 表情回复1小时
                "group_ban": 7200,  # 禁言2小时
                "group_lift_ban": 7200,  # 解禁2小时
                "group_whole_ban": 3600,  # 全体禁言1小时
                "group_whole_lift_ban": 3600,  # 解除全体禁言1小时
            }

            return ttl_mapping.get(notice_type, 3600)  # 默认1小时

        except Exception:
            return 3600

    def get_notice_text(self, stream_id: str, limit: int = 10) -> str:
        """获取指定聊天流的notice文本，用于构建提示词"""
        try:
            return self.notice_manager.get_notice_text(stream_id, limit)
        except Exception as e:
            logger.error(f"获取notice文本失败: {e}")
            return ""

    def clear_notices(self, stream_id: str | None = None, notice_type: str | None = None) -> int:
        """清理notice消息"""
        try:
            return self.notice_manager.clear_notices(stream_id, notice_type)
        except Exception as e:
            logger.error(f"清理notice失败: {e}")
            return 0

    def get_notice_stats(self) -> dict[str, Any]:
        """获取notice管理器统计信息"""
        try:
            return self.notice_manager.get_stats()
        except Exception as e:
            logger.error(f"获取notice统计失败: {e}")
            return {}


# 创建全局消息管理器实例
message_manager = MessageManager()
