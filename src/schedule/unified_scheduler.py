"""
统一调度器模块
提供统一的任务调度接口，支持时间触发、事件触发和自定义条件触发
"""

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from enum import Enum
from typing import Any

from src.common.logger import get_logger
from src.plugin_system.base.component_types import EventType

logger = get_logger("unified_scheduler")


class DeadlockDetector:
    """死锁检测器

    用于检测长时间运行的任务，防止死锁
    """
    def __init__(self, deadlock_timeout: float = 300.0):
        """
        Args:
            deadlock_timeout: 死锁超时时间（秒），默认5分钟
        """
        self._task_start_times: dict[str, float] = {}
        self._deadlock_timeout = deadlock_timeout

    def register_task_start(self, task_id: str) -> None:
        """注册任务开始时间"""
        self._task_start_times[task_id] = time.time()

    def unregister_task(self, task_id: str) -> None:
        """取消注册任务"""
        self._task_start_times.pop(task_id, None)

    def check_for_deadlocks(self) -> list[str]:
        """检查可能的死锁任务

        Returns:
            List[str]: 可能死锁的任务ID列表
        """
        current_time = time.time()
        deadlocked_tasks = []

        for task_id, start_time in self._task_start_times.items():
            if current_time - start_time > self._deadlock_timeout:
                deadlocked_tasks.append(task_id)

        return deadlocked_tasks

    def get_task_runtime(self, task_id: str) -> float:
        """获取任务运行时间

        Args:
            task_id: 任务ID

        Returns:
            float: 运行时间（秒），如果任务不存在返回0
        """
        start_time = self._task_start_times.get(task_id)
        if start_time:
            return time.time() - start_time
        return 0.0


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
    - 内置死锁检测和恢复机制
    """

    def __init__(self):
        self._tasks: dict[str, ScheduleTask] = {}
        self._running = False
        self._check_task: asyncio.Task | None = None
        self._event_subscriptions: set[str] = set()  # 追踪已订阅的事件
        self._executing_tasks: dict[str, asyncio.Task] = {}  # 追踪正在执行的任务
        # 🔧 新增：死锁检测器
        self._deadlock_detector = DeadlockDetector(deadlock_timeout=300.0)
        self._deadlock_check_task: asyncio.Task | None = None
        # 移除锁机制，使用无锁设计（基于 asyncio 单线程特性）

    async def _handle_event_trigger(self, event_name: str | EventType, event_params: dict[str, Any]) -> None:
        """处理来自 event_manager 的事件通知

        此方法由 event_manager 在触发事件时直接调用
        无锁设计：基于 asyncio 单线程特性，避免死锁
        """
        # 获取订阅该事件的所有任务
        event_tasks = []
        for task in self._tasks.values():
            if (task.trigger_type == TriggerType.EVENT
                and task.trigger_config.get("event_name") == event_name
                and task.is_active):

                # 检查事件任务是否已经在执行中，防止重复触发
                if task.schedule_id in self._executing_tasks:
                    executing_task = self._executing_tasks[task.schedule_id]
                    if not executing_task.done():
                        logger.debug(f"[调度器] 事件任务 {task.task_name} 仍在执行中，跳过本次触发")
                        continue
                    else:
                        # 任务已完成但未清理，先清理
                        self._executing_tasks.pop(task.schedule_id, None)

                event_tasks.append(task)

        if not event_tasks:
            logger.debug(f"[调度器] 事件 '{event_name}' 没有对应的调度任务")
            return

        logger.debug(f"[调度器] 事件 '{event_name}' 触发，共有 {len(event_tasks)} 个调度任务")

        # 并发执行所有事件任务（无锁设计）
        execution_tasks = []
        for task in event_tasks:
            # 🔧 新增：在死锁检测器中注册任务开始
            self._deadlock_detector.register_task_start(task.schedule_id)

            execution_task = asyncio.create_task(
                self._execute_event_task_callback(task, event_params),
                name=f"execute_event_{task.task_name}"
            )
            execution_tasks.append(execution_task)

            # 追踪正在执行的任务
            self._executing_tasks[task.schedule_id] = execution_task

        # 等待所有任务完成
        results = await asyncio.gather(*execution_tasks, return_exceptions=True)

        # 清理执行追踪
        for task in event_tasks:
            self._executing_tasks.pop(task.schedule_id, None)
            # 🔧 新增：从死锁检测器中移除任务
            self._deadlock_detector.unregister_task(task.schedule_id)

        # 收集需要移除的任务
        tasks_to_remove = []
        for task, result in zip(event_tasks, results):
            if isinstance(result, Exception):
                logger.error(f"[调度器] 执行事件任务 {task.task_name} 时发生错误: {result}", exc_info=result)
            elif result is True and not task.is_recurring:
                # 成功执行且是一次性任务，标记为删除
                tasks_to_remove.append(task.schedule_id)
                logger.debug(f"[调度器] 一次性事件任务 {task.task_name} 已完成，将被移除")

        # 移除已完成的一次性任务（无锁设计）
        for schedule_id in tasks_to_remove:
            await self._remove_task_internal(schedule_id)

    async def start(self):
        """启动调度器"""
        if self._running:
            logger.warning("调度器已在运行中")
            return

        self._running = True
        self._check_task = asyncio.create_task(self._check_loop())
        # 🔧 新增：启动死锁检测任务
        self._deadlock_check_task = asyncio.create_task(self._deadlock_check_loop())

        # 注册回调到 event_manager
        try:
            from src.plugin_system.core.event_manager import event_manager

            event_manager.register_scheduler_callback(self._handle_event_trigger)
            logger.debug("调度器已注册到 event_manager")
        except ImportError:
            logger.warning("无法导入 event_manager，事件触发功能将不可用")

        logger.info("统一调度器已启动")

    async def stop(self):
        """停止调度器"""
        if not self._running:
            return

        self._running = False

        # 🔧 修复：停止死锁检测任务
        if self._deadlock_check_task:
            self._deadlock_check_task.cancel()
            try:
                await self._deadlock_check_task
            except asyncio.CancelledError:
                pass

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
            logger.debug("调度器回调已从 event_manager 注销")
        except ImportError:
            pass

        # 取消所有正在执行的任务（无锁设计）
        executing_tasks = list(self._executing_tasks.values())
        if executing_tasks:
            logger.debug(f"取消 {len(executing_tasks)} 个正在执行的任务")

            # 在取消任务前先清空追踪
            self._executing_tasks.clear()

            # 取消任务
            for task in executing_tasks:
                if not task.done():
                    task.cancel()

            # 等待所有任务取消完成，使用较长的超时时间
            try:
                await asyncio.wait_for(
                    asyncio.gather(*executing_tasks, return_exceptions=True),
                    timeout=10.0
                )
            except asyncio.TimeoutError:
                logger.warning("部分任务取消超时，强制停止")

        logger.info("统一调度器已停止")
        # 清空所有资源
        self._tasks.clear()
        self._event_subscriptions.clear()
        self._executing_tasks.clear()
        # 🔧 新增：清理死锁检测器
        if hasattr(self, '_deadlock_detector'):
            self._deadlock_detector._task_start_times.clear()

    async def _check_loop(self):
        """主循环：每秒检查一次所有任务"""
        logger.debug("调度器检查循环已启动")
        while self._running:
            try:
                await asyncio.sleep(1)
                asyncio.create_task(self._check_and_trigger_tasks())
            except asyncio.CancelledError:
                logger.debug("调度器检查循环被取消")
                break
            except Exception as e:
                logger.error(f"调度器检查循环发生错误: {e}", exc_info=True)

    async def _deadlock_check_loop(self):
        """死锁检测循环：每30秒检查一次是否有死锁任务"""
        logger.debug("死锁检测循环已启动")
        while self._running:
            try:
                await asyncio.sleep(30)
                deadlocked_tasks = self._deadlock_detector.check_for_deadlocks()

                if deadlocked_tasks:
                    logger.warning(f"检测到 {len(deadlocked_tasks)} 个可能的死锁任务: {deadlocked_tasks}")

                    # 尝试恢复死锁任务
                    for schedule_id in deadlocked_tasks:
                        await self._handle_deadlocked_task(schedule_id)

            except asyncio.CancelledError:
                logger.debug("死锁检测循环被取消")
                break
            except Exception as e:
                logger.error(f"死锁检测循环发生错误: {e}", exc_info=True)

    async def _handle_deadlocked_task(self, schedule_id: str) -> None:
        """处理死锁任务"""
        task = self._tasks.get(schedule_id)
        if not task:
            # 任务不存在，清理检测器中的记录
            self._deadlock_detector.unregister_task(schedule_id)
            return

        runtime = self._deadlock_detector.get_task_runtime(schedule_id)
        logger.warning(f"任务 {task.task_name} 已运行 {runtime:.1f} 秒，可能已死锁")

        # 获取执行中的任务
        executing_task = self._executing_tasks.get(schedule_id)
        if executing_task and not executing_task.done():
            # 强制取消任务
            logger.warning(f"强制取消死锁任务: {task.task_name}")
            try:
                executing_task.cancel()
                # 等待任务取消，但使用较短的超时
                await asyncio.wait_for(executing_task, timeout=5.0)
                logger.info(f"死锁任务 {task.task_name} 已成功取消")
            except asyncio.TimeoutError:
                logger.error(f"无法取消死锁任务 {task.task_name}，可能需要重启系统")
            except Exception as e:
                logger.error(f"取消死锁任务 {task.task_name} 时发生错误: {e}")

            # 清理执行追踪
            self._executing_tasks.pop(schedule_id, None)

        # 从检测器中移除记录
        self._deadlock_detector.unregister_task(schedule_id)

    async def _check_and_trigger_tasks(self):
        """检查并触发到期任务

        无锁设计：基于 asyncio 单线程特性，避免死锁和阻塞
        """
        current_time = datetime.now()

        # 收集需要触发的任务
        tasks_to_trigger = []

        for schedule_id, task in list(self._tasks.items()):
            if not task.is_active:
                continue

            # 检查任务是否已经在执行中，防止重复触发
            if schedule_id in self._executing_tasks:
                executing_task = self._executing_tasks[schedule_id]
                if not executing_task.done():
                    logger.debug(f"[调度器] 任务 {task.task_name} 仍在执行中，跳过本次触发")
                    continue
                else:
                    # 任务已完成但未清理，先清理
                    self._executing_tasks.pop(schedule_id, None)

            try:
                should_trigger = await self._should_trigger_task(task, current_time)
                if should_trigger:
                    tasks_to_trigger.append(task)
            except Exception as e:
                logger.error(f"检查任务 {task.task_name} 时发生错误: {e}", exc_info=True)

        # 第二阶段：并发执行所有回调（无锁设计）
        if not tasks_to_trigger:
            return

        # 为每个任务创建独立的异步任务，确保并发执行
        execution_tasks = []
        for task in tasks_to_trigger:
            # 🔧 新增：在死锁检测器中注册任务开始
            self._deadlock_detector.register_task_start(task.schedule_id)

            execution_task = asyncio.create_task(
                self._execute_task_callback(task, current_time),
                name=f"execute_{task.task_name}"
            )
            execution_tasks.append(execution_task)

            # 追踪正在执行的任务，以便在 remove_schedule 时可以取消
            self._executing_tasks[task.schedule_id] = execution_task

        # 等待所有任务完成（使用 return_exceptions=True 避免单个任务失败影响其他任务）
        results = await asyncio.gather(*execution_tasks, return_exceptions=True)

        # 清理执行追踪
        for task in tasks_to_trigger:
            self._executing_tasks.pop(task.schedule_id, None)
            # 🔧 新增：从死锁检测器中移除任务
            self._deadlock_detector.unregister_task(task.schedule_id)

        # 第三阶段：收集需要移除的任务并移除（无锁设计）
        tasks_to_remove = []
        for task, result in zip(tasks_to_trigger, results):
            if isinstance(result, Exception):
                logger.error(f"[调度器] 执行任务 {task.task_name} 时发生错误: {result}", exc_info=result)
            elif result is True and not task.is_recurring:
                # 成功执行且是一次性任务，标记为删除
                tasks_to_remove.append(task.schedule_id)
                logger.debug(f"[调度器] 一次性任务 {task.task_name} 已完成，将被移除")

        # 移除已完成的一次性任务
        for schedule_id in tasks_to_remove:
            await self._remove_task_internal(schedule_id)

    async def _execute_task_callback(self, task: ScheduleTask, current_time: datetime) -> bool:
        """执行单个任务的回调（用于并发执行）

        Args:
            task: 要执行的任务
            current_time: 当前时间

        Returns:
            bool: 执行是否成功
        """
        try:
            logger.debug(f"[调度器] 触发任务: {task.task_name}")

            # 执行回调
            await self._execute_callback(task)

            # 更新任务状态
            task.last_triggered_at = current_time
            task.trigger_count += 1

            logger.debug(f"[调度器] 任务 {task.task_name} 执行完成")
            return True

        except Exception as e:
            logger.error(f"[调度器] 执行任务 {task.task_name} 时发生错误: {e}", exc_info=True)
            return False

    async def _execute_event_task_callback(self, task: ScheduleTask, event_params: dict[str, Any]) -> bool:
        """执行单个事件任务的回调（用于并发执行）

        Args:
            task: 要执行的任务
            event_params: 事件参数

        Returns:
            bool: 执行是否成功
        """
        try:
            logger.debug(f"[调度器] 执行事件任务: {task.task_name}")

            current_time = datetime.now()

            # 执行回调，传入事件参数
            if event_params:
                if asyncio.iscoroutinefunction(task.callback):
                    await task.callback(**event_params)
                else:
                    task.callback(**event_params)
            else:
                await self._execute_callback(task)

            # 更新任务状态
            task.last_triggered_at = current_time
            task.trigger_count += 1

            logger.debug(f"[调度器] 事件任务 {task.task_name} 执行完成")
            return True

        except Exception as e:
            logger.error(f"[调度器] 执行事件任务 {task.task_name} 时发生错误: {e}", exc_info=True)
            return False

    async def _execute_trigger_task_callback(self, task: ScheduleTask) -> bool:
        """执行强制触发的任务回调

        Args:
            task: 要执行的任务

        Returns:
            bool: 执行是否成功
        """
        try:
            logger.debug(f"[调度器] 强制触发任务: {task.task_name}")

            # 执行回调
            await self._execute_callback(task)

            # 更新任务状态
            current_time = datetime.now()
            task.last_triggered_at = current_time
            task.trigger_count += 1

            logger.debug(f"[调度器] 强制触发任务 {task.task_name} 执行完成")

            # 如果不是循环任务，需要移除
            if not task.is_recurring:
                await self._remove_task_internal(task.schedule_id)
                logger.debug(f"[调度器] 一次性任务 {task.task_name} 已完成并移除")

            return True

        except Exception as e:
            logger.error(f"[调度器] 强制触发任务 {task.task_name} 时发生错误: {e}", exc_info=True)
            return False

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
            logger.debug(f"触发任务: {task.task_name}")

            if asyncio.iscoroutinefunction(task.callback):
                await task.callback(*task.callback_args, **task.callback_kwargs)
            else:
                task.callback(*task.callback_args, **task.callback_kwargs)

            logger.debug(f"任务 {task.task_name} 执行完成")

        except Exception as e:
            logger.error(f"执行任务 {task.task_name} 的回调函数时出错: {e}", exc_info=True)

    async def _remove_task_internal(self, schedule_id: str):
        """内部方法：移除任务（无锁设计）"""
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
        force_overwrite: bool = False,
    ) -> str:
        """创建调度任务（无锁设计）

        Args:
            callback: 回调函数
            trigger_type: 触发类型
            trigger_config: 触发配置
            is_recurring: 是否循环任务
            task_name: 任务名称，如果指定则检查是否已存在同名任务
            callback_args: 回调函数位置参数
            callback_kwargs: 回调函数关键字参数
            force_overwrite: 如果同名任务已存在，是否强制覆盖

        Returns:
            str: 创建的schedule_id

        Raises:
            ValueError: 如果同名任务已存在且未启用强制覆盖
        """
        # 检查任务名称是否已存在
        if task_name is not None:
            existing_task = None
            existing_schedule_id = None

            for sid, task in self._tasks.items():
                if task.task_name == task_name and task.is_active:
                    existing_task = task
                    existing_schedule_id = sid
                    break

            if existing_task is not None:
                if force_overwrite:
                    logger.info(f"检测到同名活跃任务 '{task_name}'，强制覆盖模式已启用，移除现有任务")
                    await self.remove_schedule(existing_schedule_id)
                else:
                    raise ValueError(
                        f"任务名称 '{task_name}' 已存在活跃任务 (ID: {existing_schedule_id[:8]}...)。"
                        f"如需覆盖，请设置 force_overwrite=True"
                    )

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

        # 存储任务（无锁操作）
        self._tasks[schedule_id] = task

        if trigger_type == TriggerType.EVENT:
            event_name = trigger_config.get("event_name")
            if not event_name:
                raise ValueError("事件触发类型必须提供 event_name")

            # 添加到追踪集合
            if event_name not in self._event_subscriptions:
                self._event_subscriptions.add(event_name)
                logger.debug(f"开始追踪事件: {event_name}")

        logger.debug(f"创建调度任务: {task.task_name}")
        return schedule_id

    async def find_schedule_by_name(self, task_name: str) -> str | None:
        """根据任务名称查找schedule_id

        Args:
            task_name: 任务名称

        Returns:
            str | None: 找到的schedule_id，如果不存在则返回None
        """
        for schedule_id, task in self._tasks.items():
            if task.task_name == task_name and task.is_active:
                return schedule_id
        return None

    async def remove_schedule_by_name(self, task_name: str) -> bool:
        """根据任务名称移除调度任务

        Args:
            task_name: 任务名称

        Returns:
            bool: 是否成功移除
        """
        schedule_id = await self.find_schedule_by_name(task_name)
        if schedule_id:
            return await self.remove_schedule(schedule_id)
        return False

    async def remove_schedule(self, schedule_id: str) -> bool:
        """移除调度任务（改进的取消机制）

        如果任务正在执行，会取消执行中的任务
        """
        # 获取任务信息
        if schedule_id not in self._tasks:
            logger.warning(f"尝试移除不存在的任务: {schedule_id}")
            return False

        task = self._tasks[schedule_id]
        executing_task = self._executing_tasks.get(schedule_id)

        # 🔧 修复：改进任务取消机制，避免死锁
        if executing_task and not executing_task.done():
            logger.debug(f"取消正在执行的任务: {task.task_name}")
            try:
                executing_task.cancel()
                # 使用更长的超时时间，并添加异常处理
                await asyncio.wait_for(executing_task, timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning(f"取消任务 {task.task_name} 超时，可能存在死锁风险")
                # 不再强制移除，让任务自然完成
                return False
            except Exception as e:
                logger.error(f"取消任务 {task.task_name} 时发生未预期的错误: {e}")
                return False

        # 移除任务
        await self._remove_task_internal(schedule_id)

        # 清理执行追踪
        self._executing_tasks.pop(schedule_id, None)

        logger.debug(f"移除调度任务: {task.task_name}")
        return True

    def get_executing_task(self, schedule_id: str) -> asyncio.Task | None:
        """获取指定schedule_id的正在执行的任务

        Args:
            schedule_id: 调度任务ID

        Returns:
            asyncio.Task | None: 正在执行的任务，如果不在执行中则返回None
        """
        executing_task = self._executing_tasks.get(schedule_id)
        if executing_task and not executing_task.done():
            return executing_task
        return None

    def get_all_executing_tasks(self) -> dict[str, asyncio.Task]:
        """获取所有正在执行的任务

        Returns:
            dict[str, asyncio.Task]: schedule_id -> executing_task 的映射
        """
        # 过滤出未完成的任务
        return {
            schedule_id: task
            for schedule_id, task in self._executing_tasks.items()
            if not task.done()
        }

    async def trigger_schedule(self, schedule_id: str) -> bool:
        """强制触发指定任务（无锁设计）"""
        # 获取任务信息
        task = self._tasks.get(schedule_id)
        if not task:
            logger.warning(f"尝试触发不存在的任务: {schedule_id}")
            return False

        if not task.is_active:
            logger.warning(f"尝试触发已停用的任务: {task.task_name}")
            return False

        # 检查任务是否已经在执行中
        executing_task = self._executing_tasks.get(schedule_id)
        if executing_task and not executing_task.done():
            logger.warning(f"任务 {task.task_name} 已在执行中，无法重复触发")
            return False

        # 清理已完成的任务
        if executing_task and executing_task.done():
            self._executing_tasks.pop(schedule_id, None)
            self._deadlock_detector.unregister_task(schedule_id)

        # 🔧 新增：在死锁检测器中注册任务开始
        self._deadlock_detector.register_task_start(schedule_id)

        # 创建执行任务
        execution_task = asyncio.create_task(
            self._execute_trigger_task_callback(task),
            name=f"trigger_{task.task_name}"
        )

        # 追踪执行任务
        self._executing_tasks[schedule_id] = execution_task

        # 等待任务完成
        try:
            result = await execution_task
            return result
        finally:
            # 清理执行追踪
            self._executing_tasks.pop(schedule_id, None)
            # 🔧 新增：从死锁检测器中移除任务
            self._deadlock_detector.unregister_task(schedule_id)

    async def pause_schedule(self, schedule_id: str) -> bool:
        """暂停任务（不删除）"""
        task = self._tasks.get(schedule_id)
        if not task:
            logger.warning(f"尝试暂停不存在的任务: {schedule_id}")
            return False

        task.is_active = False
        logger.debug(f"暂停任务: {task.task_name}")
        return True

    async def resume_schedule(self, schedule_id: str) -> bool:
        """恢复任务"""
        task = self._tasks.get(schedule_id)
        if not task:
            logger.warning(f"尝试恢复不存在的任务: {schedule_id}")
            return False

        task.is_active = True
        logger.debug(f"恢复任务: {task.task_name}")
        return True

    async def get_task_info(self, schedule_id: str) -> dict[str, Any] | None:
        """获取任务信息"""
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
        executing_tasks = sum(1 for task in self._executing_tasks.values() if not task.done())

        tasks_by_type = {
            TriggerType.TIME.value: 0,
            TriggerType.EVENT.value: 0,
            TriggerType.CUSTOM.value: 0,
        }

        for task in self._tasks.values():
            tasks_by_type[task.trigger_type.value] += 1

        # 获取正在执行的任务详细信息
        executing_tasks_info = []
        for schedule_id, executing_task in self._executing_tasks.items():
            if not executing_task.done():
                task = self._tasks.get(schedule_id)
                executing_tasks_info.append({
                    "schedule_id": schedule_id[:8] + "...",
                    "task_name": task.task_name if task else "Unknown",
                    "task_obj_name": executing_task.get_name() if hasattr(executing_task, 'get_name') else str(executing_task),
                })

        # 🔧 新增：获取死锁检测统计
        deadlock_stats = {
            "monitored_tasks": len(self._deadlock_detector._task_start_times),
            "deadlock_timeout": self._deadlock_detector._deadlock_timeout,
        }

        return {
            "is_running": self._running,
            "total_tasks": total_tasks,
            "active_tasks": active_tasks,
            "paused_tasks": total_tasks - active_tasks,
            "recurring_tasks": recurring_tasks,
            "one_time_tasks": total_tasks - recurring_tasks,
            "executing_tasks": executing_tasks,
            "executing_tasks_info": executing_tasks_info,
            "tasks_by_type": tasks_by_type,
            "registered_events": list(self._event_subscriptions),
            # 🔧 新增：死锁检测统计
            "deadlock_detection": deadlock_stats,
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
