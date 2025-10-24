"""
睡眠系统的工具函数模块

此模块包含了被 state_manager 和 sleep_logic 共享的、无状态的逻辑函数，
目的是为了解决循环引用的问题。
"""
import random
from datetime import datetime, timedelta

from src.common.logger import get_logger
from src.config.config import global_config
from src.schedule.schedule_manager import schedule_manager

logger = get_logger("sleep_utils")


def _get_fixed_sleep_times(now: datetime) -> tuple[datetime | None, datetime | None]:
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


def _get_sleep_times_from_schedule(now: datetime) -> tuple[datetime | None, datetime | None]:
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
            if "sleep" in activity or "睡觉" in activity  or "入睡" in activity or "进入梦乡" in activity:
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


def should_be_sleeping(now: datetime) -> tuple[bool, datetime | None]:
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
        sleep_time, _ = _get_sleep_times_from_schedule(now)
        if not sleep_time:
            logger.debug("日程表模式开启，但未找到睡眠时间，使用固定时间作为备用。")
            sleep_time, wake_up_time = _get_fixed_sleep_times(now)
    else:
        sleep_time, wake_up_time = _get_fixed_sleep_times(now)

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