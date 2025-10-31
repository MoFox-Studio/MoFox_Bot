"""
统一调度器模块
提供统一的任务调度接口，支持时间触发、事件触发和自定义条件触发
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from enum import Enum
from typing import Any

from src.common.logger import get_logger
from src.plugin_system.base.component_types import EventType

logger = get_logger("unified_scheduler")


class TriggerType(Enum):
    """触发类型枚举"""

    TIME = "time"  # 时间触发
    EVENT = "event"  # 事件触发（通过 event_manager）
    CUSTOM = "custom"  # 自定义条件触发


class ScheduleTask:
    """调度任务模型"""

    def __init__(
        self,
        schedule_id: str,
        callback: Callable[..., Awaitable[Any]],
        trigger_type: TriggerType,
        trigger_config: dict[str, Any],
        is_recurring: bool = False,
        task_name: str | None = None,
        callback_args: tuple | None = None,
        callback_kwargs: dict | None = None,
    ):
        self.schedule_id = schedule_id
        self.callback = callback
        self.trigger_type = trigger_type
        self.trigger_config = trigger_config
        self.is_recurring = is_recurring
        self.task_name = task_name or f"Task-{schedule_id[:8]}"
        self.callback_args = callback_args or ()
        self.callback_kwargs = callback_kwargs or {}
        self.created_at = datetime.now()
        self.last_triggered_at: datetime | None = None
        self.trigger_count = 0
        self.is_active = True

    def __repr__(self) -> str:
        return (
            f"ScheduleTask(id={self.schedule_id[:8]}..., "
            f"name={self.task_name}, type={self.trigger_type.value}, "
            f"recurring={self.is_recurring}, active={self.is_active})"
        )


class UnifiedScheduler:
    """统一调度器

    提供统一的调度接口，支持：
    1. 时间触发：指定时间点或延迟时间后触发
    2. 事件触发：订阅 event_manager 的事件，当事件发生时触发
    3. 自定义触发：通过自定义判断函数决定是否触发

    特点：
    - 每秒检查一次所有任务
    - 自动执行到期任务
    - 支持循环和一次性任务
    - 提供任务管理API（创建、删除、强制触发等）
    - 与 event_manager 集成，统一事件管理
    """

    def __init__(self):
        self._tasks: dict[str, ScheduleTask] = {}
        self._running = False
        self._check_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._event_subscriptions: set[str] = set()  # 追踪已订阅的事件

    async def _handle_event_trigger(self, event_name: str | EventType, event_params: dict[str, Any]) -> None:
        """处理来自 event_manager 的事件通知
        
        此方法由 event_manager 在触发事件时直接调用
        
        注意：此方法不能在持有 self._lock 的情况下调用，
        否则会导致死锁（因为回调可能再次触发事件）
        """
        # 获取订阅该事件的所有任务（快速复制，减少锁持有时间）
        async with self._lock:
            event_tasks = [
                task
                for task in self._tasks.values()
                if task.trigger_type == TriggerType.EVENT
                and task.trigger_config.get("event_name") == event_name
                and task.is_active
            ]

        if not event_tasks:
            logger.debug(f"[调度器] 事件 '{event_name}' 没有对应的调度任务")
            return

        logger.info(f"[调度器] 事件 '{event_name}' 触发，共有 {len(event_tasks)} 个调度任务")

        tasks_to_remove = []

        # 在锁外执行回调，避免死锁
        for task in event_tasks:
            try:
                logger.debug(f"[调度器] 执行事件任务: {task.task_name}")

                # 执行回调，传入事件参数
                if event_params:
                    if asyncio.iscoroutinefunction(task.callback):
                        await task.callback(**event_params)
                    else:
                        task.callback(**event_params)
                else:
                    await self._execute_callback(task)

                task.last_triggered_at = datetime.now()
                task.trigger_count += 1

                # 如果不是循环任务，标记为删除
                if not task.is_recurring:
                    tasks_to_remove.append(task.schedule_id)

                logger.debug(f"[调度器] 事件任务 {task.task_name} 执行完成")

            except Exception as e:
                logger.error(f"[调度器] 执行事件 '{event_name}' 的任务 {task.task_name} 时出错: {e}", exc_info=True)

        # 移除已完成的一次性任务
        if tasks_to_remove:
            async with self._lock:
                for schedule_id in tasks_to_remove:
                    await self._remove_task_internal(schedule_id)

    async def start(self):
        """启动调度器"""
        if self._running:
            logger.warning("调度器已在运行中")
            return

        self._running = True
        self._check_task = asyncio.create_task(self._check_loop())

        # 注册回调到 event_manager
        try:
            from src.plugin_system.core.event_manager import event_manager

            event_manager.register_scheduler_callback(self._handle_event_trigger)
            logger.info("调度器已注册到 event_manager")
        except ImportError:
            logger.warning("无法导入 event_manager，事件触发功能将不可用")

        logger.info("统一调度器已启动")

    async def stop(self):
        """停止调度器"""
        if not self._running:
            return

        self._running = False
        if self._check_task:
            self._check_task.cancel()
            try:
                await self._check_task
            except asyncio.CancelledError:
                pass

        # 取消注册回调
        try:
            from src.plugin_system.core.event_manager import event_manager

            event_manager.unregister_scheduler_callback()
            logger.info("调度器回调已从 event_manager 注销")
        except ImportError:
            pass

        logger.info(f"统一调度器已停止，共有 {len(self._tasks)} 个任务被清理")
        self._tasks.clear()
        self._event_subscriptions.clear()

    async def _check_loop(self):
        """主循环：每秒检查一次所有任务"""
        logger.info("调度器检查循环已启动")
        while self._running:
            try:
                await asyncio.sleep(1)
                await self._check_and_trigger_tasks()
            except asyncio.CancelledError:
                logger.info("调度器检查循环被取消")
                break
            except Exception as e:
                logger.error(f"调度器检查循环发生错误: {e}", exc_info=True)

    async def _check_and_trigger_tasks(self):
        """检查并触发到期任务
        
        注意：为了避免死锁，回调执行必须在锁外进行
        """
        current_time = datetime.now()

        # 第一阶段：在锁内快速收集需要触发的任务
        async with self._lock:
            tasks_to_trigger = []

            for schedule_id, task in list(self._tasks.items()):
                if not task.is_active:
                    continue

                try:
                    should_trigger = await self._should_trigger_task(task, current_time)
                    if should_trigger:
                        tasks_to_trigger.append(task)
                except Exception as e:
                    logger.error(f"检查任务 {task.task_name} 时发生错误: {e}", exc_info=True)

        # 第二阶段：在锁外执行回调（避免死锁）
        tasks_to_remove = []

        for task in tasks_to_trigger:
            try:
                logger.debug(f"[调度器] 触发定时任务: {task.task_name}")

                # 执行回调
                await self._execute_callback(task)

                # 更新任务状态
                task.last_triggered_at = current_time
                task.trigger_count += 1

                # 如果不是循环任务，标记为删除
                if not task.is_recurring:
                    tasks_to_remove.append(task.schedule_id)
                    logger.info(f"[调度器] 一次性任务 {task.task_name} 已完成，将被移除")

            except Exception as e:
                logger.error(f"[调度器] 执行任务 {task.task_name} 时发生错误: {e}", exc_info=True)

        # 第三阶段：在锁内移除已完成的任务
        if tasks_to_remove:
            async with self._lock:
                for schedule_id in tasks_to_remove:
                    await self._remove_task_internal(schedule_id)

    async def _should_trigger_task(self, task: ScheduleTask, current_time: datetime) -> bool:
        """判断任务是否应该触发"""
        if task.trigger_type == TriggerType.TIME:
            return await self._check_time_trigger(task, current_time)
        elif task.trigger_type == TriggerType.CUSTOM:
            return await self._check_custom_trigger(task)
        # EVENT 类型由 event_manager 触发，不在这里处理
        return False

    async def _check_time_trigger(self, task: ScheduleTask, current_time: datetime) -> bool:
        """检查时间触发条件"""
        config = task.trigger_config

        if "trigger_at" in config:
            trigger_time = config["trigger_at"]
            if isinstance(trigger_time, str):
                trigger_time = datetime.fromisoformat(trigger_time)

            if task.is_recurring and "interval_seconds" in config:
                if task.last_triggered_at is None:
                    return current_time >= trigger_time
                else:
                    elapsed = (current_time - task.last_triggered_at).total_seconds()
                    return elapsed >= config["interval_seconds"]
            else:
                return current_time >= trigger_time

        elif "delay_seconds" in config:
            if task.last_triggered_at is None:
                elapsed = (current_time - task.created_at).total_seconds()
                return elapsed >= config["delay_seconds"]
            else:
                elapsed = (current_time - task.last_triggered_at).total_seconds()
                return elapsed >= config["delay_seconds"]

        return False

    async def _check_custom_trigger(self, task: ScheduleTask) -> bool:
        """检查自定义触发条件"""
        condition_func = task.trigger_config.get("condition_func")
        if not condition_func or not callable(condition_func):
            logger.warning(f"任务 {task.task_name} 的自定义条件函数无效")
            return False

        try:
            if asyncio.iscoroutinefunction(condition_func):
                result = await condition_func()
            else:
                result = condition_func()
            return bool(result)
        except Exception as e:
            logger.error(f"执行任务 {task.task_name} 的自定义条件函数时出错: {e}", exc_info=True)
            return False

    async def _execute_callback(self, task: ScheduleTask):
        """执行任务回调函数"""
        try:
            logger.info(f"触发任务: {task.task_name} (ID: {task.schedule_id[:8]}...)")

            if asyncio.iscoroutinefunction(task.callback):
                await task.callback(*task.callback_args, **task.callback_kwargs)
            else:
                task.callback(*task.callback_args, **task.callback_kwargs)

            logger.info(f"任务 {task.task_name} 执行成功 (第 {task.trigger_count + 1} 次)")

        except Exception as e:
            logger.error(f"执行任务 {task.task_name} 的回调函数时出错: {e}", exc_info=True)

    async def _remove_task_internal(self, schedule_id: str):
        """内部方法：移除任务（不加锁）"""
        task = self._tasks.pop(schedule_id, None)
        if task:
            if task.trigger_type == TriggerType.EVENT:
                event_name = task.trigger_config.get("event_name")
                if event_name:
                    has_other_subscribers = any(
                        t.trigger_type == TriggerType.EVENT and t.trigger_config.get("event_name") == event_name
                        for t in self._tasks.values()
                    )
                    # 如果没有其他任务订阅此事件，从追踪集合中移除
                    if not has_other_subscribers and event_name in self._event_subscriptions:
                        self._event_subscriptions.discard(event_name)
                        logger.debug(f"事件 '{event_name}' 已无订阅任务，从追踪中移除")

    async def create_schedule(
        self,
        callback: Callable[..., Awaitable[Any]],
        trigger_type: TriggerType,
        trigger_config: dict[str, Any],
        is_recurring: bool = False,
        task_name: str | None = None,
        callback_args: tuple | None = None,
        callback_kwargs: dict | None = None,
    ) -> str:
        """创建调度任务（详细注释见文档）"""
        schedule_id = str(uuid.uuid4())

        task = ScheduleTask(
            schedule_id=schedule_id,
            callback=callback,
            trigger_type=trigger_type,
            trigger_config=trigger_config,
            is_recurring=is_recurring,
            task_name=task_name,
            callback_args=callback_args,
            callback_kwargs=callback_kwargs,
        )

        async with self._lock:
            self._tasks[schedule_id] = task

            if trigger_type == TriggerType.EVENT:
                event_name = trigger_config.get("event_name")
                if not event_name:
                    raise ValueError("事件触发类型必须提供 event_name")

                # 添加到追踪集合
                if event_name not in self._event_subscriptions:
                    self._event_subscriptions.add(event_name)
                    logger.debug(f"开始追踪事件: {event_name}")

        logger.info(f"创建调度任务: {task}")
        return schedule_id

    async def remove_schedule(self, schedule_id: str) -> bool:
        """移除调度任务"""
        async with self._lock:
            if schedule_id not in self._tasks:
                logger.warning(f"尝试移除不存在的任务: {schedule_id}")
                return False

            task = self._tasks[schedule_id]
            await self._remove_task_internal(schedule_id)
            logger.info(f"移除调度任务: {task.task_name} (ID: {schedule_id[:8]}...)")
            return True

    async def trigger_schedule(self, schedule_id: str) -> bool:
        """强制触发指定任务"""
        async with self._lock:
            task = self._tasks.get(schedule_id)
            if not task:
                logger.warning(f"尝试触发不存在的任务: {schedule_id}")
                return False

            if not task.is_active:
                logger.warning(f"尝试触发已停用的任务: {task.task_name}")
                return False

            await self._execute_callback(task)
            task.last_triggered_at = datetime.now()
            task.trigger_count += 1

            if not task.is_recurring:
                await self._remove_task_internal(schedule_id)

            return True

    async def pause_schedule(self, schedule_id: str) -> bool:
        """暂停任务（不删除）"""
        async with self._lock:
            task = self._tasks.get(schedule_id)
            if not task:
                logger.warning(f"尝试暂停不存在的任务: {schedule_id}")
                return False

            task.is_active = False
            logger.info(f"暂停任务: {task.task_name} (ID: {schedule_id[:8]}...)")
            return True

    async def resume_schedule(self, schedule_id: str) -> bool:
        """恢复任务"""
        async with self._lock:
            task = self._tasks.get(schedule_id)
            if not task:
                logger.warning(f"尝试恢复不存在的任务: {schedule_id}")
                return False

            task.is_active = True
            logger.info(f"恢复任务: {task.task_name} (ID: {schedule_id[:8]}...)")
            return True

    async def get_task_info(self, schedule_id: str) -> dict[str, Any] | None:
        """获取任务信息"""
        async with self._lock:
            task = self._tasks.get(schedule_id)
            if not task:
                return None

            return {
                "schedule_id": task.schedule_id,
                "task_name": task.task_name,
                "trigger_type": task.trigger_type.value,
                "is_recurring": task.is_recurring,
                "is_active": task.is_active,
                "created_at": task.created_at.isoformat(),
                "last_triggered_at": task.last_triggered_at.isoformat() if task.last_triggered_at else None,
                "trigger_count": task.trigger_count,
                "trigger_config": task.trigger_config.copy(),
            }

    async def list_tasks(self, trigger_type: TriggerType | None = None) -> list[dict[str, Any]]:
        """列出所有任务或指定类型的任务"""
        async with self._lock:
            tasks = []
            for task in self._tasks.values():
                if trigger_type is None or task.trigger_type == trigger_type:
                    task_info = await self.get_task_info(task.schedule_id)
                    if task_info:
                        tasks.append(task_info)
            return tasks

    def get_statistics(self) -> dict[str, Any]:
        """获取调度器统计信息"""
        total_tasks = len(self._tasks)
        active_tasks = sum(1 for task in self._tasks.values() if task.is_active)
        recurring_tasks = sum(1 for task in self._tasks.values() if task.is_recurring)

        tasks_by_type = {
            TriggerType.TIME.value: 0,
            TriggerType.EVENT.value: 0,
            TriggerType.CUSTOM.value: 0,
        }

        for task in self._tasks.values():
            tasks_by_type[task.trigger_type.value] += 1

        return {
            "is_running": self._running,
            "total_tasks": total_tasks,
            "active_tasks": active_tasks,
            "paused_tasks": total_tasks - active_tasks,
            "recurring_tasks": recurring_tasks,
            "one_time_tasks": total_tasks - recurring_tasks,
            "tasks_by_type": tasks_by_type,
            "registered_events": list(self._event_subscriptions),
        }


# 全局调度器实例
unified_scheduler = UnifiedScheduler()

async def initialize_scheduler():
    """初始化调度器
    
    这个函数应该在 bot 启动时调用
    """
    try:
        logger.info("正在启动统一调度器...")
        await unified_scheduler.start()
        logger.info("统一调度器启动成功")

        # 获取初始统计信息
        stats = unified_scheduler.get_statistics()
        logger.info(f"调度器状态: {stats}")

    except Exception as e:
        logger.error(f"启动统一调度器失败: {e}", exc_info=True)
        raise


async def shutdown_scheduler():
    """关闭调度器
    
    这个函数应该在 bot 关闭时调用
    """
    try:
        logger.info("正在关闭统一调度器...")

        # 显示最终统计
        stats = unified_scheduler.get_statistics()
        logger.info(f"调度器最终统计: {stats}")

        # 列出剩余任务
        remaining_tasks = await unified_scheduler.list_tasks()
        if remaining_tasks:
            logger.warning(f"检测到 {len(remaining_tasks)} 个未清理的任务:")
            for task in remaining_tasks:
                logger.warning(f"  - {task['task_name']} (ID: {task['schedule_id'][:8]}...)")

        await unified_scheduler.stop()
        logger.info("统一调度器已关闭")

    except Exception as e:
        logger.error(f"关闭统一调度器失败: {e}", exc_info=True)
