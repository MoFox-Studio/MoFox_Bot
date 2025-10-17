from datetime import datetime, time, timedelta
import random
from typing import Optional, Tuple

from src.common.logger import get_logger
from src.config.config import global_config
from src.schedule.schedule_manager import schedule_manager
from .state_manager import SleepState, sleep_state_manager

logger = get_logger("sleep_logic")


class SleepLogic:
    """
    核心睡眠逻辑，睡眠系统的“大脑”

    负责根据当前的配置、时间、日程表以及状态，判断是否需要切换睡眠状态。
    它本身是无状态的，所有的状态都读取和写入 SleepStateManager。
    """

    def check_and_update_sleep_state(self):
        """
        检查并更新当前的睡眠状态，这是整个逻辑的入口。
        由定时任务周期性调用。
        """
        current_state = sleep_state_manager.get_current_state()
        now = datetime.now()

        if current_state == SleepState.AWAKE:
            self._check_should_fall_asleep(now)
        elif current_state == SleepState.SLEEPING:
            self._check_should_wake_up(now)
        elif current_state == SleepState.INSOMNIA:
            # TODO: 实现失眠逻辑
            # 例如：检查失眠状态是否结束，如果结束则转换回 SLEEPING
            pass
        elif current_state == SleepState.WOKEN_UP_ANGRY:
            # TODO: 实现起床气逻辑
            # 例如：检查生气状态是否结束，如果结束则转换回 SLEEPING 或 AWAKE
            pass

    def _check_should_fall_asleep(self, now: datetime):
        """
        当状态为 AWAKE 时，检查是否应该进入睡眠。
        """
        should_sleep, wake_up_time = self._should_be_sleeping(now)
        if should_sleep:
            logger.info("判断结果：应进入睡眠状态。")
            sleep_state_manager.set_state(SleepState.SLEEPING, wake_up=wake_up_time)

    def _check_should_wake_up(self, now: datetime):
        """
        当状态为 SLEEPING 时，检查是否应该醒来。
        这里包含了处理跨天获取日程的核心逻辑。
        """
        wake_up_time = sleep_state_manager.get_wake_up_time()

        # 核心逻辑：两段式检测
        # 如果 state_manager 中还没有起床时间，说明是昨晚入睡，需要等待今天凌晨的新日程。
        sleep_start_time = sleep_state_manager.get_sleep_start_time()
        if not wake_up_time and sleep_start_time and now.date() > sleep_start_time.date():
            logger.debug("当前为睡眠状态但无起床时间，尝试从新日程中解析...")
            _, new_wake_up_time = self._get_wakeup_times_from_schedule(now)

            if new_wake_up_time:
                logger.info(f"成功从新日程获取到起床时间: {new_wake_up_time.strftime('%H:%M')}")
                sleep_state_manager.set_wake_up_time(new_wake_up_time)
                wake_up_time = new_wake_up_time
            else:
                logger.debug("未能获取到新的起床时间，继续睡眠。")
                return
        else:
            logger.info("还没有到达第二天,继续睡眠。")

        if wake_up_time and now >= wake_up_time:
            logger.info(f"当前时间 {now.strftime('%H:%M')} 已到达或超过预定起床时间 {wake_up_time.strftime('%H:%M')}。")
            sleep_state_manager.set_state(SleepState.AWAKE)

    def _should_be_sleeping(self, now: datetime) -> Tuple[bool, Optional[datetime]]:
        """
        判断在当前时刻，是否应该处于睡眠时间。

        Returns:
            元组 (是否应该睡眠, 预期的起床时间或None)
        """
        sleep_config = global_config.sleep_system
        if not sleep_config.enable:
            return False, None

        sleep_time, wake_up_time = None, None

        if sleep_config.sleep_by_schedule:
            sleep_time, _ = self._get_sleep_times_from_schedule(now)
            if not sleep_time:
                logger.debug("日程表模式开启，但未找到睡眠时间，使用固定时间作为备用。")
                sleep_time, wake_up_time = self._get_fixed_sleep_times(now)
        else:
            sleep_time, wake_up_time = self._get_fixed_sleep_times(now)

        if not sleep_time:
            return False, None

        # 检查当前时间是否在睡眠时间范围内
        if now >= sleep_time:
            # 如果起床时间是第二天（通常情况），且当前时间小于起床时间，则在睡眠范围内
            if wake_up_time and wake_up_time > sleep_time and now < wake_up_time:
                 return True, wake_up_time
            # 如果当前时间大于入睡时间，说明已经进入睡眠窗口
            return True, wake_up_time
            
        return False, None

    def _get_fixed_sleep_times(self, now: datetime) -> Tuple[Optional[datetime], Optional[datetime]]:
        """
        当使用“固定时间”模式时，从此方法计算睡眠和起床时间。
        会加入配置中的随机偏移量，让作息更自然。
        """
        sleep_config = global_config.sleep_system
        try:
            sleep_offset = random.randint(
                -sleep_config.sleep_time_offset_minutes, sleep_config.sleep_time_offset_minutes
            )
            wake_up_offset = random.randint(
                -sleep_config.wake_up_time_offset_minutes, sleep_config.wake_up_time_offset_minutes
            )

            sleep_t = datetime.strptime(sleep_config.fixed_sleep_time, "%H:%M").time()
            wake_up_t = datetime.strptime(sleep_config.fixed_wake_up_time, "%H:%M").time()

            sleep_time = datetime.combine(now.date(), sleep_t) + timedelta(minutes=sleep_offset)
            
            # 如果起床时间比睡觉时间早，说明是第二天
            wake_up_day = now.date() + timedelta(days=1) if wake_up_t < sleep_t else now.date()
            wake_up_time = datetime.combine(wake_up_day, wake_up_t) + timedelta(minutes=wake_up_offset)

            return sleep_time, wake_up_time
        except (ValueError, TypeError) as e:
            logger.error(f"解析固定睡眠时间失败: {e}")
            return None, None

    def _get_sleep_times_from_schedule(self, now: datetime) -> Tuple[Optional[datetime], Optional[datetime]]:
        """
        当使用“日程表”模式时，从此方法获取睡眠时间。
        实现了核心逻辑：
        - 解析“今天”日程中的睡觉时间。
        """
        # 阶段一：获取当天的睡觉时间
        today_schedule = schedule_manager.today_schedule
        sleep_time = None
        if today_schedule:
            for event in today_schedule:
                activity = event.get("activity", "").lower()
                if "sleep" in activity or "睡觉" in activity or "休息" in activity:
                    try:
                        time_range = event.get("time_range", "")
                        start_str, _ = time_range.split("-")
                        sleep_t = datetime.strptime(start_str.strip(), "%H:%M").time()
                        sleep_time = datetime.combine(now.date(), sleep_t)
                        break
                    except (ValueError, AttributeError):
                        logger.warning(f"解析日程中的睡眠时间失败: {event}")
                        continue
        wake_up_time = None

        return sleep_time, wake_up_time
    
    def _get_wakeup_times_from_schedule(self, now: datetime) -> Tuple[Optional[datetime], Optional[datetime]]:
            """
            当使用“日程表”模式时，从此方法获取睡眠时间。
            实现了核心逻辑：
            - 解析“今天”日程中的睡觉时间。
            """
            # 阶段一：获取当天的睡觉时间
            today_schedule = schedule_manager.today_schedule
            wake_up_time = None
            if today_schedule:
                for event in today_schedule:
                    activity = event.get("activity", "").lower()
                    if "wake_up" in activity or "醒来" in activity or "起床" in activity:
                        try:
                            time_range = event.get("time_range", "")
                            start_str, _ = time_range.split("-")
                            sleep_t = datetime.strptime(start_str.strip(), "%H:%M").time()
                            wake_up_time = datetime.combine(now.date(), sleep_t)
                            break
                        except (ValueError, AttributeError):
                            logger.warning(f"解析日程中的睡眠时间失败: {event}")
                            continue

            return None, wake_up_time


# 全局单例
sleep_logic = SleepLogic()