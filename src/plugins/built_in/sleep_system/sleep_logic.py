import logging
import random
import time
from datetime import datetime, time as dt_time, timedelta
from .config import SleepSystemConfig
from .state_manager import StateManager, SleepState

logger = logging.getLogger(__name__)

class SleepLogic:
    """
    实现睡眠系统的核心状态机逻辑。
    """
    def __init__(self, config: SleepSystemConfig, state_manager: StateManager):
        self.config = config
        self.state_manager = state_manager

    async def update_state(self) -> None:
        """
        核心更新函数，由定时任务调用。
        根据当前时间和状态，决定是否进行状态转换。
        """
        current_state = await self.state_manager.get_state()
        now = datetime.now()
        
        handler = getattr(self, f"_handle_{current_state.current_state.lower()}", self._handle_unknown)
        await handler(current_state, now)

    def _is_in_sleep_time_range(self, now: datetime) -> bool:
        """检查当前时间是否在理论睡眠时间范围内"""
        wake_up_time = dt_time(self.config.wake_up_time[0], self.config.wake_up_time[1])
        sleep_time = dt_time(self.config.sleep_time[0], self.config.sleep_time[1])
        now_time = now.time()

        if sleep_time > wake_up_time:  # 跨天睡眠
            return now_time >= sleep_time or now_time < wake_up_time
        else:  # 当天睡眠
            return sleep_time <= now_time < wake_up_time

    async def _handle_awake(self, state: SleepState, now: datetime):
        """处理 AWAKE 状态的逻辑"""
        # 检查是否到了准备睡觉的时间
        sleep_datetime = datetime.combine(now.date(), dt_time(self.config.sleep_time[0], self.config.sleep_time[1]))
        prepare_start_time = sleep_datetime - timedelta(minutes=self.config.prepare_sleep_duration)

        if prepare_start_time <= now < sleep_datetime:
            await self._transition_to(state, "PREPARING_SLEEP", duration_minutes=self.config.prepare_sleep_duration)
            logger.info("时间已到，进入睡前准备状态。")
            # 在这里可以触发“准备睡觉”的情绪或回复

    async def _handle_preparing_sleep(self, state: SleepState, now: datetime):
        """处理 PREPARING_SLEEP 状态的逻辑"""
        if state.state_end_time and now.timestamp() >= state.state_end_time:
            # 准备时间结束，进入睡眠
            if self._is_in_sleep_time_range(now):
                await self._transition_to(state, "SLEEPING")
                logger.info("准备时间结束，已进入睡眠状态。")
            else:
                await self._transition_to(state, "AWAKE")
                logger.info("准备期间离开了理论睡眠时间，返回 AWAKE 状态。")

    async def _handle_sleeping(self, state: SleepState, now: datetime):
        """处理 SLEEPING 状态的逻辑"""
        # 检查是否到了起床时间
        if not self._is_in_sleep_time_range(now):
            await self._transition_to(state, "AWAKE")
            logger.info("理论睡眠时间结束，已切换到 AWAKE 状态。")
            # 在这里可以触发“睡醒”的情绪
            return

        # 根据概率随机触发失眠
        if random.random() < self.config.insomnia_probability:
            duration = random.randint(self.config.insomnia_duration_minutes[0], self.config.insomnia_duration_minutes[1])
            await self._transition_to(state, "INSOMNIA", duration_minutes=duration)
            logger.info(f"随机触发失眠，持续 {duration} 分钟。")
            # 在这里可以触发“烦躁”的情绪

    async def _handle_insomnia(self, state: SleepState, now: datetime):
        """处理 INSOMNIA 状态的逻辑"""
        # 检查失眠时间是否结束
        if state.state_end_time and now.timestamp() >= state.state_end_time:
            await self._transition_to(state, "SLEEPING")
            logger.info("失眠时间结束，返回睡眠状态。")
        # 如果在失眠期间就到了起床时间，直接唤醒
        elif not self._is_in_sleep_time_range(now):
            await self._transition_to(state, "AWAKE")
            logger.info("在失眠期间到达起床时间，已唤醒。")

    async def _handle_woken_up(self, state: SleepState, now: datetime):
        """处理 WOKEN_UP 状态的逻辑"""
        # 检查冷却时间是否结束
        if state.state_end_time and now.timestamp() >= state.state_end_time:
            if self._is_in_sleep_time_range(now):
                await self._transition_to(state, "PREPARING_SLEEP", duration_minutes=self.config.prepare_sleep_duration)
                logger.info("被吵醒冷却时间结束，尝试重新入睡。")
            else:
                await self._transition_to(state, "AWAKE")
                logger.info("被吵醒后到达起床时间，已唤醒。")

    async def _handle_unknown(self, state: SleepState, now: datetime):
        """处理未知状态"""
        logger.warning(f"检测到未知的睡眠状态: {state.current_state}。将重置为 AWAKE。")
        await self._transition_to(state, "AWAKE")

    async def handle_external_event(self):
        """处理外部事件，例如收到用户消息"""
        current_state = await self.state_manager.get_state()
        if current_state.current_state in ["SLEEPING", "INSOMNIA"]:
            await self._transition_to(current_state, "WOKEN_UP", duration_minutes=self.config.woken_up_cooldown_minutes)
            logger.info("在睡眠中被外部事件打断，进入 WOKEN_UP 状态。")
            # 在这里可以触发“起床气”情绪

    async def _transition_to(self, old_state: SleepState, new_state_name: str, duration_minutes: int = 0):
        """
        状态转换的统一处理函数。
        
        Args:
            old_state: 转换前的状态对象。
            new_state_name: 新状态的名称。
            duration_minutes: 新状态的持续时间（分钟），如果为0则不设结束时间。
        """
        current_timestamp = time.time()
        new_end_time = None
        if duration_minutes > 0:
            new_end_time = current_timestamp + duration_minutes * 60

        new_state = SleepState(
            current_state=new_state_name,
            state_end_time=new_end_time,
            last_updated=current_timestamp,
            metadata=old_state.metadata  # 继承 metadata
        )
        await self.state_manager.save_state(new_state)
        logger.info(f"睡眠状态已从 {old_state.current_state} 转换为 {new_state_name}。")
