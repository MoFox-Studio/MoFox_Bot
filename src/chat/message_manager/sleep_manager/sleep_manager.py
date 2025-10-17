import asyncio
import random
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Optional

from src.common.logger import get_logger
from src.config.config import global_config

from .notification_sender import NotificationSender
from .sleep_state import SleepContext, SleepState
from .time_checker import TimeChecker

if TYPE_CHECKING:
    from .wakeup_manager import WakeUpManager

logger = get_logger("sleep_manager")


class SleepManager:
    """
    睡眠管理器，核心组件之一，负责管理角色的睡眠周期和状态转换。
    它实现了一个状态机，根据预设的时间表、睡眠压力和随机因素，
    在不同的睡眠状态（如清醒、准备入睡、睡眠、失眠）之间进行切换。
    """

    def __init__(self):
        """
        初始化睡眠管理器。
        """
        self.context = SleepContext()  # 睡眠上下文，管理所有状态
        self.time_checker = TimeChecker()  # 时间检查器
        self.last_sleep_log_time = 0  # 上次记录睡眠日志的时间戳
        self.sleep_log_interval = 35  # 睡眠日志记录间隔（秒）
        self._last_fully_slept_log_time: float = 0  # 上次完全进入睡眠状态的时间戳

    def get_current_sleep_state(self) -> SleepState:
        """获取当前的睡眠状态。"""
        return self.context.current_state

    def is_sleeping(self) -> bool:
        """判断当前是否处于正在睡觉的状态。"""
        return self.context.current_state == SleepState.SLEEPING

    def is_woken_up(self) -> bool:
        """判断当前是否处于被吵醒的状态。"""
        return self.context.current_state == SleepState.WOKEN_UP

    async def update_sleep_state(self, wakeup_manager: Optional["WakeUpManager"] = None):
        """
        更新睡眠状态的核心方法，实现状态机的主要逻辑。
        该方法会被周期性调用，以检查并更新当前的睡眠状态。

        Args:
            wakeup_manager (Optional["WakeUpManager"]): 唤醒管理器，用于获取睡眠压力等上下文信息。
        """
        # 如果全局禁用了睡眠系统，则强制设置为清醒状态并返回
        if not global_config.sleep_system.enable:
            if self.context.current_state != SleepState.AWAKE:
                logger.debug("睡眠系统禁用，强制设为 AWAKE")
                self.context.current_state = SleepState.AWAKE
            return

        now = datetime.now()
        today = now.date()

        # 跨天处理：如果日期变化，重置每日相关的睡眠状态
        if self.context.last_sleep_check_date != today:
            logger.info(f"新的一天 ({today})，重置睡眠状态。")
            self.context.total_delayed_minutes_today = 0
            self.context.current_state = SleepState.AWAKE
            self.context.sleep_buffer_end_time = None
            self.context.last_sleep_check_date = today
            self.context.save()

        # 检查当前是否处于理论上的睡眠时间段
        is_in_theoretical_sleep, activity = self.time_checker.is_in_theoretical_sleep_time(now.time())

        # --- 状态机核心处理逻辑 ---
        current_state = self.context.current_state
        if current_state == SleepState.AWAKE:
            if is_in_theoretical_sleep:
                self._handle_awake_to_sleep(now, activity, wakeup_manager)

        elif current_state == SleepState.PREPARING_SLEEP:
            self._handle_preparing_sleep(now, is_in_theoretical_sleep, wakeup_manager)

        elif current_state == SleepState.SLEEPING:
            self._handle_sleeping(now, is_in_theoretical_sleep, activity, wakeup_manager)

        elif current_state == SleepState.INSOMNIA:
            self._handle_insomnia(now, is_in_theoretical_sleep)

        elif current_state == SleepState.WOKEN_UP:
            self._handle_woken_up(now, is_in_theoretical_sleep, wakeup_manager)

    def _handle_awake_to_sleep(self, now: datetime, activity: str | None, wakeup_manager: Optional["WakeUpManager"]):
        """处理从“清醒”到“准备入睡”的状态转换。"""
        if activity:
            logger.info(f"进入理论休眠时间 '{activity}'，开始进行睡眠决策...")
        else:
            logger.info("进入理论休眠时间，开始进行睡眠决策...")

        if global_config.sleep_system.enable_flexible_sleep:
            # --- 新的弹性睡眠逻辑 ---
            if wakeup_manager:
                sleep_pressure = wakeup_manager.context.sleep_pressure
                pressure_threshold = global_config.sleep_system.flexible_sleep_pressure_threshold
                max_delay_minutes = global_config.sleep_system.max_sleep_delay_minutes

                buffer_seconds = 0
                # 如果睡眠压力低于阈值，则计算延迟时间
                if sleep_pressure <= pressure_threshold:
                    # 压力差，归一化到 (0, 1]
                    pressure_diff = (pressure_threshold - sleep_pressure) / pressure_threshold
                    # 延迟分钟数，压力越低，延迟越长
                    delay_minutes = int(pressure_diff * max_delay_minutes)

                    # 确保总延迟不超过当日最大值
                    remaining_delay = max_delay_minutes - self.context.total_delayed_minutes_today
                    delay_minutes = min(delay_minutes, remaining_delay)

                    if delay_minutes > 0:
                        # 增加一些随机性
                        buffer_seconds = random.randint(int(delay_minutes * 0.8 * 60), int(delay_minutes * 1.2 * 60))
                        self.context.total_delayed_minutes_today += buffer_seconds / 60.0
                        logger.info(f"睡眠压力 ({sleep_pressure:.1f}) 较低，延迟 {buffer_seconds / 60:.1f} 分钟入睡。")
                    else:
                        # 延迟额度已用完，设置一个较短的准备时间
                        buffer_seconds = random.randint(1 * 60, 2 * 60)
                        logger.info("今日延迟入睡额度已用完，进入短暂准备后入睡。")
                else:
                    # 睡眠压力较高，设置一个较短的准备时间
                    buffer_seconds = random.randint(1 * 60, 2 * 60)
                    logger.info(f"睡眠压力 ({sleep_pressure:.1f}) 较高，将在短暂准备后入睡。")

                # 发送睡前通知
                if global_config.sleep_system.enable_pre_sleep_notification:
                    asyncio.create_task(NotificationSender.send_goodnight_notification(wakeup_manager.context))

                self.context.sleep_buffer_end_time = now + timedelta(seconds=buffer_seconds)
                self.context.current_state = SleepState.PREPARING_SLEEP
                logger.info(f"进入准备入睡状态，将在 {buffer_seconds / 60:.1f} 分钟内入睡。")
                self.context.save()
            else:
                # 无法获取 wakeup_manager，退回旧逻辑
                buffer_seconds = random.randint(1 * 60, 3 * 60)
                self.context.sleep_buffer_end_time = now + timedelta(seconds=buffer_seconds)
                self.context.current_state = SleepState.PREPARING_SLEEP
                logger.warning("无法获取 WakeUpManager，弹性睡眠采用默认1-3分钟延迟。")
                self.context.save()
        else:
            # 非弹性睡眠模式
            if wakeup_manager and global_config.sleep_system.enable_pre_sleep_notification:
                asyncio.create_task(NotificationSender.send_goodnight_notification(wakeup_manager.context))
            self.context.current_state = SleepState.SLEEPING

    def _handle_preparing_sleep(
        self, now: datetime, is_in_theoretical_sleep: bool, wakeup_manager: Optional["WakeUpManager"]
    ):
        """处理“准备入睡”状态下的逻辑。"""
        # 如果在准备期间离开了理论睡眠时间，则取消入睡
        if not is_in_theoretical_sleep:
            logger.info("准备入睡期间离开理论休眠时间，取消入睡，恢复清醒。")
            self.context.current_state = SleepState.AWAKE
            self.context.sleep_buffer_end_time = None
            self.context.save()
        # 如果缓冲时间结束，则正式进入睡眠状态
        elif self.context.sleep_buffer_end_time and now >= self.context.sleep_buffer_end_time:
            logger.info("睡眠缓冲期结束，正式进入休眠状态。")
            self.context.current_state = SleepState.SLEEPING
            self._last_fully_slept_log_time = now.timestamp()

            # 设置一个随机的延迟，用于触发“睡后失眠”检查
            delay_minutes_range = global_config.sleep_system.insomnia_trigger_delay_minutes
            delay_minutes = random.randint(delay_minutes_range[0], delay_minutes_range[1])
            self.context.sleep_buffer_end_time = now + timedelta(minutes=delay_minutes)
            logger.info(f"已设置睡后失眠检查，将在 {delay_minutes} 分钟后触发。")

            self.context.save()

    def _handle_sleeping(
        self,
        now: datetime,
        is_in_theoretical_sleep: bool,
        activity: str | None,
        wakeup_manager: Optional["WakeUpManager"],
    ):
        """处理“正在睡觉”状态下的逻辑。"""
        # 如果理论睡眠时间结束，则自然醒来
        if not is_in_theoretical_sleep:
            logger.info("理论休眠时间结束，自然醒来。")
            self.context.current_state = SleepState.AWAKE
            self.context.save()
        # 检查是否到了触发“睡后失眠”的时间点
        elif self.context.sleep_buffer_end_time and now >= self.context.sleep_buffer_end_time:
            if wakeup_manager:
                sleep_pressure = wakeup_manager.context.sleep_pressure
                pressure_threshold = global_config.sleep_system.flexible_sleep_pressure_threshold
                # 检查是否触发失眠
                insomnia_reason = None
                if sleep_pressure < pressure_threshold:
                    insomnia_reason = "low_pressure"
                    logger.info(f"睡眠压力 ({sleep_pressure:.1f}) 低于阈值 ({pressure_threshold})，触发睡后失眠。")
                elif random.random() < getattr(global_config.sleep_system, "random_insomnia_chance", 0.1):
                    insomnia_reason = "random"
                    logger.info("随机触发失眠。")

                if insomnia_reason:
                    self.context.current_state = SleepState.INSOMNIA

                    # 设置失眠的持续时间
                    duration_minutes_range = global_config.sleep_system.insomnia_duration_minutes
                    duration_minutes = random.randint(*duration_minutes_range)
                    self.context.sleep_buffer_end_time = now + timedelta(minutes=duration_minutes)

                    # 发送失眠通知
                    asyncio.create_task(
                        NotificationSender.send_insomnia_notification(wakeup_manager.context, insomnia_reason)
                    )
                    logger.info(f"进入失眠状态 (原因: {insomnia_reason})，将持续 {duration_minutes} 分钟。")
                else:
                    # 睡眠压力正常，不触发失眠，清除检查时间点
                    logger.info(f"睡眠压力 ({sleep_pressure:.1f}) 正常，未触发睡后失眠。")
                    self.context.sleep_buffer_end_time = None
                self.context.save()
        else:
            # 定期记录睡眠日志
            current_timestamp = now.timestamp()
            if current_timestamp - self.last_sleep_log_time > self.sleep_log_interval and activity:
                logger.info(f"当前处于休眠活动 '{activity}' 中。")
                self.last_sleep_log_time = current_timestamp

    def _handle_insomnia(self, now: datetime, is_in_theoretical_sleep: bool):
        """处理“失眠”状态下的逻辑。"""
        # 如果离开理论睡眠时间，则失眠结束
        if not is_in_theoretical_sleep:
            logger.info("已离开理论休眠时间，失眠结束，恢复清醒。")
            self.context.current_state = SleepState.AWAKE
            self.context.sleep_buffer_end_time = None
            self.context.save()
        # 如果失眠持续时间已过，则恢复睡眠
        elif self.context.sleep_buffer_end_time and now >= self.context.sleep_buffer_end_time:
            logger.info("失眠状态持续时间已过，恢复睡眠。")
            self.context.current_state = SleepState.SLEEPING
            self.context.sleep_buffer_end_time = None
            self.context.save()

    def _handle_woken_up(self, now: datetime, is_in_theoretical_sleep: bool, wakeup_manager: Optional["WakeUpManager"]):
        """处理“被吵醒”状态下的逻辑。"""
        # 如果理论睡眠时间结束，则状态自动结束
        if not is_in_theoretical_sleep:
            logger.info("理论休眠时间结束，被吵醒的状态自动结束。")
            self.context.current_state = SleepState.AWAKE
            self.context.re_sleep_attempt_time = None
            self.context.save()
        # 到了尝试重新入睡的时间点
        elif self.context.re_sleep_attempt_time and now >= self.context.re_sleep_attempt_time:
            logger.info("被吵醒后经过一段时间，尝试重新入睡...")
            if wakeup_manager:
                sleep_pressure = wakeup_manager.context.sleep_pressure
                pressure_threshold = global_config.sleep_system.flexible_sleep_pressure_threshold

                # 如果睡眠压力足够，则尝试重新入睡
                if sleep_pressure >= pressure_threshold:
                    logger.info("睡眠压力足够，从被吵醒状态转换到准备入睡。")
                    buffer_seconds = random.randint(3 * 60, 8 * 60)
                    self.context.sleep_buffer_end_time = now + timedelta(seconds=buffer_seconds)
                    self.context.current_state = SleepState.PREPARING_SLEEP
                    self.context.re_sleep_attempt_time = None
                else:
                    # 睡眠压力不足，延迟一段时间后再次尝试
                    delay_minutes = 15
                    self.context.re_sleep_attempt_time = now + timedelta(minutes=delay_minutes)
                    logger.info(
                        f"睡眠压力({sleep_pressure:.1f})仍然较低，暂时保持清醒，在 {delay_minutes} 分钟后再次尝试。"
                    )
                self.context.save()

    def reset_sleep_state_after_wakeup(self):
        """
        当角色被用户消息等外部因素唤醒时调用此方法。
        将状态强制转换为 WOKEN_UP，并设置一个延迟，之后会尝试重新入睡。
        """
        if self.context.current_state in [SleepState.PREPARING_SLEEP, SleepState.SLEEPING, SleepState.INSOMNIA]:
            logger.info("被唤醒，进入 WOKEN_UP 状态！")
            self.context.current_state = SleepState.WOKEN_UP
            self.context.sleep_buffer_end_time = None
            re_sleep_delay_minutes = getattr(global_config.sleep_system, "re_sleep_delay_minutes", 10)
            self.context.re_sleep_attempt_time = datetime.now() + timedelta(minutes=re_sleep_delay_minutes)
            logger.info(f"将在 {re_sleep_delay_minutes} 分钟后尝试重新入睡。")
            self.context.save()
