import random
from datetime import datetime, timedelta

from src.common.logger import get_logger
from src.config.config import global_config
from src.schedule.schedule_manager import schedule_manager
from . import utils
from .state_manager import SleepState,get_sleep_state_manager



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
        current_state = get_sleep_state_manager().get_current_state()
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
        from .state_manager import SleepState
        should_sleep, wake_up_time = utils.should_be_sleeping(now)
        if should_sleep:
            logger.info("判断结果：应进入睡眠状态。")
            get_sleep_state_manager().set_state(SleepState.SLEEPING, wake_up=wake_up_time)

    def _check_should_wake_up(self, now: datetime):
        """
        当状态为 SLEEPING 时，检查是否应该醒来。
        这里包含了处理跨天获取日程的核心逻辑。
        """
        from .state_manager import SleepState
        wake_up_time = get_sleep_state_manager().get_wake_up_time()

        # 核心逻辑：两段式检测
        # 如果 state_manager 中还没有起床时间，说明是昨晚入睡，需要等待今天凌晨的新日程。
        sleep_start_time = get_sleep_state_manager().get_sleep_start_time()
        if not wake_up_time:
            if sleep_start_time and now.date() > sleep_start_time.date():
                logger.debug("当前为睡眠状态但无起床时间，尝试从新日程中解析...")
                _, new_wake_up_time = self._get_wakeup_times_from_schedule(now)

                if new_wake_up_time:
                    logger.info(f"成功从新日程获取到起床时间: {new_wake_up_time.strftime('%H:%M')}")
                    get_sleep_state_manager().set_wake_up_time(new_wake_up_time)
                    wake_up_time = new_wake_up_time
                else:
                    logger.debug("未能获取到新的起床时间，继续睡眠。")
                    return
            else:
                logger.info("还没有到达第二天,继续睡眠。")
        if wake_up_time:
            logger.info(f"尚未到苏醒时间,苏醒时间在{wake_up_time}")
        if wake_up_time and now >= wake_up_time:
            logger.info(f"当前时间 {now.strftime('%H:%M')} 已到达或超过预定起床时间 {wake_up_time.strftime('%H:%M')}。")
            get_sleep_state_manager().set_state(SleepState.AWAKE)

    def _get_wakeup_times_from_schedule(self, now: datetime) -> tuple[datetime | None, datetime | None]:
            """
            当使用“日程表”模式时，从此方法获取wakeup时间。
            实现了核心逻辑：
            - 解析“今天”日程中的wakeup时间。
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
