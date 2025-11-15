"""
流循环管理器
为每个聊天流创建独立的无限循环任务，主动轮询处理消息
"""

import asyncio
import time
from typing import TYPE_CHECKING, Any

from src.chat.chatter_manager import ChatterManager
from src.chat.energy_system import energy_manager
from src.common.logger import get_logger
from src.config.config import global_config
from src.plugin_system.apis.chat_api import get_chat_manager

if TYPE_CHECKING:
    from src.common.data_models.message_manager_data_model import StreamContext

logger = get_logger("stream_loop_manager")


class StreamLoopManager:
    """流循环管理器 - 每个流一个独立的无限循环任务"""

    def __init__(self, max_concurrent_streams: int | None = None):
        # 统计信息
        self.stats: dict[str, Any] = {
            "active_streams": 0,
            "total_loops": 0,
            "total_process_cycles": 0,
            "total_failures": 0,
            "start_time": time.time(),
        }

        # 配置参数
        self.max_concurrent_streams = max_concurrent_streams or global_config.chat.max_concurrent_distributions

        # 强制分发策略
        self.force_dispatch_unread_threshold: int | None = getattr(
            global_config.chat, "force_dispatch_unread_threshold", 20
        )
        self.force_dispatch_min_interval: float = getattr(global_config.chat, "force_dispatch_min_interval", 0.1)

        # Chatter管理器
        self.chatter_manager: ChatterManager | None = None

        # 状态控制
        self.is_running = False

        # 每个流的上一次间隔值（用于日志去重）
        self._last_intervals: dict[str, float] = {}

        # 流循环启动锁：防止并发启动同一个流的多个循环任务
        self._stream_start_locks: dict[str, asyncio.Lock] = {}

        logger.info(f"流循环管理器初始化完成 (最大并发流数: {self.max_concurrent_streams})")

    async def start(self) -> None:
        """启动流循环管理器"""
        if self.is_running:
            logger.warning("流循环管理器已经在运行")
            return

        self.is_running = True

    async def stop(self) -> None:
        """停止流循环管理器"""
        if not self.is_running:
            return

        self.is_running = False

        # 取消所有流循环
        try:
            # 获取所有活跃的流
            from src.plugin_system.apis.chat_api import get_chat_manager

            chat_manager = get_chat_manager()
            all_streams = chat_manager.get_all_streams()

            # 创建任务列表以便并发取消
            cancel_tasks = []
            for chat_stream in all_streams.values():
                context = chat_stream.context_manager.context
                if context.stream_loop_task and not context.stream_loop_task.done():
                    context.stream_loop_task.cancel()
                    cancel_tasks.append((chat_stream.stream_id, context.stream_loop_task))

            # 并发等待所有任务取消
            if cancel_tasks:
                logger.info(f"正在取消 {len(cancel_tasks)} 个流循环任务...")
                await asyncio.gather(
                    *[self._wait_for_task_cancel(stream_id, task) for stream_id, task in cancel_tasks],
                    return_exceptions=True,
                )

            logger.info("所有流循环已清理")
        except Exception as e:
            logger.error(f"停止管理器时出错: {e}")

        logger.info("流循环管理器已停止")

    async def start_stream_loop(self, stream_id: str, force: bool = False) -> bool:
        """启动指定流的循环任务 - 优化版本使用自适应管理器

        Args:
            stream_id: 流ID
            force: 是否强制启动

        Returns:
            bool: 是否成功启动
        """
        # 获取流上下文
        context = await self._get_stream_context(stream_id)
        if not context:
            logger.warning(f"无法获取流上下文: {stream_id}")
            return False

        # 快速路径：如果流已存在且不是强制启动，无需处理
        if not force and context.stream_loop_task and not context.stream_loop_task.done():
            logger.debug(f"🔄 [流循环] stream={stream_id[:8]}, 循环已在运行，跳过启动")
            return True

        # 获取或创建该流的启动锁
        if stream_id not in self._stream_start_locks:
            self._stream_start_locks[stream_id] = asyncio.Lock()

        lock = self._stream_start_locks[stream_id]

        # 使用锁防止并发启动同一个流的多个循环任务
        async with lock:
            # 如果是强制启动且任务仍在运行，先取消旧任务
            if force and context.stream_loop_task and not context.stream_loop_task.done():
                logger.warning(f"⚠️ [流循环] stream={stream_id[:8]}, 强制启动模式：先取消现有任务")
                old_task = context.stream_loop_task
                old_task.cancel()
                try:
                    await asyncio.wait_for(old_task, timeout=2.0)
                    logger.debug(f"✅ [流循环] stream={stream_id[:8]}, 旧任务已结束")
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    logger.debug(f"⏱️ [流循环] stream={stream_id[:8]}, 旧任务已取消或超时")
                except Exception as e:
                    logger.warning(f"❌ [流循环] stream={stream_id[:8]}, 等待旧任务结束时出错: {e}")

            # 创建流循环任务
            try:
                # 检查是否有旧任务残留
                if context.stream_loop_task and not context.stream_loop_task.done():
                    logger.error(f"🚨 [流循环] stream={stream_id[:8]}, 错误：旧任务仍在运行！这不应该发生！")
                    # 紧急取消
                    context.stream_loop_task.cancel()
                    await asyncio.sleep(0.1)

                loop_task = asyncio.create_task(self._stream_loop_worker(stream_id), name=f"stream_loop_{stream_id}")

                # 将任务记录到 StreamContext 中
                context.stream_loop_task = loop_task

                # 更新统计信息
                self.stats["active_streams"] += 1
                self.stats["total_loops"] += 1

                logger.info(f"🚀 [流循环] stream={stream_id[:8]}, 启动新的流循环任务，任务ID: {id(loop_task)}")
                return True

            except Exception as e:
                logger.error(f"❌ [流循环] stream={stream_id[:8]}, 启动失败: {e}")
                return False

    async def stop_stream_loop(self, stream_id: str) -> bool:
        """停止指定流的循环任务

        Args:
            stream_id: 流ID

        Returns:
            bool: 是否成功停止
        """
        # 获取流上下文
        context = await self._get_stream_context(stream_id)
        if not context:
            logger.debug(f"流 {stream_id} 上下文不存在，无需停止")
            return False

        # 检查是否有 stream_loop_task
        if not context.stream_loop_task or context.stream_loop_task.done():
            logger.debug(f"流 {stream_id} 循环不存在或已结束，无需停止")
            return False

        task = context.stream_loop_task
        if not task.done():
            task.cancel()
            try:
                # 设置取消超时，避免无限等待
                await asyncio.wait_for(task, timeout=5.0)
            except asyncio.CancelledError:
                logger.debug(f"流循环任务已取消: {stream_id}")
            except asyncio.TimeoutError:
                logger.warning(f"流循环任务取消超时: {stream_id}")
            except Exception as e:
                logger.error(f"等待流循环任务结束时出错: {stream_id} - {e}")

        # 清空 StreamContext 中的任务记录
        context.stream_loop_task = None

        logger.debug(f"停止流循环: {stream_id}")
        return True

    async def _stream_loop_worker(self, stream_id: str) -> None:
        """单个流的工作循环 - 优化版本

        Args:
            stream_id: 流ID
        """
        task_id = id(asyncio.current_task())
        logger.info(f"🔄 [流工作器] stream={stream_id[:8]}, 任务ID={task_id}, 启动")

        try:
            while self.is_running:
                try:
                    # 1. 获取流上下文
                    context = await self._get_stream_context(stream_id)
                    if not context:
                        logger.warning(f"⚠️ [流工作器] stream={stream_id[:8]}, 无法获取流上下文")
                        await asyncio.sleep(10.0)
                        continue

                    # 2. 检查是否有消息需要处理
                    await self._flush_cached_messages_to_unread(stream_id)
                    unread_count = self._get_unread_count(context)
                    force_dispatch = self._needs_force_dispatch_for_context(context, unread_count)

                    has_messages = force_dispatch or await self._has_messages_to_process(context)

                    if has_messages:
                        if force_dispatch:
                            logger.info(f"⚡ [流工作器] stream={stream_id[:8]}, 任务ID={task_id}, 未读消息 {unread_count} 条，触发强制分发")
                        else:
                            logger.info(f"📨 [流工作器] stream={stream_id[:8]}, 任务ID={task_id}, 开始处理消息")

                        # 3. 在处理前更新能量值（用于下次间隔计算）
                        try:
                            asyncio.create_task(self._update_stream_energy(stream_id, context))
                        except Exception as e:
                            logger.debug(f"更新流能量失败 {stream_id}: {e}")

                        # 4. 激活chatter处理
                        try:
                            success = await asyncio.wait_for(self._process_stream_messages(stream_id, context), global_config.chat.thinking_timeout)
                        except asyncio.TimeoutError:
                            logger.warning(f"⏱️ [流工作器] stream={stream_id[:8]}, 任务ID={task_id}, 处理超时")
                            success = False
                        # 更新统计
                        self.stats["total_process_cycles"] += 1
                        if success:
                            logger.info(f"✅ [流工作器] stream={stream_id[:8]}, 任务ID={task_id}, 处理成功")

                            # 🔒 处理成功后，等待一小段时间确保清理操作完成
                            # 这样可以避免在 chatter_manager 清除未读消息之前就进入下一轮循环
                            await asyncio.sleep(0.1)
                        else:
                            self.stats["total_failures"] += 1
                            logger.warning(f"❌ [流工作器] stream={stream_id[:8]}, 任务ID={task_id}, 处理失败")

                    # 5. 计算下次检查间隔
                    interval = await self._calculate_interval(stream_id, has_messages)

                    # 6. sleep等待下次检查
                    # 只在间隔发生变化时输出日志，避免刷屏
                    last_interval = self._last_intervals.get(stream_id)
                    if last_interval is None or abs(interval - last_interval) > 0.01:
                        logger.info(f"流 {stream_id} 等待周期变化: {interval:.2f}s")
                        self._last_intervals[stream_id] = interval
                    await asyncio.sleep(interval)

                except asyncio.CancelledError:
                    logger.info(f"🛑 [流工作器] stream={stream_id[:8]}, 任务ID={task_id}, 被取消")
                    break
                except Exception as e:
                    logger.error(f"❌ [流工作器] stream={stream_id[:8]}, 任务ID={task_id}, 出错: {e}", exc_info=True)
                    self.stats["total_failures"] += 1
                    await asyncio.sleep(5.0)  # 错误时等待5秒再重试

        finally:
            # 清理 StreamContext 中的任务记录
            try:
                context = await self._get_stream_context(stream_id)
                if context and context.stream_loop_task:
                    context.stream_loop_task = None
                    logger.info(f"🧹 [流工作器] stream={stream_id[:8]}, 任务ID={task_id}, 清理任务记录")
            except Exception as e:
                logger.debug(f"清理 StreamContext 任务记录失败: {e}")

            # 清理间隔记录
            self._last_intervals.pop(stream_id, None)

            logger.info(f"🏁 [流工作器] stream={stream_id[:8]}, 任务ID={task_id}, 循环结束")

    async def _get_stream_context(self, stream_id: str) -> "StreamContext | None":
        """获取流上下文

        Args:
            stream_id: 流ID

        Returns:
            Optional[StreamContext]: 流上下文，如果不存在返回None
        """
        try:
            chat_manager = get_chat_manager()
            chat_stream = await chat_manager.get_stream(stream_id)
            if chat_stream:
                return chat_stream.context_manager.context
            return None
        except Exception as e:
            logger.error(f"获取流上下文失败 {stream_id}: {e}")
            return None

    async def _has_messages_to_process(self, context: "StreamContext") -> bool:
        """检查是否有消息需要处理

        Args:
            context: 流上下文

        Returns:
            bool: 是否有未读消息
        """
        try:
            # 检查是否有未读消息
            if hasattr(context, "unread_messages") and context.unread_messages:
                return True

            return False
        except Exception as e:
            logger.error(f"检查消息状态失败: {e}")
            return False

    async def _process_stream_messages(self, stream_id: str, context: "StreamContext") -> bool:
        """处理流消息 - 支持子任务管理

        Args:
            stream_id: 流ID
            context: 流上下文

        Returns:
            bool: 是否处理成功
        """
        if not self.chatter_manager:
            logger.warning(f"Chatter管理器未设置: {stream_id}")
            return False

        # 🔒 防止并发处理：如果已经在处理中，直接返回
        if context.is_chatter_processing:
            logger.warning(f"🔒 [并发保护] stream={stream_id[:8]}, Chatter 正在处理中，跳过本次处理请求")
            return False

        # 设置处理状态为正在处理
        self._set_stream_processing_status(stream_id, True)

        chatter_task = None
        try:
            start_time = time.time()
            # 检查未读消息，如果为空则直接返回（优化：避免无效的 chatter 调用）
            unread_messages = context.get_unread_messages()
            if not unread_messages:
                logger.debug(f"流 {stream_id} 未读消息为空，跳过 chatter 处理")
                return True  # 返回 True 表示处理完成（虽然没有实际处理）

            logger.debug(f"流 {stream_id} 有 {len(unread_messages)} 条未读消息，开始处理")

            # 设置触发用户ID，以实现回复保护
            last_message = context.get_last_message()
            if last_message:
                context.triggering_user_id = last_message.user_info.user_id

            # 设置 Chatter 正在处理的标志
            context.is_chatter_processing = True
            logger.debug(f"设置 Chatter 处理标志: {stream_id}")

            # 创建 chatter 处理任务，以便可以在打断时取消
            chatter_task = asyncio.create_task(
                self.chatter_manager.process_stream_context(stream_id, context),
                name=f"chatter_process_{stream_id}"
            )

            # 等待 chatter 任务完成
            results = await chatter_task
            success = results.get("success", False)

            if success:
                process_time = time.time() - start_time
                logger.debug(f"流处理成功: {stream_id} (耗时: {process_time:.2f}s)")
            else:
                logger.warning(f"流处理失败: {stream_id} - {results.get('error_message', '未知错误')}")

            return success
        except asyncio.CancelledError:
            if chatter_task and not chatter_task.done():
                chatter_task.cancel()
            raise
        except Exception as e:
            logger.error(f"流处理异常: {stream_id} - {e}", exc_info=True)
            return False
        finally:
            # 清除 Chatter 处理标志
            context.is_chatter_processing = False
            logger.debug(f"清除 Chatter 处理标志: {stream_id}")

            # 无论成功或失败，都要设置处理状态为未处理
            self._set_stream_processing_status(stream_id, False)

    def _set_stream_processing_status(self, stream_id: str, is_processing: bool) -> None:
        """设置流的处理状态"""
        try:
            from .message_manager import message_manager

            if message_manager.is_running:
                message_manager.set_stream_processing_status(stream_id, is_processing)
                logger.debug(f"设置流处理状态: stream={stream_id}, processing={is_processing}")

        except ImportError:
            logger.debug("MessageManager不可用，跳过状态设置")
        except Exception as e:
            logger.warning(f"设置流处理状态失败: stream={stream_id}, error={e}")

    async def _flush_cached_messages_to_unread(self, stream_id: str) -> list:
        """将缓存消息刷新到未读消息列表"""
        try:
            # 获取流上下文
            context = await self._get_stream_context(stream_id)
            if not context:
                logger.warning(f"无法获取流上下文: {stream_id}")
                return []

            # 使用StreamContext的缓存刷新功能
            if hasattr(context, "flush_cached_messages"):
                cached_messages = context.flush_cached_messages()
                if cached_messages:
                    logger.debug(f"从StreamContext刷新缓存消息: stream={stream_id}, 数量={len(cached_messages)}")
                return cached_messages
            else:
                logger.debug(f"StreamContext不支持缓存刷新: stream={stream_id}")
                return []

        except Exception as e:
            logger.warning(f"刷新StreamContext缓存失败: stream={stream_id}, error={e}")
            return []

    async def _update_stream_energy(self, stream_id: str, context: Any) -> None:
        """更新流的能量值

        Args:
            stream_id: 流ID
            context: 流上下文 (StreamContext)
        """
        try:
            from src.chat.message_receive.chat_stream import get_chat_manager

            # 获取聊天流
            chat_manager = get_chat_manager()
            chat_stream = await chat_manager.get_stream(stream_id)

            if not chat_stream:
                logger.debug(f"无法找到聊天流 {stream_id}，跳过能量更新")
                return

            # 从 context_manager 获取消息（包括未读和历史消息）
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
            user_id = None
            if context.triggering_user_id:
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

    async def _calculate_interval(self, stream_id: str, has_messages: bool) -> float:
        """计算下次检查间隔

        Args:
            stream_id: 流ID
            has_messages: 本次是否有消息处理

        Returns:
            float: 间隔时间（秒）
        """
        # 基础间隔
        base_interval = getattr(global_config.chat, "distribution_interval", 5.0)

        # 如果没有消息，使用更长的间隔
        if not has_messages:
            return base_interval * 2.0  # 无消息时间隔加倍

        # 尝试使用能量管理器计算间隔
        try:
            # 获取当前focus_energy
            focus_energy = energy_manager.energy_cache.get(stream_id, (0.5, 0))[0]

            # 使用能量管理器计算间隔
            interval = energy_manager.get_distribution_interval(focus_energy)

            logger.debug(f"流 {stream_id} 动态间隔: {interval:.2f}s (能量: {focus_energy:.3f})")
            return interval

        except Exception as e:
            logger.debug(f"流 {stream_id} 使用默认间隔: {base_interval:.2f}s ({e})")
            return base_interval

    def get_queue_status(self) -> dict[str, Any]:
        """获取队列状态

        Returns:
            Dict[str, Any]: 队列状态信息
        """
        current_time = time.time()
        uptime = current_time - self.stats["start_time"] if self.is_running else 0

        # 从统计信息中获取活跃流数量
        active_streams = self.stats.get("active_streams", 0)

        return {
            "active_streams": active_streams,
            "total_loops": self.stats["total_loops"],
            "max_concurrent": self.max_concurrent_streams,
            "is_running": self.is_running,
            "uptime": uptime,
            "total_process_cycles": self.stats["total_process_cycles"],
            "total_failures": self.stats["total_failures"],
            "stats": self.stats.copy(),
        }

    def set_chatter_manager(self, chatter_manager: ChatterManager) -> None:
        """设置chatter管理器

        Args:
            chatter_manager: chatter管理器实例
        """
        self.chatter_manager = chatter_manager
        logger.debug(f"设置chatter管理器: {chatter_manager.__class__.__name__}")

    async def _should_force_dispatch_for_stream(self, stream_id: str) -> bool:
        if not self.force_dispatch_unread_threshold or self.force_dispatch_unread_threshold <= 0:
            return False

        try:
            chat_manager = get_chat_manager()
            chat_stream = await chat_manager.get_stream(stream_id)
            if not chat_stream:
                return False

            unread = getattr(chat_stream.context_manager.context, "unread_messages", [])
            return len(unread) > self.force_dispatch_unread_threshold
        except Exception as e:
            logger.debug(f"检查流 {stream_id} 是否需要强制分发失败: {e}")
            return False

    def _get_unread_count(self, context: "StreamContext") -> int:
        try:
            unread_messages = context.unread_messages
            if unread_messages is None:
                return 0
            return len(unread_messages)
        except Exception:
            return 0

    def _needs_force_dispatch_for_context(self, context: "StreamContext", unread_count: int | None = None) -> bool:
        if not self.force_dispatch_unread_threshold or self.force_dispatch_unread_threshold <= 0:
            return False

        count = unread_count if unread_count is not None else self._get_unread_count(context)
        return count > self.force_dispatch_unread_threshold

    def get_performance_summary(self) -> dict[str, Any]:
        """获取性能摘要

        Returns:
            Dict[str, Any]: 性能摘要
        """
        current_time = time.time()
        uptime = current_time - self.stats["start_time"]

        # 计算吞吐量
        throughput = self.stats["total_process_cycles"] / max(1, uptime / 3600)  # 每小时处理次数

        # 从统计信息中获取活跃流数量
        active_streams = self.stats.get("active_streams", 0)

        return {
            "uptime_hours": uptime / 3600,
            "active_streams": active_streams,
            "total_process_cycles": self.stats["total_process_cycles"],
            "total_failures": self.stats["total_failures"],
            "throughput_per_hour": throughput,
            "max_concurrent_streams": self.max_concurrent_streams,
        }

    async def _refresh_focus_energy(self, stream_id: str) -> None:
        """分发完成后基于历史消息刷新能量值"""
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

    async def _wait_for_task_cancel(self, stream_id: str, task: asyncio.Task) -> None:
        """等待任务取消完成，带有超时控制

        Args:
            stream_id: 流ID
            task: 要等待取消的任务
        """
        try:
            await asyncio.wait_for(task, timeout=5.0)
            logger.debug(f"流循环任务已正常结束: {stream_id}")
        except asyncio.CancelledError:
            logger.debug(f"流循环任务已取消: {stream_id}")
        except asyncio.TimeoutError:
            logger.warning(f"流循环任务取消超时: {stream_id}")
        except Exception as e:
            logger.error(f"等待流循环任务结束时出错: {stream_id} - {e}")

    async def _force_dispatch_stream(self, stream_id: str) -> None:
        """强制分发流处理

        当流的未读消息超过阈值时，强制触发分发处理
        这个方法主要用于突破并发限制时的紧急处理

        注意：此方法目前未被使用，相关功能已集成到 start_stream_loop 方法中

        Args:
            stream_id: 流ID
        """
        logger.debug(f"强制分发流处理: {stream_id}")

        try:
            # 获取流上下文
            context = await self._get_stream_context(stream_id)
            if not context:
                logger.warning(f"强制分发时未找到流上下文: {stream_id}")
                return

            # 检查是否有现有的 stream_loop_task
            if context.stream_loop_task and not context.stream_loop_task.done():
                logger.debug(f"发现现有流循环 {stream_id}，将先取消再重新创建")
                existing_task = context.stream_loop_task
                existing_task.cancel()
                # 创建异步任务来等待取消完成，并添加异常处理
                cancel_task = asyncio.create_task(
                    self._wait_for_task_cancel(stream_id, existing_task), name=f"cancel_existing_loop_{stream_id}"
                )
                # 为取消任务添加异常处理，避免孤儿任务
                cancel_task.add_done_callback(
                    lambda task: logger.debug(f"取消任务完成: {stream_id}")
                    if not task.exception()
                    else logger.error(f"取消任务异常: {stream_id} - {task.exception()}")
                )

            # 检查未读消息数量
            unread_count = self._get_unread_count(context)
            logger.info(f"流 {stream_id} 当前未读消息数: {unread_count}")

            # 使用 start_stream_loop 重新创建流循环任务
            success = await self.start_stream_loop(stream_id, force=True)

            if success:
                logger.info(f"已创建强制分发流循环: {stream_id}")
            else:
                logger.warning(f"创建强制分发流循环失败: {stream_id}")

        except Exception as e:
            logger.error(f"强制分发流处理失败 {stream_id}: {e}", exc_info=True)


# 全局流循环管理器实例
stream_loop_manager = StreamLoopManager()
