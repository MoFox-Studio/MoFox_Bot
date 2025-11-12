"""
主动思考调度器
基于统一调度器(unified_scheduler)，为每个聊天流管理主动思考任务
根据聊天流的兴趣分数(stream_interest_score)动态计算触发间隔
"""

import asyncio
from datetime import datetime, timedelta
from typing import Any

from src.common.logger import get_logger
from src.plugin_system.apis.unified_scheduler import TriggerType, unified_scheduler

logger = get_logger("proactive_thinking_scheduler")


class ProactiveThinkingScheduler:
    """主动思考调度器

    负责为每个聊天流创建和管理主动思考任务。
    特点：
    1. 根据聊天流的兴趣分数动态计算触发间隔
    2. 在bot回复后自动重置定时任务
    3. 抛出话题后暂停，等待下次reply后才恢复
    """

    def __init__(self):
        """初始化调度器"""
        self._stream_schedules: dict[str, str] = {}  # stream_id -> schedule_id
        self._paused_streams: set[str] = set()  # 因抛出话题而暂停的聊天流
        self._lock = asyncio.Lock()

        # 统计数据
        self._statistics: dict[str, dict[str, Any]] = {}  # stream_id -> 统计信息
        self._daily_counts: dict[str, dict[str, int]] = {}  # stream_id -> {date: count}

        # 历史决策记录：stream_id -> 上次决策信息
        self._last_decisions: dict[str, dict[str, Any]] = {}

        # 从全局配置加载（延迟导入避免循环依赖）
        from src.config.config import global_config

        self.config = global_config.proactive_thinking

    def _calculate_interval(self, focus_energy: float) -> int:
        """根据 focus_energy 计算触发间隔

        Args:
            focus_energy: 聊天流的 focus_energy 值 (0.0-1.0)

        Returns:
            int: 触发间隔（秒）

        公式：
        - focus_energy 越高，间隔越短（更频繁思考）
        - interval = base_interval * (factor - focus_energy)
        - 例如：focus_energy=0.5 -> interval=1800*1.5=2700秒(45分钟)
        -       focus_energy=0.8 -> interval=1800*1.2=2160秒(36分钟)
        -       focus_energy=0.2 -> interval=1800*1.8=3240秒(54分钟)
        """
        # 如果不使用 focus_energy，直接返回基础间隔
        if not self.config.use_interest_score:
            return self.config.base_interval

        # 确保值在有效范围内
        focus_energy = max(0.0, min(1.0, focus_energy))

        # 计算间隔：focus_energy 越高，系数越小，间隔越短
        factor = self.config.interest_score_factor - focus_energy
        interval = int(self.config.base_interval * factor)

        # 限制在最小和最大间隔之间
        interval = max(self.config.min_interval, min(self.config.max_interval, interval))

        logger.debug(f"Focus Energy {focus_energy:.3f} -> 触发间隔 {interval}秒 ({interval / 60:.1f}分钟)")
        return interval

    def _check_whitelist_blacklist(self, stream_config: str) -> bool:
        """检查聊天流是否通过黑白名单验证

        Args:
            stream_config: 聊天流配置字符串，格式: "platform:id:type"

        Returns:
            bool: True表示允许主动思考，False表示拒绝
        """
        # 解析类型
        parts = stream_config.split(":")
        if len(parts) != 3:
            logger.warning(f"无效的stream_config格式: {stream_config}")
            return False

        is_private = parts[2] == "private"

        # 黑名单检查（优先级高）
        if self.config.blacklist_mode:
            blacklist = self.config.blacklist_private if is_private else self.config.blacklist_group
            if stream_config in blacklist:
                logger.debug(f"聊天流 {stream_config} 在黑名单中，拒绝主动思考")
                return False

        # 白名单检查
        if self.config.whitelist_mode:
            whitelist = self.config.whitelist_private if is_private else self.config.whitelist_group
            if stream_config not in whitelist:
                logger.debug(f"聊天流 {stream_config} 不在白名单中，拒绝主动思考")
                return False

        return True

    def _check_interest_score_threshold(self, interest_score: float) -> bool:
        """检查兴趣分数是否在阈值范围内

        Args:
            interest_score: 兴趣分数

        Returns:
            bool: True表示在范围内
        """
        if interest_score < self.config.min_interest_score:
            logger.debug(f"兴趣分数 {interest_score:.2f} 低于最低阈值 {self.config.min_interest_score}")
            return False

        if interest_score > self.config.max_interest_score:
            logger.debug(f"兴趣分数 {interest_score:.2f} 高于最高阈值 {self.config.max_interest_score}")
            return False

        return True

    def _check_daily_limit(self, stream_id: str) -> bool:
        """检查今日主动发言次数是否超限

        Args:
            stream_id: 聊天流ID

        Returns:
            bool: True表示未超限
        """
        if self.config.max_daily_proactive == 0:
            return True  # 不限制

        today = datetime.now().strftime("%Y-%m-%d")

        if stream_id not in self._daily_counts:
            self._daily_counts[stream_id] = {}

        # 清理过期日期的数据
        for date in list(self._daily_counts[stream_id].keys()):
            if date != today:
                del self._daily_counts[stream_id][date]

        count = self._daily_counts[stream_id].get(today, 0)

        if count >= self.config.max_daily_proactive:
            logger.debug(f"聊天流 {stream_id} 今日主动发言次数已达上限 ({count}/{self.config.max_daily_proactive})")
            return False

        return True

    def _increment_daily_count(self, stream_id: str):
        """增加今日主动发言计数"""
        today = datetime.now().strftime("%Y-%m-%d")

        if stream_id not in self._daily_counts:
            self._daily_counts[stream_id] = {}

        self._daily_counts[stream_id][today] = self._daily_counts[stream_id].get(today, 0) + 1

    def _is_in_quiet_hours(self) -> bool:
        """检查当前是否在安静时段

        Returns:
            bool: True表示在安静时段
        """
        if not self.config.enable_time_strategy:
            return False

        now = datetime.now()
        current_time = now.strftime("%H:%M")

        start = self.config.quiet_hours_start
        end = self.config.quiet_hours_end

        # 处理跨日的情况（如23:00-07:00）
        if start <= end:
            return start <= current_time <= end
        else:
            return current_time >= start or current_time <= end

    async def _get_stream_focus_energy(self, stream_id: str) -> float:
        """获取聊天流的 focus_energy

        Args:
            stream_id: 聊天流ID

        Returns:
            float: focus_energy 值，默认0.5
        """
        try:
            # 从聊天管理器获取聊天流
            from src.chat.message_receive.chat_stream import get_chat_manager

            logger.debug("[调度器] 获取聊天管理器")
            chat_manager = get_chat_manager()
            logger.debug(f"[调度器] 从聊天管理器获取聊天流 {stream_id}")
            chat_stream = await chat_manager.get_stream(stream_id)

            if chat_stream:
                # 计算并获取最新的 focus_energy
                logger.debug("[调度器] 找到聊天流，开始计算 focus_energy")
                focus_energy = await chat_stream.calculate_focus_energy()
                logger.debug(f"[调度器] 聊天流 {stream_id} 的 focus_energy: {focus_energy:.3f}")
                return focus_energy
            else:
                logger.debug(f"[调度器] 未找到聊天流 {stream_id}，使用默认 focus_energy=0.5")
                return 0.5

        except Exception as e:
            logger.error(f"[调度器] ❌ 获取聊天流 {stream_id} 的 focus_energy 失败: {e}", exc_info=True)
            return 0.5

    async def schedule_proactive_thinking(self, stream_id: str) -> bool:
        """为聊天流创建或重置主动思考任务

        Args:
            stream_id: 聊天流ID

        Returns:
            bool: 是否成功创建/重置任务
        """
        logger.debug(f"[调度器] 开始为聊天流 {stream_id} 创建/重置主动思考任务")
        try:
            async with self._lock:
                # 如果该流因抛出话题而暂停，先清除暂停标记
                if stream_id in self._paused_streams:
                    logger.debug(f"[调度器] 清除聊天流 {stream_id} 的暂停标记")
                    self._paused_streams.discard(stream_id)

                # 如果已经有任务，先移除
                if stream_id in self._stream_schedules:
                    old_schedule_id = self._stream_schedules[stream_id]
                    logger.debug(f"[调度器] 移除聊天流 {stream_id} 的旧任务")
                    await unified_scheduler.remove_schedule(old_schedule_id)

                # 获取 focus_energy 并计算间隔
                focus_energy = await self._get_stream_focus_energy(stream_id)
                logger.debug(f"[调度器] focus_energy={focus_energy:.3f}")

                interval_seconds = self._calculate_interval(focus_energy)
                logger.debug(f"[调度器] 触发间隔={interval_seconds}秒 ({interval_seconds / 60:.1f}分钟)")

                # 导入回调函数（延迟导入避免循环依赖）
                from src.plugins.built_in.affinity_flow_chatter.proactive.proactive_thinking_executor import (
                    execute_proactive_thinking,
                )

                # 创建新任务
                schedule_id = await unified_scheduler.create_schedule(
                    callback=execute_proactive_thinking,
                    trigger_type=TriggerType.TIME,
                    trigger_config={
                        "delay_seconds": interval_seconds,
                    },
                    is_recurring=True,
                    task_name=f"ProactiveThinking-{stream_id}",
                    callback_args=(stream_id,),
                )

                self._stream_schedules[stream_id] = schedule_id

                # 计算下次触发时间
                next_run_time = datetime.now() + timedelta(seconds=interval_seconds)

                logger.debug(
                    f"主动思考任务已创建: {stream_id} | "
                    f"Focus: {focus_energy:.3f} | "
                    f"间隔: {interval_seconds / 60:.1f}分钟 | "
                    f"下次: {next_run_time.strftime('%H:%M:%S')}"
                )
                return True

        except Exception as e:
            logger.error(f"❌ 创建主动思考任务失败 {stream_id}: {e}", exc_info=True)
            return False

    async def pause_proactive_thinking(self, stream_id: str, reason: str = "抛出话题") -> bool:
        """暂停聊天流的主动思考任务

        当选择"抛出话题"后，应该暂停该聊天流的主动思考，
        直到bot至少执行过一次reply后才恢复。

        Args:
            stream_id: 聊天流ID
            reason: 暂停原因

        Returns:
            bool: 是否成功暂停
        """
        try:
            async with self._lock:
                if stream_id not in self._stream_schedules:
                    logger.warning(f"尝试暂停不存在的任务: {stream_id}")
                    return False

                schedule_id = self._stream_schedules[stream_id]
                success = await unified_scheduler.pause_schedule(schedule_id)

                if success:
                    self._paused_streams.add(stream_id)
                    logger.debug(f"暂停主动思考: {stream_id}，原因: {reason}")

                return success

        except Exception:
            # 错误日志已在上面记录
            return False

    async def resume_proactive_thinking(self, stream_id: str) -> bool:
        """恢复聊天流的主动思考任务

        Args:
            stream_id: 聊天流ID

        Returns:
            bool: 是否成功恢复
        """
        try:
            async with self._lock:
                if stream_id not in self._stream_schedules:
                    logger.warning(f"尝试恢复不存在的任务: {stream_id}")
                    return False

                schedule_id = self._stream_schedules[stream_id]
                success = await unified_scheduler.resume_schedule(schedule_id)

                if success:
                    self._paused_streams.discard(stream_id)
                    logger.debug(f"恢复主动思考: {stream_id}")

                return success

        except Exception as e:
            logger.error(f"❌ 恢复主动思考失败 {stream_id}: {e}", exc_info=True)
            return False

    async def cancel_proactive_thinking(self, stream_id: str) -> bool:
        """取消聊天流的主动思考任务

        Args:
            stream_id: 聊天流ID

        Returns:
            bool: 是否成功取消
        """
        try:
            async with self._lock:
                if stream_id not in self._stream_schedules:
                    return True  # 已经不存在，视为成功

                schedule_id = self._stream_schedules.pop(stream_id)
                self._paused_streams.discard(stream_id)

                success = await unified_scheduler.remove_schedule(schedule_id)
                logger.debug(f"⏹️ 取消主动思考 {stream_id}")

                return success

        except Exception as e:
            logger.error(f"❌ 取消主动思考失败 {stream_id}: {e}", exc_info=True)
            return False

    async def is_paused(self, stream_id: str) -> bool:
        """检查聊天流的主动思考是否被暂停

        Args:
            stream_id: 聊天流ID

        Returns:
            bool: 是否暂停中
        """
        async with self._lock:
            return stream_id in self._paused_streams

    async def get_task_info(self, stream_id: str) -> dict[str, Any] | None:
        """获取聊天流的主动思考任务信息

        Args:
            stream_id: 聊天流ID

        Returns:
            dict: 任务信息，如果不存在返回None
        """
        async with self._lock:
            if stream_id not in self._stream_schedules:
                return None

            schedule_id = self._stream_schedules[stream_id]
            task_info = await unified_scheduler.get_task_info(schedule_id)

            if task_info:
                task_info["is_paused_for_topic"] = stream_id in self._paused_streams

            return task_info

    async def list_all_tasks(self) -> list[dict[str, Any]]:
        """列出所有主动思考任务

        Returns:
            list: 任务信息列表
        """
        async with self._lock:
            tasks = []
            for stream_id, schedule_id in self._stream_schedules.items():
                task_info = await unified_scheduler.get_task_info(schedule_id)
                if task_info:
                    task_info["stream_id"] = stream_id
                    task_info["is_paused_for_topic"] = stream_id in self._paused_streams
                    tasks.append(task_info)
            return tasks

    def get_statistics(self) -> dict[str, Any]:
        """获取调度器统计信息

        Returns:
            dict: 统计信息
        """
        return {
            "total_scheduled_streams": len(self._stream_schedules),
            "paused_for_topic": len(self._paused_streams),
            "active_tasks": len(self._stream_schedules) - len(self._paused_streams),
        }

    async def log_next_trigger_times(self, max_streams: int = 10):
        """在日志中输出聊天流的下次触发时间

        Args:
            max_streams: 最多显示多少个聊天流，0表示全部
        """
        logger.info("=" * 60)
        logger.info("主动思考任务状态")
        logger.info("=" * 60)

        tasks = await self.list_all_tasks()

        if not tasks:
            logger.info("当前没有活跃的主动思考任务")
            logger.info("=" * 60)
            return

        # 按下次触发时间排序
        tasks_sorted = sorted(tasks, key=lambda x: x.get("next_run_time", datetime.max) or datetime.max)

        # 限制显示数量
        if max_streams > 0:
            tasks_sorted = tasks_sorted[:max_streams]

        logger.info(f"共有 {len(self._stream_schedules)} 个任务，显示前 {len(tasks_sorted)} 个")
        logger.info("")

        for i, task in enumerate(tasks_sorted, 1):
            stream_id = task.get("stream_id", "Unknown")
            next_run = task.get("next_run_time")
            is_paused = task.get("is_paused_for_topic", False)

            # 获取聊天流名称（如果可能）
            stream_name = stream_id[:16] + "..." if len(stream_id) > 16 else stream_id

            if next_run:
                # 计算剩余时间
                now = datetime.now()
                remaining = next_run - now
                remaining_seconds = int(remaining.total_seconds())

                if remaining_seconds < 0:
                    time_str = "已过期（待执行）"
                elif remaining_seconds < 60:
                    time_str = f"{remaining_seconds}秒后"
                elif remaining_seconds < 3600:
                    time_str = f"{remaining_seconds // 60}分钟后"
                else:
                    hours = remaining_seconds // 3600
                    minutes = (remaining_seconds % 3600) // 60
                    time_str = f"{hours}小时{minutes}分钟后"

                status = "⏸️ 暂停中" if is_paused else "✅ 活跃"

                logger.info(
                    f"[{i:2d}] {status} | {stream_name}\n"
                    f"     下次触发: {next_run.strftime('%Y-%m-%d %H:%M:%S')} ({time_str})"
                )
            else:
                logger.info(f"[{i:2d}] ⚠️  未知 | {stream_name}\n     下次触发: 未设置")

        logger.info("")
        logger.info("=" * 60)

    def get_last_decision(self, stream_id: str) -> dict[str, Any] | None:
        """获取聊天流的上次主动思考决策

        Args:
            stream_id: 聊天流ID

        Returns:
            dict: 上次决策信息，包含：
                - action: "do_nothing" | "simple_bubble" | "throw_topic"
                - reasoning: 决策理由
                - topic: (可选) 话题内容
                - timestamp: 决策时间戳
            None: 如果没有历史决策
        """
        return self._last_decisions.get(stream_id)

    def record_decision(self, stream_id: str, action: str, reasoning: str, topic: str | None = None) -> None:
        """记录聊天流的主动思考决策

        Args:
            stream_id: 聊天流ID
            action: 决策动作
            reasoning: 决策理由
            topic: (可选) 话题内容
        """
        self._last_decisions[stream_id] = {
            "action": action,
            "reasoning": reasoning,
            "topic": topic,
            "timestamp": datetime.now().isoformat(),
        }
        logger.debug(f"已记录聊天流 {stream_id} 的决策: {action}")


# 全局调度器实例
proactive_thinking_scheduler = ProactiveThinkingScheduler()
