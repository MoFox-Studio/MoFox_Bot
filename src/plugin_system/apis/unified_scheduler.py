"""
统一调度器模块 (重构版)
提供统一的任务调度接口，支持时间触发、事件触发和自定义条件触发

核心特性:
1. 完全无锁设计 - 基于 asyncio 单线程特性，避免死锁
2. 任务隔离 - 每个任务独立执行，互不阻塞
3. 优雅降级 - 失败任务不影响其他任务
4. 资源管理 - 自动清理完成的任务，防止资源泄漏
5. 死锁检测 - 多级超时机制和强制恢复
6. 并发控制 - 可配置并发限制，防止资源耗尽
"""

import asyncio
import time
import uuid
import weakref
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from src.common.logger import get_logger
from src.plugin_system.base.component_types import EventType

logger = get_logger("unified_scheduler")


# ==================== 配置和常量 ====================


@dataclass
class SchedulerConfig:
    """调度器配置"""

    # 检查间隔
    check_interval: float = 1.0  # 主循环检查间隔(秒)
    deadlock_check_interval: float = 30.0  # 死锁检查间隔(秒)

    # 超时配置
    task_default_timeout: float = 300.0  # 默认任务超时(5分钟)
    task_cancel_timeout: float = 10.0  # 任务取消超时(10秒)
    shutdown_timeout: float = 30.0  # 关闭超时(30秒)
    deadlock_threshold: float = 600.0  # 死锁检测阈值(10分钟，超过此时间视为死锁)

    # 并发控制
    max_concurrent_tasks: int = 100  # 最大并发任务数
    enable_task_semaphore: bool = True  # 是否启用任务信号量

    # 重试配置
    enable_retry: bool = True  # 是否启用失败重试
    max_retries: int = 3  # 最大重试次数
    retry_delay: float = 5.0  # 重试延迟(秒)

    # 资源管理
    cleanup_interval: float = 60.0  # 清理已完成任务的间隔(秒)
    keep_completed_tasks: int = 100  # 保留的已完成任务数(用于统计)


# ==================== 枚举类型 ====================


class TriggerType(Enum):
    """触发类型枚举"""

    TIME = "time"  # 时间触发
    EVENT = "event"  # 事件触发（通过 event_manager）
    CUSTOM = "custom"  # 自定义条件触发


class TaskStatus(Enum):
    """任务状态枚举"""

    PENDING = "pending"  # 等待触发
    RUNNING = "running"  # 正在执行
    COMPLETED = "completed"  # 已完成
    FAILED = "failed"  # 执行失败
    CANCELLED = "cancelled"  # 已取消
    PAUSED = "paused"  # 已暂停
    TIMEOUT = "timeout"  # 执行超时


# ==================== 任务模型 ====================


@dataclass
class TaskExecution:
    """任务执行记录"""

    execution_id: str
    started_at: datetime
    ended_at: datetime | None = None
    status: TaskStatus = TaskStatus.RUNNING
    error: Exception | None = None
    result: Any = None
    duration: float = 0.0

    def complete(self, result: Any = None) -> None:
        """标记执行完成"""
        self.ended_at = datetime.now()
        self.status = TaskStatus.COMPLETED
        self.result = result
        self.duration = (self.ended_at - self.started_at).total_seconds()

    def fail(self, error: Exception) -> None:
        """标记执行失败"""
        self.ended_at = datetime.now()
        self.status = TaskStatus.FAILED
        self.error = error
        self.duration = (self.ended_at - self.started_at).total_seconds()

    def cancel(self) -> None:
        """标记执行取消"""
        self.ended_at = datetime.now()
        self.status = TaskStatus.CANCELLED
        self.duration = (self.ended_at - self.started_at).total_seconds()


@dataclass
class ScheduleTask:
    """调度任务模型（重构版）"""

    # 基本信息
    schedule_id: str
    task_name: str
    callback: Callable[..., Awaitable[Any]]

    # 触发配置
    trigger_type: TriggerType
    trigger_config: dict[str, Any]
    is_recurring: bool = False

    # 回调参数
    callback_args: tuple = field(default_factory=tuple)
    callback_kwargs: dict = field(default_factory=dict)

    # 状态信息
    status: TaskStatus = TaskStatus.PENDING
    created_at: datetime = field(default_factory=datetime.now)
    last_triggered_at: datetime | None = None
    next_trigger_at: datetime | None = None

    # 统计信息
    trigger_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    total_execution_time: float = 0.0

    # 执行记录（弱引用，避免内存泄漏）
    execution_history: list[TaskExecution] = field(default_factory=list)
    current_execution: TaskExecution | None = None

    # 重试配置
    max_retries: int = 0
    retry_count: int = 0
    last_error: Exception | None = None

    # 超时配置
    timeout: float | None = None

    # 运行时引用
    _asyncio_task: asyncio.Task | None = field(default=None, init=False, repr=False)
    _weak_scheduler: Any = field(default=None, init=False, repr=False)

    def __repr__(self) -> str:
        return (
            f"ScheduleTask(id={self.schedule_id[:8]}..., "
            f"name={self.task_name}, type={self.trigger_type.value}, "
            f"status={self.status.value}, recurring={self.is_recurring})"
        )

    def is_active(self) -> bool:
        """任务是否活跃（可以被触发）"""
        return self.status in (TaskStatus.PENDING, TaskStatus.RUNNING)

    def can_trigger(self) -> bool:
        """任务是否可以被触发"""
        return self.status == TaskStatus.PENDING

    def start_execution(self) -> TaskExecution:
        """开始新的执行"""
        execution = TaskExecution(execution_id=str(uuid.uuid4()), started_at=datetime.now())
        self.current_execution = execution
        self.status = TaskStatus.RUNNING
        return execution

    def finish_execution(self, success: bool, result: Any = None, error: Exception | None = None) -> None:
        """完成当前执行"""
        if not self.current_execution:
            return

        if success:
            self.current_execution.complete(result)
            self.success_count += 1
            self.retry_count = 0  # 重置重试计数
        else:
            self.current_execution.fail(error or Exception("Unknown error"))
            self.failure_count += 1
            self.last_error = error

        self.total_execution_time += self.current_execution.duration

        # 保留最近10条执行记录
        self.execution_history.append(self.current_execution)
        if len(self.execution_history) > 10:
            self.execution_history.pop(0)

        self.current_execution = None
        self.last_triggered_at = datetime.now()
        self.trigger_count += 1

        # 更新状态
        if self.is_recurring:
            self.status = TaskStatus.PENDING
        else:
            self.status = TaskStatus.COMPLETED if success else TaskStatus.FAILED


# ==================== 死锁检测器（重构版）====================


class DeadlockDetector:
    """死锁检测器（重构版）

    功能增强:
    1. 多级超时检测
    2. 任务健康度评分
    3. 自动恢复建议
    """

    def __init__(self, config: SchedulerConfig):
        self.config = config
        self._monitored_tasks: dict[str, tuple[float, str]] = {}  # task_id -> (start_time, task_name)
        self._timeout_history: defaultdict[str, list[float]] = defaultdict(list)  # task_id -> [timeout_times]

    def register_task(self, task_id: str, task_name: str) -> None:
        """注册任务开始监控"""
        self._monitored_tasks[task_id] = (time.time(), task_name)

    def unregister_task(self, task_id: str) -> None:
        """取消注册任务"""
        self._monitored_tasks.pop(task_id, None)

    def get_running_time(self, task_id: str) -> float:
        """获取任务运行时间"""
        if task_id not in self._monitored_tasks:
            return 0.0
        start_time, _ = self._monitored_tasks[task_id]
        return time.time() - start_time

    def check_deadlocks(self) -> list[tuple[str, float, str]]:
        """检查死锁任务

        Returns:
            List[Tuple[task_id, runtime, task_name]]: 疑似死锁的任务列表
        """
        current_time = time.time()
        deadlocked = []

        for task_id, (start_time, task_name) in list(self._monitored_tasks.items()):
            runtime = current_time - start_time
            # 使用死锁阈值而不是默认超时
            if runtime > self.config.deadlock_threshold:
                deadlocked.append((task_id, runtime, task_name))

        return deadlocked

    def record_timeout(self, task_id: str) -> None:
        """记录超时事件"""
        self._timeout_history[task_id].append(time.time())
        # 只保留最近10次记录
        if len(self._timeout_history[task_id]) > 10:
            self._timeout_history[task_id].pop(0)

    def get_health_score(self, task_id: str) -> float:
        """计算任务健康度 (0.0-1.0)

        基于超时频率计算，频繁超时的任务健康度低
        """
        if task_id not in self._timeout_history:
            return 1.0

        timeouts = self._timeout_history[task_id]
        if not timeouts:
            return 1.0

        # 最近10次执行中的超时次数
        recent_count = len(timeouts)
        # 健康度 = 1 - (超时次数 / 10)
        return max(0.0, 1.0 - (recent_count / 10.0))

    def clear(self) -> None:
        """清空所有监控数据"""
        self._monitored_tasks.clear()
        self._timeout_history.clear()


# ==================== 统一调度器（完全重构版）====================


class UnifiedScheduler:
    """统一调度器（完全重构版）

    核心改进:
    1. 完全无锁设计 - 利用 asyncio 的单线程特性
    2. 任务完全隔离 - 使用独立的 Task，互不阻塞
    3. 多级超时保护 - 任务超时、取消超时、关闭超时
    4. 优雅降级 - 单个任务失败不影响整体
    5. 资源自动清理 - 防止内存泄漏
    6. 并发控制 - 可配置的并发限制
    7. 健康监控 - 任务健康度评分和统计

    特点：
    - 每秒检查一次所有任务
    - 自动执行到期任务
    - 支持循环和一次性任务
    - 提供完整的任务管理API
    - 与 event_manager 集成
    - 内置死锁检测和恢复机制
    """

    def __init__(self, config: SchedulerConfig | None = None):
        self.config = config or SchedulerConfig()

        # 任务存储
        self._tasks: dict[str, ScheduleTask] = {}
        self._tasks_by_name: dict[str, str] = {}  # task_name -> schedule_id 快速查找

        # 运行状态
        self._running = False
        self._stopping = False

        # 后台任务
        self._check_loop_task: asyncio.Task | None = None
        self._deadlock_check_task: asyncio.Task | None = None
        self._cleanup_task: asyncio.Task | None = None

        # 事件订阅追踪
        self._event_subscriptions: dict[str | EventType, set[str]] = defaultdict(set)  # event -> {task_ids}

        # 死锁检测器
        self._deadlock_detector = DeadlockDetector(self.config)

        # 并发控制
        self._task_semaphore: asyncio.Semaphore | None = None
        if self.config.enable_task_semaphore:
            self._task_semaphore = asyncio.Semaphore(self.config.max_concurrent_tasks)

        # 统计信息
        self._total_executions = 0
        self._total_failures = 0
        self._total_timeouts = 0
        self._start_time: datetime | None = None

        # 已完成任务缓存（用于统计）
        self._completed_tasks: list[ScheduleTask] = []

    # ==================== 生命周期管理 ====================

    async def start(self) -> None:
        """启动调度器"""
        if self._running:
            logger.warning("调度器已在运行中")
            return

        logger.info("正在启动统一调度器...")
        self._running = True
        self._stopping = False
        self._start_time = datetime.now()

        # 启动后台任务
        self._check_loop_task = asyncio.create_task(self._check_loop(), name="scheduler_check_loop")
        self._deadlock_check_task = asyncio.create_task(self._deadlock_check_loop(), name="scheduler_deadlock_check")
        self._cleanup_task = asyncio.create_task(self._cleanup_loop(), name="scheduler_cleanup")

        # 注册到 event_manager
        try:
            from src.plugin_system.core.event_manager import event_manager

            event_manager.register_scheduler_callback(self._handle_event_trigger)
            logger.debug("调度器已注册到 event_manager")
        except ImportError:
            logger.warning("无法导入 event_manager，事件触发功能将不可用")

        logger.info("统一调度器已启动")

    async def stop(self) -> None:
        """停止调度器（优雅关闭）"""
        if not self._running:
            return

        logger.info("正在停止统一调度器...")
        self._stopping = True
        self._running = False

        # 取消后台任务
        background_tasks = [
            self._check_loop_task,
            self._deadlock_check_task,
            self._cleanup_task,
        ]

        for task in background_tasks:
            if task and not task.done():
                task.cancel()

        # 等待后台任务完成
        await asyncio.gather(*[t for t in background_tasks if t], return_exceptions=True)

        # 取消注册 event_manager
        try:
            from src.plugin_system.core.event_manager import event_manager

            event_manager.unregister_scheduler_callback()
            logger.debug("调度器已从 event_manager 注销")
        except ImportError:
            pass

        # 取消所有正在执行的任务
        await self._cancel_all_running_tasks()

        # 显示最终统计
        stats = self.get_statistics()
        logger.info(
            f"调度器最终统计: 总任务={stats['total_tasks']}, "
            f"执行次数={stats['total_executions']}, "
            f"失败={stats['total_failures']}"
        )

        # 清理资源
        self._tasks.clear()
        self._tasks_by_name.clear()
        self._event_subscriptions.clear()
        self._completed_tasks.clear()
        self._deadlock_detector.clear()

        logger.info("统一调度器已停止")

    async def _cancel_all_running_tasks(self) -> None:
        """取消所有正在运行的任务"""
        running_tasks = [
            task for task in self._tasks.values() if task.status == TaskStatus.RUNNING and task._asyncio_task
        ]

        if not running_tasks:
            return

        logger.info(f"正在取消 {len(running_tasks)} 个运行中的任务...")

        # 第一阶段：发送取消信号
        for task in running_tasks:
            if task._asyncio_task and not task._asyncio_task.done():
                task._asyncio_task.cancel()

        # 第二阶段：等待取消完成（带超时）
        cancel_tasks = [
            task._asyncio_task for task in running_tasks if task._asyncio_task and not task._asyncio_task.done()
        ]

        if cancel_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*cancel_tasks, return_exceptions=True), timeout=self.config.shutdown_timeout
                )
                logger.info("所有任务已成功取消")
            except asyncio.TimeoutError:
                logger.warning(f"部分任务取消超时（{self.config.shutdown_timeout}秒），强制停止")

    # ==================== 后台循环 ====================

    async def _check_loop(self) -> None:
        """主循环：定期检查和触发任务"""
        logger.debug("调度器主循环已启动")

        while self._running:
            try:
                await asyncio.sleep(self.config.check_interval)

                if not self._stopping:
                    # 使用 create_task 避免阻塞循环
                    asyncio.create_task(self._check_and_trigger_tasks(), name="check_trigger_tasks")

            except asyncio.CancelledError:
                logger.debug("调度器主循环被取消")
                break
            except Exception as e:
                logger.error(f"调度器主循环发生错误: {e}", exc_info=True)

    async def _deadlock_check_loop(self) -> None:
        """死锁检测循环"""
        logger.debug("死锁检测循环已启动")

        while self._running:
            try:
                await asyncio.sleep(self.config.deadlock_check_interval)

                if not self._stopping:
                    # 使用 create_task 避免阻塞循环，并限制错误传播
                    asyncio.create_task(self._safe_check_and_handle_deadlocks(), name="deadlock_check")

            except asyncio.CancelledError:
                logger.debug("死锁检测循环被取消")
                break
            except Exception as e:
                logger.error(f"死锁检测循环发生错误: {e}", exc_info=True)
                # 继续运行，不因单次错误停止

    async def _cleanup_loop(self) -> None:
        """清理循环：定期清理已完成的任务"""
        logger.debug("清理循环已启动")

        while self._running:
            try:
                await asyncio.sleep(self.config.cleanup_interval)

                if not self._stopping:
                    await self._cleanup_completed_tasks()

            except asyncio.CancelledError:
                logger.debug("清理循环被取消")
                break
            except Exception as e:
                logger.error(f"清理循环发生错误: {e}", exc_info=True)

    # ==================== 任务触发逻辑 ====================

    async def _check_and_trigger_tasks(self) -> None:
        """检查并触发到期任务（完全无锁设计）"""
        current_time = datetime.now()
        tasks_to_trigger: list[ScheduleTask] = []

        # 第一阶段：收集需要触发的任务
        for task in list(self._tasks.values()):
            if not task.can_trigger():
                continue

            try:
                should_trigger = await self._should_trigger_task(task, current_time)
                if should_trigger:
                    tasks_to_trigger.append(task)
            except Exception as e:
                logger.error(f"检查任务 {task.task_name} 触发条件时出错: {e}", exc_info=True)

        # 第二阶段：并发触发所有任务
        if tasks_to_trigger:
            await self._trigger_tasks_concurrently(tasks_to_trigger)

    async def _should_trigger_task(self, task: ScheduleTask, current_time: datetime) -> bool:
        """判断任务是否应该触发"""
        if task.trigger_type == TriggerType.TIME:
            return self._check_time_trigger(task, current_time)
        elif task.trigger_type == TriggerType.CUSTOM:
            return await self._check_custom_trigger(task)
        # EVENT 类型由 event_manager 触发
        return False

    def _check_time_trigger(self, task: ScheduleTask, current_time: datetime) -> bool:
        """检查时间触发条件"""
        config = task.trigger_config

        # 检查 trigger_at
        if "trigger_at" in config:
            trigger_time = config["trigger_at"]
            if isinstance(trigger_time, str):
                trigger_time = datetime.fromisoformat(trigger_time)

            if task.is_recurring and "interval_seconds" in config:
                # 循环任务：检查是否达到间隔
                if task.last_triggered_at is None:
                    return current_time >= trigger_time
                else:
                    elapsed = (current_time - task.last_triggered_at).total_seconds()
                    return elapsed >= config["interval_seconds"]
            else:
                # 一次性任务：检查是否到达触发时间
                return current_time >= trigger_time

        # 检查 delay_seconds
        elif "delay_seconds" in config:
            if task.last_triggered_at is None:
                # 首次触发：从创建时间算起
                elapsed = (current_time - task.created_at).total_seconds()
                return elapsed >= config["delay_seconds"]
            else:
                # 后续触发：从上次触发时间算起
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

    async def _trigger_tasks_concurrently(self, tasks: list[ScheduleTask]) -> None:
        """并发触发多个任务"""
        logger.debug(f"并发触发 {len(tasks)} 个任务")

        # 为每个任务创建独立的执行 Task
        execution_tasks = []
        for task in tasks:
            exec_task = asyncio.create_task(self._execute_task(task), name=f"exec_{task.task_name}")
            task._asyncio_task = exec_task
            execution_tasks.append(exec_task)

        # 等待所有任务完成（不阻塞主循环）
        # 使用 return_exceptions=True 确保单个任务失败不影响其他任务
        await asyncio.gather(*execution_tasks, return_exceptions=True)

    async def _execute_task(self, task: ScheduleTask) -> None:
        """执行单个任务（完全隔离）"""
        execution = task.start_execution()
        self._deadlock_detector.register_task(task.schedule_id, task.task_name)

        try:
            # 使用信号量控制并发
            async with self._acquire_semaphore():
                # 应用超时保护
                timeout = task.timeout or self.config.task_default_timeout

                try:
                    await asyncio.wait_for(self._run_callback(task), timeout=timeout)

                    # 执行成功
                    task.finish_execution(success=True)
                    self._total_executions += 1
                    logger.debug(f"任务 {task.task_name} 执行成功 (第{task.trigger_count}次)")

                except asyncio.TimeoutError:
                    # 任务超时
                    logger.warning(f"任务 {task.task_name} 执行超时 ({timeout}秒)")
                    task.status = TaskStatus.TIMEOUT
                    task.finish_execution(success=False, error=TimeoutError(f"Task timeout after {timeout}s"))
                    self._total_timeouts += 1
                    self._deadlock_detector.record_timeout(task.schedule_id)

                except asyncio.CancelledError:
                    # 任务被取消
                    logger.debug(f"任务 {task.task_name} 被取消")
                    if task.current_execution:
                        task.current_execution.cancel()
                    task.status = TaskStatus.CANCELLED
                    raise  # 重新抛出，让上层处理

                except Exception as e:
                    # 任务执行失败
                    logger.error(f"任务 {task.task_name} 执行失败: {e}", exc_info=True)
                    task.finish_execution(success=False, error=e)
                    self._total_failures += 1

                    # 检查是否需要重试
                    if self.config.enable_retry and task.retry_count < task.max_retries:
                        task.retry_count += 1
                        logger.info(
                            f"任务 {task.task_name} 将在 {self.config.retry_delay}秒后重试 "
                            f"({task.retry_count}/{task.max_retries})"
                        )
                        await asyncio.sleep(self.config.retry_delay)
                        task.status = TaskStatus.PENDING  # 重置为待触发状态

        finally:
            # 清理
            self._deadlock_detector.unregister_task(task.schedule_id)
            task._asyncio_task = None

            # 如果是一次性任务且成功完成，移动到已完成列表
            if not task.is_recurring and task.status == TaskStatus.COMPLETED:
                await self._move_to_completed(task)

    async def _run_callback(self, task: ScheduleTask) -> Any:
        """运行任务回调函数"""
        try:
            if asyncio.iscoroutinefunction(task.callback):
                result = await task.callback(*task.callback_args, **task.callback_kwargs)
            else:
                # 同步函数在线程池中运行，避免阻塞事件循环
                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(
                    None, lambda: task.callback(*task.callback_args, **task.callback_kwargs)
                )
            return result
        except Exception as e:
            logger.error(f"执行任务 {task.task_name} 的回调函数时出错: {e}", exc_info=True)
            raise

    def _acquire_semaphore(self):
        """获取信号量（如果启用）"""
        if self._task_semaphore:
            return self._task_semaphore
        else:
            # 返回一个空的上下文管理器
            from contextlib import nullcontext

            return nullcontext()

    async def _move_to_completed(self, task: ScheduleTask) -> None:
        """将任务移动到已完成列表"""
        if task.schedule_id in self._tasks:
            self._tasks.pop(task.schedule_id)
            self._tasks_by_name.pop(task.task_name, None)

            # 清理事件订阅
            if task.trigger_type == TriggerType.EVENT:
                event_name = task.trigger_config.get("event_name")
                if event_name and event_name in self._event_subscriptions:
                    self._event_subscriptions[event_name].discard(task.schedule_id)
                    if not self._event_subscriptions[event_name]:
                        del self._event_subscriptions[event_name]

            # 添加到已完成列表
            self._completed_tasks.append(task)
            if len(self._completed_tasks) > self.config.keep_completed_tasks:
                self._completed_tasks.pop(0)

            logger.debug(f"一次性任务 {task.task_name} 已完成并移除")

    # ==================== 事件触发处理 ====================

    async def _handle_event_trigger(self, event_name: str | EventType, event_params: dict[str, Any]) -> None:
        """处理来自 event_manager 的事件通知（无锁设计）"""
        task_ids = self._event_subscriptions.get(event_name, set())
        if not task_ids:
            return

        # 收集需要触发的任务
        tasks_to_trigger = []
        for task_id in list(task_ids):  # 使用 list() 避免迭代时修改
            task = self._tasks.get(task_id)
            if task and task.can_trigger():
                tasks_to_trigger.append(task)

        if not tasks_to_trigger:
            return

        logger.debug(f"事件 '{event_name}' 触发 {len(tasks_to_trigger)} 个任务")

        # 并发执行所有事件任务
        execution_tasks = []
        for task in tasks_to_trigger:
            # 将事件参数注入到回调
            exec_task = asyncio.create_task(
                self._execute_event_task(task, event_params), name=f"event_exec_{task.task_name}"
            )
            task._asyncio_task = exec_task
            execution_tasks.append(exec_task)

        # 等待所有任务完成
        await asyncio.gather(*execution_tasks, return_exceptions=True)

    async def _execute_event_task(self, task: ScheduleTask, event_params: dict[str, Any]) -> None:
        """执行事件触发的任务"""
        execution = task.start_execution()
        self._deadlock_detector.register_task(task.schedule_id, task.task_name)

        try:
            async with self._acquire_semaphore():
                timeout = task.timeout or self.config.task_default_timeout

                try:
                    # 合并事件参数和任务参数
                    merged_kwargs = {**task.callback_kwargs, **event_params}

                    if asyncio.iscoroutinefunction(task.callback):
                        await asyncio.wait_for(task.callback(*task.callback_args, **merged_kwargs), timeout=timeout)
                    else:
                        loop = asyncio.get_running_loop()
                        await asyncio.wait_for(
                            loop.run_in_executor(None, lambda: task.callback(*task.callback_args, **merged_kwargs)),
                            timeout=timeout,
                        )

                    task.finish_execution(success=True)
                    self._total_executions += 1
                    logger.debug(f"事件任务 {task.task_name} 执行成功")

                except asyncio.TimeoutError:
                    logger.warning(f"事件任务 {task.task_name} 执行超时")
                    task.status = TaskStatus.TIMEOUT
                    task.finish_execution(success=False, error=TimeoutError())
                    self._total_timeouts += 1
                    self._deadlock_detector.record_timeout(task.schedule_id)

                except asyncio.CancelledError:
                    logger.debug(f"事件任务 {task.task_name} 被取消")
                    if task.current_execution:
                        task.current_execution.cancel()
                    task.status = TaskStatus.CANCELLED
                    raise

                except Exception as e:
                    logger.error(f"事件任务 {task.task_name} 执行失败: {e}", exc_info=True)
                    task.finish_execution(success=False, error=e)
                    self._total_failures += 1

        finally:
            self._deadlock_detector.unregister_task(task.schedule_id)
            task._asyncio_task = None

            if not task.is_recurring and task.status == TaskStatus.COMPLETED:
                await self._move_to_completed(task)

    # ==================== 死锁检测和处理 ====================

    async def _safe_check_and_handle_deadlocks(self) -> None:
        """安全地检查并处理死锁任务（带错误隔离）"""
        try:
            await self._check_and_handle_deadlocks()
        except RecursionError:
            logger.error("死锁检测发生递归错误，跳过本轮检测")
        except Exception as e:
            logger.error(f"死锁检测处理失败: {e}", exc_info=True)

    async def _check_and_handle_deadlocks(self) -> None:
        """检查并处理死锁任务"""
        deadlocked = self._deadlock_detector.check_deadlocks()

        if not deadlocked:
            return

        logger.warning(f"检测到 {len(deadlocked)} 个可能的死锁任务")

        for task_id, runtime, task_name in deadlocked:
            task = self._tasks.get(task_id)
            if not task:
                self._deadlock_detector.unregister_task(task_id)
                continue

            health = self._deadlock_detector.get_health_score(task_id)
            logger.warning(f"任务 {task_name} 疑似死锁: 运行时间={runtime:.1f}秒, 健康度={health:.2f}")

            # 尝试取消任务（每个取消操作独立处理错误）
            try:
                await self._cancel_task(task, reason="deadlock detected")
            except Exception as e:
                logger.error(f"取消任务 {task_name} 时出错: {e}", exc_info=True)
                # 强制清理
                task._asyncio_task = None
                task.status = TaskStatus.CANCELLED
                self._deadlock_detector.unregister_task(task_id)

    async def _cancel_task(self, task: ScheduleTask, reason: str = "manual") -> bool:
        """取消正在运行的任务（多级超时机制）"""
        if not task._asyncio_task or task._asyncio_task.done():
            return True

        logger.info(f"取消任务 {task.task_name} (原因: {reason})")

        # 第一阶段：发送取消信号
        task._asyncio_task.cancel()

        # 第二阶段：渐进式等待（使用 asyncio.wait 避免递归）
        timeouts = [1.0, 3.0, 5.0, 10.0]
        for i, timeout in enumerate(timeouts):
            try:
                # 使用 asyncio.wait 代替 wait_for，避免重新抛出异常
                done, pending = await asyncio.wait({task._asyncio_task}, timeout=timeout)

                if done:
                    # 任务已完成（可能是正常完成或被取消）
                    logger.debug(f"任务 {task.task_name} 在阶段 {i + 1} 成功停止")
                    return True

                # 超时：继续下一阶段或放弃
                if i < len(timeouts) - 1:
                    logger.warning(f"任务 {task.task_name} 取消阶段 {i + 1} 超时，继续等待...")
                    continue
                else:
                    logger.error(f"任务 {task.task_name} 取消失败，强制清理")
                    break

            except Exception as e:
                logger.error(f"取消任务 {task.task_name} 时发生异常: {e}", exc_info=True)
                return False

        # 第三阶段：强制清理
        task._asyncio_task = None
        task.status = TaskStatus.CANCELLED
        self._deadlock_detector.unregister_task(task.schedule_id)
        return False

    # ==================== 资源清理 ====================

    async def _cleanup_completed_tasks(self) -> None:
        """清理已完成的任务"""
        # 清理已完成的一次性任务
        completed_tasks = [
            task for task in self._tasks.values() if not task.is_recurring and task.status == TaskStatus.COMPLETED
        ]

        for task in completed_tasks:
            await self._move_to_completed(task)

        if completed_tasks:
            logger.debug(f"清理了 {len(completed_tasks)} 个已完成的任务")

        # 清理已完成的 asyncio Task
        for task in list(self._tasks.values()):
            if task._asyncio_task and task._asyncio_task.done():
                task._asyncio_task = None

    # ==================== 任务管理 API ====================

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
        timeout: float | None = None,
        max_retries: int = 0,
    ) -> str:
        """创建调度任务

        Args:
            callback: 回调函数（必须是异步函数）
            trigger_type: 触发类型
            trigger_config: 触发配置
            is_recurring: 是否循环任务
            task_name: 任务名称（建议提供，用于查找和管理）
            callback_args: 回调函数位置参数
            callback_kwargs: 回调函数关键字参数
            force_overwrite: 如果同名任务已存在，是否强制覆盖
            timeout: 任务超时时间（秒），None表示使用默认值
            max_retries: 最大重试次数

        Returns:
            str: 创建的 schedule_id

        Raises:
            ValueError: 如果同名任务已存在且未启用强制覆盖
            RuntimeError: 如果调度器未运行
        """
        if not self._running:
            raise RuntimeError("调度器未运行，请先调用 start()")

        # 生成任务ID和名称
        schedule_id = str(uuid.uuid4())
        if task_name is None:
            task_name = f"Task-{schedule_id[:8]}"

        # 检查同名任务
        if task_name in self._tasks_by_name:
            existing_id = self._tasks_by_name[task_name]
            existing_task = self._tasks.get(existing_id)

            if existing_task and existing_task.is_active():
                if force_overwrite:
                    logger.info(f"检测到同名活跃任务 '{task_name}'，启用强制覆盖，移除现有任务")
                    await self.remove_schedule(existing_id)
                else:
                    raise ValueError(
                        f"任务名称 '{task_name}' 已存在活跃任务 (ID: {existing_id[:8]}...)。"
                        f"如需覆盖，请设置 force_overwrite=True"
                    )

        # 创建任务
        task = ScheduleTask(
            schedule_id=schedule_id,
            task_name=task_name,
            callback=callback,
            trigger_type=trigger_type,
            trigger_config=trigger_config,
            is_recurring=is_recurring,
            callback_args=callback_args or (),
            callback_kwargs=callback_kwargs or {},
            timeout=timeout,
            max_retries=max_retries,
        )

        # 保存弱引用到调度器（避免循环引用）
        task._weak_scheduler = weakref.ref(self)

        # 注册任务
        self._tasks[schedule_id] = task
        self._tasks_by_name[task_name] = schedule_id

        # 如果是事件触发，注册事件订阅
        if trigger_type == TriggerType.EVENT:
            event_name = trigger_config.get("event_name")
            if not event_name:
                raise ValueError("事件触发类型必须提供 event_name")
            self._event_subscriptions[event_name].add(schedule_id)
            logger.debug(f"任务 {task_name} 订阅事件: {event_name}")

        logger.debug(f"创建调度任务: {task_name} (ID: {schedule_id[:8]}...)")
        return schedule_id

    async def remove_schedule(self, schedule_id: str) -> bool:
        """移除调度任务

        如果任务正在执行，会安全地取消执行中的任务

        Args:
            schedule_id: 任务ID

        Returns:
            bool: 是否成功移除
        """
        task = self._tasks.get(schedule_id)
        if not task:
            logger.warning(f"尝试移除不存在的任务: {schedule_id[:8]}...")
            return False

        # 如果任务正在运行，先取消
        if task.status == TaskStatus.RUNNING:
            await self._cancel_task(task, reason="removed")

        # 从字典中移除
        self._tasks.pop(schedule_id, None)
        self._tasks_by_name.pop(task.task_name, None)

        # 清理事件订阅
        if task.trigger_type == TriggerType.EVENT:
            event_name = task.trigger_config.get("event_name")
            if event_name and event_name in self._event_subscriptions:
                self._event_subscriptions[event_name].discard(schedule_id)
                if not self._event_subscriptions[event_name]:
                    del self._event_subscriptions[event_name]
                    logger.debug(f"事件 '{event_name}' 已无订阅任务")

        logger.debug(f"移除调度任务: {task.task_name}")
        return True

    async def remove_schedule_by_name(self, task_name: str) -> bool:
        """根据任务名称移除调度任务

        Args:
            task_name: 任务名称

        Returns:
            bool: 是否成功移除
        """
        schedule_id = self._tasks_by_name.get(task_name)
        if schedule_id:
            return await self.remove_schedule(schedule_id)
        logger.warning(f"未找到名为 '{task_name}' 的任务")
        return False

    async def find_schedule_by_name(self, task_name: str) -> str | None:
        """根据任务名称查找 schedule_id

        Args:
            task_name: 任务名称

        Returns:
            str | None: 找到的 schedule_id，如果不存在则返回 None
        """
        return self._tasks_by_name.get(task_name)

    async def trigger_schedule(self, schedule_id: str) -> bool:
        """强制触发指定任务（立即执行）

        Args:
            schedule_id: 任务ID

        Returns:
            bool: 是否成功触发
        """
        task = self._tasks.get(schedule_id)
        if not task:
            logger.warning(f"尝试触发不存在的任务: {schedule_id[:8]}...")
            return False

        if not task.can_trigger():
            logger.warning(f"任务 {task.task_name} 当前状态 {task.status.value} 无法触发")
            return False

        logger.info(f"强制触发任务: {task.task_name}")

        # 创建执行任务
        exec_task = asyncio.create_task(self._execute_task(task), name=f"manual_trigger_{task.task_name}")
        task._asyncio_task = exec_task

        # 等待完成
        try:
            await exec_task
            return task.status == TaskStatus.COMPLETED
        except Exception as e:
            logger.error(f"强制触发任务 {task.task_name} 失败: {e}", exc_info=True)
            return False

    async def pause_schedule(self, schedule_id: str) -> bool:
        """暂停任务（不删除，但不会被触发）

        Args:
            schedule_id: 任务ID

        Returns:
            bool: 是否成功暂停
        """
        task = self._tasks.get(schedule_id)
        if not task:
            logger.warning(f"尝试暂停不存在的任务: {schedule_id[:8]}...")
            return False

        if task.status == TaskStatus.RUNNING:
            logger.warning(f"任务 {task.task_name} 正在运行，无法暂停")
            return False

        task.status = TaskStatus.PAUSED
        logger.debug(f"暂停任务: {task.task_name}")
        return True

    async def resume_schedule(self, schedule_id: str) -> bool:
        """恢复暂停的任务

        Args:
            schedule_id: 任务ID

        Returns:
            bool: 是否成功恢复
        """
        task = self._tasks.get(schedule_id)
        if not task:
            logger.warning(f"尝试恢复不存在的任务: {schedule_id[:8]}...")
            return False

        if task.status != TaskStatus.PAUSED:
            logger.warning(f"任务 {task.task_name} 状态为 {task.status.value}，无需恢复")
            return False

        task.status = TaskStatus.PENDING
        logger.debug(f"恢复任务: {task.task_name}")
        return True

    async def get_task_info(self, schedule_id: str) -> dict[str, Any] | None:
        """获取任务详细信息

        Args:
            schedule_id: 任务ID

        Returns:
            dict | None: 任务信息字典，如果不存在返回 None
        """
        task = self._tasks.get(schedule_id)
        if not task:
            return None

        # 计算平均执行时间
        avg_execution_time = 0.0
        if task.success_count > 0:
            avg_execution_time = task.total_execution_time / task.success_count

        # 获取健康度
        health = self._deadlock_detector.get_health_score(schedule_id)

        return {
            "schedule_id": task.schedule_id,
            "task_name": task.task_name,
            "trigger_type": task.trigger_type.value,
            "is_recurring": task.is_recurring,
            "status": task.status.value,
            "created_at": task.created_at.isoformat(),
            "last_triggered_at": task.last_triggered_at.isoformat() if task.last_triggered_at else None,
            "next_trigger_at": task.next_trigger_at.isoformat() if task.next_trigger_at else None,
            "trigger_count": task.trigger_count,
            "success_count": task.success_count,
            "failure_count": task.failure_count,
            "retry_count": task.retry_count,
            "max_retries": task.max_retries,
            "avg_execution_time": avg_execution_time,
            "total_execution_time": task.total_execution_time,
            "health_score": health,
            "is_running": task.status == TaskStatus.RUNNING,
            "trigger_config": task.trigger_config.copy(),
            "timeout": task.timeout,
            "last_error": str(task.last_error) if task.last_error else None,
        }

    async def list_tasks(
        self,
        trigger_type: TriggerType | None = None,
        status: TaskStatus | None = None,
    ) -> list[dict[str, Any]]:
        """列出所有任务或指定类型/状态的任务

        Args:
            trigger_type: 触发类型过滤
            status: 状态过滤

        Returns:
            list: 任务信息列表
        """
        tasks = []
        for task in self._tasks.values():
            # 应用过滤器
            if trigger_type is not None and task.trigger_type != trigger_type:
                continue
            if status is not None and task.status != status:
                continue

            task_info = await self.get_task_info(task.schedule_id)
            if task_info:
                tasks.append(task_info)

        return tasks

    def get_statistics(self) -> dict[str, Any]:
        """获取调度器统计信息

        Returns:
            dict: 统计信息字典
        """
        # 统计各状态的任务数
        status_counts = defaultdict(int)
        for task in self._tasks.values():
            status_counts[task.status.value] += 1

        # 统计各类型的任务数
        type_counts = defaultdict(int)
        for task in self._tasks.values():
            type_counts[task.trigger_type.value] += 1

        # 计算运行时长
        uptime = 0.0
        if self._start_time:
            uptime = (datetime.now() - self._start_time).total_seconds()

        # 获取正在运行的任务
        running_tasks_info = []
        for task in self._tasks.values():
            if task.status == TaskStatus.RUNNING:
                runtime = 0.0
                if task.current_execution:
                    runtime = (datetime.now() - task.current_execution.started_at).total_seconds()
                running_tasks_info.append(
                    {
                        "schedule_id": task.schedule_id[:8] + "...",
                        "task_name": task.task_name,
                        "runtime": runtime,
                    }
                )

        return {
            "is_running": self._running,
            "uptime_seconds": uptime,
            "total_tasks": len(self._tasks),
            "active_tasks": status_counts[TaskStatus.PENDING.value],
            "running_tasks": status_counts[TaskStatus.RUNNING.value],
            "paused_tasks": status_counts[TaskStatus.PAUSED.value],
            "completed_tasks_archived": len(self._completed_tasks),
            "status_breakdown": dict(status_counts),
            "type_breakdown": dict(type_counts),
            "recurring_tasks": sum(1 for t in self._tasks.values() if t.is_recurring),
            "one_time_tasks": sum(1 for t in self._tasks.values() if not t.is_recurring),
            "registered_events": list(self._event_subscriptions.keys()),
            "total_executions": self._total_executions,
            "total_failures": self._total_failures,
            "total_timeouts": self._total_timeouts,
            "success_rate": (
                self._total_executions / (self._total_executions + self._total_failures)
                if self._total_executions + self._total_failures > 0
                else 0.0
            ),
            "running_tasks_info": running_tasks_info,
            "config": {
                "max_concurrent_tasks": self.config.max_concurrent_tasks,
                "task_default_timeout": self.config.task_default_timeout,
                "enable_retry": self.config.enable_retry,
                "max_retries": self.config.max_retries,
            },
        }


# ==================== 全局单例和辅助函数 ====================

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
