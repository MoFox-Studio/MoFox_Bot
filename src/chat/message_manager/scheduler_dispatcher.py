"""
基于 unified_scheduler 的消息分发管理器
替代原有的 stream_loop_task 循环机制，使用统一的调度器来管理消息处理时机
"""

import asyncio
import time
from typing import Any

from src.chat.chatter_manager import ChatterManager
from src.chat.energy_system import energy_manager
from src.common.data_models.message_manager_data_model import StreamContext
from src.common.logger import get_logger
from src.config.config import global_config
from src.plugin_system.apis.chat_api import get_chat_manager
from src.schedule.unified_scheduler import TriggerType, unified_scheduler

logger = get_logger("scheduler_dispatcher")


class SchedulerDispatcher:
    """基于 scheduler 的消息分发器

    工作流程：
    1. 接收消息时，将消息添加到聊天流上下文
    2. 检查是否有活跃的 schedule，如果没有则创建
    3. 如果有，检查打断判定，成功则移除旧 schedule 并创建新的
    4. schedule 到期时，激活 chatter 处理
    5. 处理完成后，计算下次间隔并注册新 schedule
    """

    def __init__(self):
        # 追踪每个流的 task_name
        self.stream_schedules: dict[str, str] = {}  # stream_id -> task_name

        # Chatter 管理器
        self.chatter_manager: ChatterManager | None = None
        
        # 统计信息
        self.stats = {
            "total_schedules_created": 0,
            "total_schedules_cancelled": 0,
            "total_interruptions": 0,
            "total_process_cycles": 0,
            "total_failures": 0,
            "start_time": time.time(),
        }

        self.is_running = False

        logger.info("基于 Scheduler 的消息分发器初始化完成")

    async def start(self) -> None:
        """启动分发器"""
        if self.is_running:
            logger.warning("分发器已在运行")
            return

        self.is_running = True
        logger.info("基于 Scheduler 的消息分发器已启动")

    async def stop(self) -> None:
        """停止分发器"""
        if not self.is_running:
            return

        self.is_running = False

        # 取消所有活跃的 schedule
        task_names = list(self.stream_schedules.values())
        for task_name in task_names:
            try:
                await unified_scheduler.remove_schedule_by_name(task_name)
            except Exception as e:
                logger.error(f"移除 schedule {task_name} 失败: {e}")

        self.stream_schedules.clear()
        logger.info("基于 Scheduler 的消息分发器已停止")

    def set_chatter_manager(self, chatter_manager: ChatterManager) -> None:
        """设置 Chatter 管理器"""
        self.chatter_manager = chatter_manager
        logger.debug(f"设置 Chatter 管理器: {chatter_manager.__class__.__name__}")

    async def on_message_received(self, stream_id: str) -> None:
        """消息接收时的处理逻辑

        Args:
            stream_id: 聊天流ID
        """
        if not self.is_running:
            logger.warning("分发器未运行，忽略消息")
            return

        try:
            # 1. 获取流上下文
            context = await self._get_stream_context(stream_id)
            if not context:
                logger.warning(f"无法获取流上下文: {stream_id}")
                return

            # 2. 检查是否有活跃的 schedule
            task_name = self.stream_schedules.get(stream_id)
            has_active_schedule = False

            if task_name:
                # 验证schedule是否真的还在scheduler中活跃
                schedule_id = await unified_scheduler.find_schedule_by_name(task_name)
                if schedule_id:
                    # 进一步检查任务是否正在执行或即将执行
                    task = await unified_scheduler.get_task_info(schedule_id)
                    if task and task['is_active']:
                        has_active_schedule = True
                        logger.debug(f"验证到活跃schedule: 流={stream_id[:8]}..., task={task_name}")
                    else:
                        logger.warning(f"发现不活跃的schedule记录，将清理: 流={stream_id[:8]}..., task={task_name}")
                        # 清理无效记录
                        self.stream_schedules.pop(stream_id, None)
                else:
                    logger.warning(f"发现无效的schedule记录，将清理: 流={stream_id[:8]}..., task={task_name}")
                    # 清理无效记录
                    self.stream_schedules.pop(stream_id, None)

            if not has_active_schedule:
                # 4. 创建新的 schedule（在锁内，避免重复创建）
                await self._create_schedule(stream_id, context)
                return

            # 3. 检查打断判定
            if has_active_schedule:
                should_interrupt = await self._check_interruption(stream_id, context)

                if should_interrupt:
                    # 移除旧 schedule 并创建新的
                    await self._cancel_and_recreate_schedule(stream_id, context)
                    logger.debug(f"⚡ 打断成功: 流={stream_id[:8]}..., 已重新创建 schedule")
                else:
                    logger.debug(f"打断判定失败，保持原有 schedule: 流={stream_id[:8]}...")

        except Exception as e:
            logger.error(f"处理消息接收事件失败 {stream_id}: {e}", exc_info=True)

    async def _get_stream_context(self, stream_id: str) -> StreamContext | None:
        """获取流上下文"""
        try:
            chat_manager = get_chat_manager()
            chat_stream = await chat_manager.get_stream(stream_id)
            if chat_stream:
                return chat_stream.context_manager.context
            return None
        except Exception as e:
            logger.error(f"获取流上下文失败 {stream_id}: {e}")
            return None

    async def _check_interruption(self, stream_id: str, context: StreamContext) -> bool:
        """检查是否应该打断当前处理

        Args:
            stream_id: 流ID
            context: 流上下文

        Returns:
            bool: 是否应该打断
        """
        # 检查是否启用打断
        if not global_config.chat.interruption_enabled:
            return False

        # 检查是否正在回复，以及是否允许在回复时打断
        if context.is_replying:
            if not global_config.chat.allow_reply_interruption:
                logger.debug(f"聊天流 {stream_id} 正在回复中，且配置不允许回复时打断")
                return False
            else:
                logger.debug(f"聊天流 {stream_id} 正在回复中，但配置允许回复时打断")

        # 只有当 Chatter 真正在处理时才检查打断
        if not context.is_chatter_processing:
            logger.debug(f"聊天流 {stream_id} Chatter 未在处理，无需打断")
            return False

        # 检查最后一条消息
        last_message = context.get_last_message()
        if not last_message:
            return False

        # 检查是否为表情包消息
        if last_message.is_picid or last_message.is_emoji:
            logger.info(f"消息 {last_message.message_id} 是表情包或Emoji，跳过打断检查")
            return False

        # 检查触发用户ID
        triggering_user_id = context.triggering_user_id
        if triggering_user_id and last_message.user_info.user_id != triggering_user_id:
            logger.info(f"消息来自非触发用户 {last_message.user_info.user_id}，实际触发用户为 {triggering_user_id}，跳过打断检查")
            return False

        # 检查是否已达到最大打断次数
        if context.interruption_count >= global_config.chat.interruption_max_limit:
            logger.debug(
                f"聊天流 {stream_id} 已达到最大打断次数 {context.interruption_count}/{global_config.chat.interruption_max_limit}"
            )
            return False

        # 计算打断概率
        interruption_probability = context.calculate_interruption_probability(
            global_config.chat.interruption_max_limit
        )

        # 根据概率决定是否打断
        import random
        if random.random() < interruption_probability:
            logger.debug(f"聊天流 {stream_id} 触发消息打断，打断概率: {interruption_probability:.2f}")

            # 增加打断计数
            await context.increment_interruption_count()
            self.stats["total_interruptions"] += 1

            # 检查是否已达到最大次数
            if context.interruption_count >= global_config.chat.interruption_max_limit:
                logger.warning(
                    f"聊天流 {stream_id} 已达到最大打断次数 {context.interruption_count}/{global_config.chat.interruption_max_limit}，后续消息将不再打断"
                )
            else:
                logger.info(
                    f"聊天流 {stream_id} 已打断，当前打断次数: {context.interruption_count}/{global_config.chat.interruption_max_limit}"
                )

            return True
        else:
            logger.debug(f"聊天流 {stream_id} 未触发打断，打断概率: {interruption_probability:.2f}")
            return False

    async def _cancel_and_recreate_schedule(self, stream_id: str, context: StreamContext) -> None:
        """取消旧的 schedule 并创建新的（打断模式，使用极短延迟）

        Args:
            stream_id: 流ID
            context: 流上下文
        """
        # 移除旧的 schedule
        old_task_name = self.stream_schedules.get(stream_id)
        if old_task_name:
            success = await unified_scheduler.remove_schedule_by_name(old_task_name)
            if success:
                logger.info(f"🔄 已移除旧 schedule 并准备重建: 流={stream_id[:8]}..., task={old_task_name}")
                self.stats["total_schedules_cancelled"] += 1
                # 只有成功移除后才从追踪中删除
                self.stream_schedules.pop(stream_id)
            else:
                logger.error(
                    f"❌ 打断失败：无法移除旧 schedule: 流={stream_id[:8]}..., "
                    f"task={old_task_name}, 放弃创建新 schedule 避免重复"
                )
                # 移除失败，不创建新 schedule，避免重复
                return

            # 创建新的 schedule，使用即时处理模式（极短延迟）
            await self._create_schedule(stream_id, context, immediate_mode=True)

    async def _create_schedule(self, stream_id: str, context: StreamContext, immediate_mode: bool = False) -> None:
        """为聊天流创建新的 schedule

        Args:
            stream_id: 流ID
            context: 流上下文
            immediate_mode: 是否使用即时处理模式（打断时使用极短延迟）
        """
        try:
            # 检查是否已有活跃的 schedule，如果有则先移除
            if stream_id in self.stream_schedules:
                old_task_name = self.stream_schedules[stream_id]
                logger.warning(
                    f"⚠️ 流 {stream_id[:8]}... 已有活跃 schedule {old_task_name}, "
                    f"这不应该发生，将先移除旧 schedule"
                )
                await unified_scheduler.remove_schedule_by_name(old_task_name)
                del self.stream_schedules[stream_id]

            # 如果是即时处理模式（打断时），使用固定的1秒延迟立即重新处理
            if immediate_mode:
                delay = 1.0  # 硬编码1秒延迟，确保打断后能快速重新处理
                logger.debug(
                    f"⚡ 打断模式启用: 流={stream_id[:8]}..., "
                    f"使用即时延迟={delay:.1f}s 立即重新处理"
                )
            else:
                # 常规模式：计算初始延迟
                delay = await self._calculate_initial_delay(stream_id, context)

            # 获取未读消息数量用于日志
            unread_count = len(context.unread_messages) if context.unread_messages else 0

            # 生成任务名称 - 使用stream_id确保唯一性
            task_name = f"dispatch_{stream_id}"

            # 创建 schedule - 使用force_overwrite确保可以覆盖
            schedule_id = await unified_scheduler.create_schedule(
                callback=self._on_schedule_triggered,
                trigger_type=TriggerType.TIME,
                trigger_config={"delay_seconds": delay},
                is_recurring=False,  # 一次性任务，处理完后会创建新的
                task_name=task_name,
                callback_args=(stream_id,),
                force_overwrite=True,  # 允许覆盖同名任务
            )

            # 追踪 task_name
            self.stream_schedules[stream_id] = task_name
            self.stats["total_schedules_created"] += 1

            mode_indicator = "⚡打断" if immediate_mode else "📅常规"

            logger.info(
                f"{mode_indicator} 创建 schedule: 流={stream_id[:8]}..., "
                f"延迟={delay:.3f}s, 未读={unread_count}, "
                f"task={task_name}, ID={schedule_id[:8]}..."
            )

        except Exception as e:
            logger.error(f"创建 schedule 失败 {stream_id}: {e}", exc_info=True)

    async def _calculate_initial_delay(self, stream_id: str, context: StreamContext) -> float:
        """计算初始延迟时间

        Args:
            stream_id: 流ID
            context: 流上下文

        Returns:
            float: 延迟时间（秒）
        """
        # 基础间隔
        base_interval = getattr(global_config.chat, "distribution_interval", 5.0)

        # 检查是否有未读消息
        unread_count = len(context.unread_messages) if context.unread_messages else 0

        # 强制分发阈值
        force_dispatch_threshold = getattr(global_config.chat, "force_dispatch_unread_threshold", 20)

        # 如果未读消息过多，使用最小间隔
        if force_dispatch_threshold and unread_count > force_dispatch_threshold:
            min_interval = getattr(global_config.chat, "force_dispatch_min_interval", 0.1)
            logger.warning(
                f"⚠️ 强制分发触发: 流={stream_id[:8]}..., "
                f"未读={unread_count} (阈值={force_dispatch_threshold}), "
                f"使用最小间隔={min_interval}s"
            )
            return min_interval

        # 尝试使用能量管理器计算间隔
        try:
            # 更新能量值
            await self._update_stream_energy(stream_id, context)

            # 获取当前 focus_energy
            focus_energy = energy_manager.energy_cache.get(stream_id, (0.5, 0))[0]

            # 使用能量管理器计算间隔
            interval = energy_manager.get_distribution_interval(focus_energy)

            logger.info(
                f"📊 动态间隔计算: 流={stream_id[:8]}..., "
                f"能量={focus_energy:.3f}, 间隔={interval:.2f}s"
            )
            return interval

        except Exception as e:
            logger.info(
                f"📊 使用默认间隔: 流={stream_id[:8]}..., "
                f"间隔={base_interval:.2f}s (动态计算失败: {e})"
            )
            return base_interval

    async def _update_stream_energy(self, stream_id: str, context: StreamContext) -> None:
        """更新流的能量值

        Args:
            stream_id: 流ID
            context: 流上下文
        """
        try:
            from src.chat.message_receive.chat_stream import get_chat_manager

            # 获取聊天流
            chat_manager = get_chat_manager()
            chat_stream = await chat_manager.get_stream(stream_id)

            if not chat_stream:
                logger.debug(f"无法找到聊天流 {stream_id}，跳过能量更新")
                return

            # 合并未读消息和历史消息
            all_messages = []

            # 添加历史消息
            history_messages = context.get_history_messages(limit=global_config.chat.max_context_size)
            all_messages.extend(history_messages)

            # 添加未读消息
            unread_messages = context.get_unread_messages()
            all_messages.extend(unread_messages)

            # 按时间排序并限制数量
            all_messages.sort(key=lambda m: m.time)
            messages = all_messages[-global_config.chat.max_context_size:]

            # 获取用户ID
            user_id = context.triggering_user_id

            # 使用能量管理器计算并缓存能量值
            energy = await energy_manager.calculate_focus_energy(
                stream_id=stream_id,
                messages=messages,
                user_id=user_id
            )

            # 同步更新到 ChatStream
            chat_stream._focus_energy = energy

            logger.debug(f"已更新流 {stream_id} 的能量值: {energy:.3f}")

        except Exception as e:
            logger.warning(f"更新流能量失败 {stream_id}: {e}", exc_info=False)

    async def _on_schedule_triggered(self, stream_id: str) -> None:
        """schedule 触发时的回调

        Args:
            stream_id: 流ID
        """
        try:
            task_name = self.stream_schedules.get(stream_id)

            logger.info(
                f"⏰ Schedule 触发: 流={stream_id[:8]}..., "
                f"task={task_name or 'None'}, "
                f"开始处理消息"
            )

            # 获取流上下文
            context = await self._get_stream_context(stream_id)
            if not context:
                logger.warning(f"Schedule 触发时无法获取流上下文: {stream_id}")
                return

            # 检查是否有未读消息
            if not context.unread_messages:
                logger.debug(f"流 {stream_id} 没有未读消息，跳过处理")
                return

            # 激活 chatter 处理（不需要锁，允许并发处理）
            success = await self._process_stream(stream_id, context)

            # 更新统计
            self.stats["total_process_cycles"] += 1
            if not success:
                self.stats["total_failures"] += 1

            # 清理schedule记录 - 处理完成后总是清理本地记录
            removed_task_name = self.stream_schedules.pop(stream_id, None)
            if removed_task_name:
                logger.debug(f"清理schedule记录: 流={stream_id[:8]}..., task={removed_task_name}")

            # 检查缓存中是否有待处理的消息
            from src.chat.message_manager.message_manager import message_manager

            has_cached = message_manager.has_cached_messages(stream_id)

            if has_cached:
                # 有缓存消息，立即创建新 schedule 继续处理
                logger.info(
                    f"🔁 处理完成但有缓存消息: 流={stream_id[:8]}..., "
                    f"立即创建新 schedule 继续处理"
                )
                await self._create_schedule(stream_id, context)
            else:
                # 没有缓存消息，不创建 schedule，等待新消息到达
                logger.debug(
                    f"✅ 处理完成且无缓存消息: 流={stream_id[:8]}..., "
                    f"等待新消息到达"
                )

        except Exception as e:
            logger.error(f"Schedule 回调执行失败 {stream_id}: {e}", exc_info=True)

    async def _process_stream(self, stream_id: str, context: StreamContext) -> bool:
        """处理流消息

        Args:
            stream_id: 流ID
            context: 流上下文

        Returns:
            bool: 是否处理成功
        """
        if not self.chatter_manager:
            logger.warning(f"Chatter 管理器未设置: {stream_id}")
            return False

        # 设置处理状态
        self._set_stream_processing_status(stream_id, True)

        try:
            start_time = time.time()

            # 设置触发用户ID
            last_message = context.get_last_message()
            if last_message:
                context.triggering_user_id = last_message.user_info.user_id

            # 创建异步任务刷新能量（不阻塞主流程）
            energy_task = asyncio.create_task(self._refresh_focus_energy(stream_id))

            # 设置 Chatter 正在处理的标志
            context.is_chatter_processing = True
            logger.debug(f"设置 Chatter 处理标志: {stream_id}")

            try:
                # 调用 chatter_manager 处理流上下文
                results = await self.chatter_manager.process_stream_context(stream_id, context)
                success = results.get("success", False)

                if success:
                    process_time = time.time() - start_time
                    logger.debug(f"流处理成功: {stream_id} (耗时: {process_time:.2f}s)")
                else:
                    logger.warning(f"流处理失败: {stream_id} - {results.get('error_message', '未知错误')}")

                return success

            finally:
                # 清除 Chatter 处理标志
                context.is_chatter_processing = False
                logger.debug(f"清除 Chatter 处理标志: {stream_id}")

                # 等待能量刷新任务完成
                try:
                    await asyncio.wait_for(energy_task, timeout=5.0)
                except asyncio.TimeoutError:
                    logger.warning(f"等待能量刷新超时: {stream_id}")
                except Exception as e:
                    logger.debug(f"能量刷新任务异常: {e}")

        except Exception as e:
            logger.error(f"流处理异常: {stream_id} - {e}", exc_info=True)
            return False

        finally:
            # 设置处理状态为未处理
            self._set_stream_processing_status(stream_id, False)

    def _set_stream_processing_status(self, stream_id: str, is_processing: bool) -> None:
        """设置流的处理状态"""
        try:
            from src.chat.message_manager.message_manager import message_manager

            if message_manager.is_running:
                message_manager.set_stream_processing_status(stream_id, is_processing)
                logger.debug(f"设置流处理状态: stream={stream_id}, processing={is_processing}")

        except ImportError:
            logger.debug("MessageManager 不可用，跳过状态设置")
        except Exception as e:
            logger.warning(f"设置流处理状态失败: stream={stream_id}, error={e}")

    async def _refresh_focus_energy(self, stream_id: str) -> None:
        """分发完成后刷新能量值"""
        try:
            chat_manager = get_chat_manager()
            chat_stream = await chat_manager.get_stream(stream_id)
            if not chat_stream:
                logger.debug(f"刷新能量时未找到聊天流: {stream_id}")
                return

            await chat_stream.context_manager.refresh_focus_energy_from_history()
            logger.debug(f"已刷新聊天流 {stream_id} 的聚焦能量")
        except Exception as e:
            logger.warning(f"刷新聊天流 {stream_id} 能量失败: {e}")

    async def cleanup_invalid_schedules(self) -> int:
        """清理无效的schedule记录

        Returns:
            int: 清理的记录数量
        """
        cleaned_count = 0
        invalid_streams = []

        for stream_id, task_name in list(self.stream_schedules.items()):
            # 验证schedule是否真的还在scheduler中活跃
            schedule_id = await unified_scheduler.find_schedule_by_name(task_name)
            if not schedule_id:
                invalid_streams.append(stream_id)
                continue

            # 检查任务是否还在活跃状态
            task = await unified_scheduler.get_task_info(schedule_id)
            if not task or not task['is_active']:
                invalid_streams.append(stream_id)

        # 清理无效记录
        for stream_id in invalid_streams:
            task_name = self.stream_schedules.pop(stream_id, None)
            if task_name:
                logger.info(f"清理无效schedule记录: 流={stream_id[:8]}..., task={task_name}")
                cleaned_count += 1

        return cleaned_count

    def get_statistics(self) -> dict[str, Any]:
        """获取统计信息"""
        uptime = time.time() - self.stats["start_time"]
        return {
            "is_running": self.is_running,
            "active_schedules": len(self.stream_schedules),
            "total_schedules_created": self.stats["total_schedules_created"],
            "total_schedules_cancelled": self.stats["total_schedules_cancelled"],
            "total_interruptions": self.stats["total_interruptions"],
            "total_process_cycles": self.stats["total_process_cycles"],
            "total_failures": self.stats["total_failures"],
            "uptime": uptime,
        }


# 全局实例
scheduler_dispatcher = SchedulerDispatcher()
