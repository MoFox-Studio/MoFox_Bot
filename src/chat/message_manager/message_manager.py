"""
消息管理模块
管理每个聊天流的上下文信息，包含历史记录和未读消息，定期检查并处理新消息
"""

import asyncio
import random
import time
from collections import defaultdict, deque
from typing import TYPE_CHECKING, Any, Dict

from src.chat.chatter_manager import ChatterManager
from src.chat.message_receive.chat_stream import ChatStream
from src.chat.planner_actions.action_manager import ChatterActionManager
from src.common.data_models.database_data_model import DatabaseMessages
from src.common.data_models.message_manager_data_model import MessageManagerStats, StreamStats
from src.common.logger import get_logger
from src.config.config import global_config
from src.plugin_system.apis.chat_api import get_chat_manager

from .distribution_manager import stream_loop_manager
from .sleep_system.state_manager import SleepState, sleep_state_manager


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
        self.message_caches: Dict[str, deque] = defaultdict(deque)  # 每个流的消息缓存
        self.stream_processing_status: Dict[str, bool] = defaultdict(bool)  # 流的处理状态
        self.cache_stats = {
            "total_cached_messages": 0,
            "total_flushed_messages": 0,
        }

        # 不再需要全局上下文管理器，直接通过 ChatManager 访问各个 ChatStream 的 context_manager

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

        # 启动流缓存管理器
        try:
            from src.chat.message_manager.stream_cache_manager import init_stream_cache_manager

            await init_stream_cache_manager()
        except Exception as e:
            logger.error(f"启动流缓存管理器失败: {e}")

        # 启动消息缓存系统（内置）
        logger.info("📦 消息缓存系统已启动")

        # 启动自适应流管理器
        try:
            from src.chat.message_manager.adaptive_stream_manager import init_adaptive_stream_manager

            await init_adaptive_stream_manager()
            logger.info("🎯 自适应流管理器已启动")
        except Exception as e:
            logger.error(f"启动自适应流管理器失败: {e}")

        # 启动睡眠和唤醒管理器
        # 睡眠系统的定时任务启动移至 main.py

        # 启动流循环管理器并设置chatter_manager
        await stream_loop_manager.start()
        stream_loop_manager.set_chatter_manager(self.chatter_manager)

        logger.info("🚀 消息管理器已启动 | 流循环管理器已启动")

    async def stop(self):
        """停止消息管理器"""
        if not self.is_running:
            return

        self.is_running = False

        # 停止批量数据库写入器
        try:
            from src.chat.message_manager.batch_database_writer import shutdown_batch_writer

            await shutdown_batch_writer()
            logger.info("📦 批量数据库写入器已停止")
        except Exception as e:
            logger.error(f"停止批量数据库写入器失败: {e}")

        # 停止流缓存管理器
        try:
            from src.chat.message_manager.stream_cache_manager import shutdown_stream_cache_manager

            await shutdown_stream_cache_manager()
            logger.info("🗄️ 流缓存管理器已停止")
        except Exception as e:
            logger.error(f"停止流缓存管理器失败: {e}")

        # 停止消息缓存系统（内置）
        self.message_caches.clear()
        self.stream_processing_status.clear()
        logger.info("📦 消息缓存系统已停止")

        # 停止自适应流管理器
        try:
            from src.chat.message_manager.adaptive_stream_manager import shutdown_adaptive_stream_manager

            await shutdown_adaptive_stream_manager()
            logger.info("🎯 自适应流管理器已停止")
        except Exception as e:
            logger.error(f"停止自适应流管理器失败: {e}")


        # 停止流循环管理器
        await stream_loop_manager.stop()

        logger.info("🛑 消息管理器已停止 | 流循环管理器已停止")

    async def add_message(self, stream_id: str, message: DatabaseMessages):
        """添加消息到指定聊天流"""
        # 在消息处理的最前端检查睡眠状态
        current_sleep_state = sleep_state_manager.get_current_state()
        if current_sleep_state == SleepState.SLEEPING:
            logger.info(f"处于 {current_sleep_state.name} 状态，消息被拦截。")
            return  # 直接返回，不处理消息

        # TODO: 在这里为 WOKEN_UP_ANGRY 等未来状态添加特殊处理逻辑

        try:
            chat_manager = get_chat_manager()
            chat_stream = await chat_manager.get_stream(stream_id)
            if not chat_stream:
                logger.warning(f"MessageManager.add_message: 聊天流 {stream_id} 不存在")
                return
            await self._check_and_handle_interruption(chat_stream)
            await chat_stream.context_manager.add_message(message)

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

    async def bulk_update_messages(self, stream_id: str, updates: list[dict[str, Any]]) -> int:
        """批量更新消息信息，降低更新频率"""
        if not updates:
            return 0

        try:
            chat_manager = get_chat_manager()
            chat_stream = await chat_manager.get_stream(stream_id)
            if not chat_stream:
                logger.warning(f"MessageManager.bulk_update_messages: 聊天流 {stream_id} 不存在")
                return 0

            updated_count = 0
            for item in updates:
                message_id = item.get("message_id")
                if not message_id:
                    continue

                payload = {key: value for key, value in item.items() if key != "message_id" and value is not None}

                if not payload:
                    continue

                success = await chat_stream.context_manager.update_message(message_id, payload)
                if success:
                    updated_count += 1

            if updated_count:
                logger.debug(f"批量更新消息 {updated_count} 条 (stream={stream_id})")
            return updated_count
        except Exception as e:
            logger.error(f"批量更新聊天流 {stream_id} 消息失败: {e}")
            return 0

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

            context = chat_stream.stream_context
            context.is_active = False

            # 取消处理任务
            if hasattr(context, "processing_task") and context.processing_task and not context.processing_task.done():
                context.processing_task.cancel()

            logger.info(f"停用聊天流: {stream_id}")

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

            context = chat_stream.stream_context
            context.is_active = True
            logger.info(f"激活聊天流: {stream_id}")

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

            context = chat_stream.stream_context
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
                    await chat_stream.context_manager.clear_context()
                    del chat_manager.streams[stream_id]
                    logger.info(f"清理不活跃聊天流: {stream_id}")
                except Exception as e:
                    logger.error(f"清理聊天流 {stream_id} 失败: {e}")
            if inactive_streams:
                logger.info(f"已清理 {len(inactive_streams)} 个不活跃聊天流")
            else:
                logger.debug("没有需要清理的不活跃聊天流")
        except Exception as e:
            logger.error(f"清理不活跃聊天流时发生错误: {e}")

    async def _check_and_handle_interruption(self, chat_stream: ChatStream | None = None):
        """检查并处理消息打断 - 支持多重回复任务取消"""
        if not global_config.chat.interruption_enabled or not chat_stream:
            return

        # 🌟 修复：获取所有处理任务（包括多重回复）
        all_processing_tasks = self.chatter_manager.get_all_processing_tasks(chat_stream.stream_id)

        if all_processing_tasks:
            # 计算打断概率 - 使用新的线性概率模型
            interruption_probability = chat_stream.context_manager.context.calculate_interruption_probability(
                global_config.chat.interruption_max_limit
            )

            # 检查是否已达到最大打断次数
            if chat_stream.context_manager.context.interruption_count >= global_config.chat.interruption_max_limit:
                logger.debug(
                    f"聊天流 {chat_stream.stream_id} 已达到最大打断次数 {chat_stream.context_manager.context.interruption_count}/{global_config.chat.interruption_max_limit}，跳过打断检查"
                )
                return

            # 根据概率决定是否打断
            if random.random() < interruption_probability:
                logger.info(f"聊天流 {chat_stream.stream_id} 触发消息打断，打断概率: {interruption_probability:.2f}，检测到 {len(all_processing_tasks)} 个任务")

                # 🌟 修复：取消所有任务（包括多重回复）
                cancelled_count = self.chatter_manager.cancel_all_stream_tasks(chat_stream.stream_id)

                if cancelled_count > 0:
                    logger.info(f"消息打断成功取消 {cancelled_count} 个任务: {chat_stream.stream_id}")
                else:
                    logger.warning(f"消息打断未能取消任何任务: {chat_stream.stream_id}")

                # 增加打断计数
                await chat_stream.context_manager.context.increment_interruption_count()

                # 🚀 新增：打断后立即重新进入聊天流程
                # 🚀 新增：打断后延迟重新进入聊天流程，以合并短时间内的多条消息
                asyncio.create_task(self._trigger_delayed_reprocess(chat_stream, delay=0.5))

                # 检查是否已达到最大次数
                if chat_stream.context_manager.context.interruption_count >= global_config.chat.interruption_max_limit:
                    logger.warning(
                        f"聊天流 {chat_stream.stream_id} 已达到最大打断次数 {chat_stream.context_manager.context.interruption_count}/{global_config.chat.interruption_max_limit}，后续消息将不再打断"
                    )
                else:
                    logger.info(
                        f"聊天流 {chat_stream.stream_id} 已打断并重新进入处理流程，当前打断次数: {chat_stream.context_manager.context.interruption_count}/{global_config.chat.interruption_max_limit}"
                    )
            else:
                logger.debug(f"聊天流 {chat_stream.stream_id} 未触发打断，打断概率: {interruption_probability:.2f}，检测到 {len(all_processing_tasks)} 个任务")

    async def _trigger_delayed_reprocess(self, chat_stream: ChatStream, delay: float):
        """打断后延迟重新进入聊天流程，以合并短时间内的多条消息"""
        await asyncio.sleep(delay)
        await self._trigger_reprocess(chat_stream)

    async def _trigger_reprocess(self, chat_stream: ChatStream):
        """重新处理聊天流的核心逻辑"""
        try:
            stream_id = chat_stream.stream_id

            logger.info(f"🚀 打断后立即重新处理聊天流: {stream_id}")

            # 等待一小段时间确保当前消息已经添加到未读消息中
            await asyncio.sleep(0.1)

            # 获取当前的stream context
            context = chat_stream.stream_context

            # 确保有未读消息需要处理
            unread_messages = context.get_unread_messages()
            if not unread_messages:
                logger.debug(f"💭 聊天流 {stream_id} 没有未读消息，跳过重新处理")
                return

            logger.info(f"💬 开始重新处理 {len(unread_messages)} 条未读消息: {stream_id}")

            # 创建新的处理任务
            task = asyncio.create_task(
                self.chatter_manager.process_stream_context(stream_id, context),
                name=f"reprocess_{stream_id}_{int(time.time())}"
            )

            # 设置处理任务
            self.chatter_manager.set_processing_task(stream_id, task)

            # 等待处理完成（使用超时防止无限等待）
            try:
                result = await asyncio.wait_for(task, timeout=30.0)
                success = result.get("success", False)
                actions_count = result.get("actions_count", 0)

                if success:
                    logger.info(f"✅ 聊天流 {stream_id} 重新处理成功: 执行了 {actions_count} 个动作")
                else:
                    logger.warning(f"❌ 聊天流 {stream_id} 重新处理失败")

            except asyncio.TimeoutError:
                logger.warning(f"⏰ 聊天流 {stream_id} 重新处理超时")
                if not task.done():
                    task.cancel()
            except Exception as e:
                logger.error(f"💥 聊天流 {stream_id} 重新处理出错: {e}")
                if not task.done():
                    task.cancel()

        except Exception as e:
            logger.error(f"🚨 触发重新处理时出错: {e}")

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


# 创建全局消息管理器实例
message_manager = MessageManager()
