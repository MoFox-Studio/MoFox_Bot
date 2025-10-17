import random
from datetime import datetime, time, timedelta
from typing import Any

from src.common.logger import get_logger
from src.config.config import global_config
from src.schedule.schedule_manager import schedule_manager

logger = get_logger("time_checker")


class TimeChecker:
    def __init__(self):
        # 缓存当天的偏移量，确保一天内使用相同的偏移量
        self._daily_sleep_offset: int = 0
        self._daily_wake_offset: int = 0
        self._offset_date = None

    def _get_daily_offsets(self):
        """获取当天的睡眠和起床时间偏移量，每天生成一次"""
        today = datetime.now().date()

        # 如果是新的一天，重新生成偏移量
        if self._offset_date != today:
            sleep_offset_range = global_config.sleep_system.sleep_time_offset_minutes
            wake_offset_range = global_config.sleep_system.wake_up_time_offset_minutes

            # 生成 ±offset_range 范围内的随机偏移量
            self._daily_sleep_offset = random.randint(-sleep_offset_range, sleep_offset_range)
            self._daily_wake_offset = random.randint(-wake_offset_range, wake_offset_range)
            self._offset_date = today

            logger.debug(
                f"生成新的每日偏移量 - 睡觉时间偏移: {self._daily_sleep_offset}分钟, 起床时间偏移: {self._daily_wake_offset}分钟"
            )

        return self._daily_sleep_offset, self._daily_wake_offset

    @staticmethod
    def get_today_schedule() -> list[dict[str, Any]] | None:
        """从全局 ScheduleManager 获取今天的日程安排。"""
        return schedule_manager.today_schedule

    def is_in_theoretical_sleep_time(self, now_time: time) -> tuple[bool, str | None]:
        if global_config.sleep_system.sleep_by_schedule:
            if self.get_today_schedule():
                return self._is_in_schedule_sleep_time(now_time)
            else:
                return self._is_in_sleep_time(now_time)
        else:
            return self._is_in_sleep_time(now_time)

    def _is_in_schedule_sleep_time(self, now_time: time) -> tuple[bool, str | None]:
        """检查当前时间是否落在日程表的任何一个睡眠活动中"""
        sleep_keywords = ["休眠", "睡觉", "梦乡"]
        today_schedule = self.get_today_schedule()
        if today_schedule:
            for event in today_schedule:
                try:
                    activity = event.get("activity", "").strip()
                    time_range = event.get("time_range")

                    if not activity or not time_range:
                        continue

                    if any(keyword in activity for keyword in sleep_keywords):
                        start_str, end_str = time_range.split("-")
                        start_time = datetime.strptime(start_str.strip(), "%H:%M").time()
                        end_time = datetime.strptime(end_str.strip(), "%H:%M").time()

                        if start_time <= end_time:  # 同一天
                            if start_time <= now_time < end_time:
                                return True, activity
                        else:  # 跨天
                            if now_time >= start_time or now_time < end_time:
                                return True, activity
                except (ValueError, KeyError, AttributeError) as e:
                    logger.warning(f"解析日程事件时出错: {event}, 错误: {e}")
                    continue
        return False, None

    def _is_in_sleep_time(self, now_time: time) -> tuple[bool, str | None]:
        """检查当前时间是否在固定的睡眠时间内（应用偏移量）"""
        try:
            start_time_str = global_config.sleep_system.fixed_sleep_time
            end_time_str = global_config.sleep_system.fixed_wake_up_time

            # 获取当天的偏移量
            sleep_offset, wake_offset = self._get_daily_offsets()

            # 解析基础时间
            base_start_time = datetime.strptime(start_time_str, "%H:%M")
            base_end_time = datetime.strptime(end_time_str, "%H:%M")

            # 应用偏移量
            actual_start_time = (base_start_time + timedelta(minutes=sleep_offset)).time()
            actual_end_time = (base_end_time + timedelta(minutes=wake_offset)).time()

            logger.debug(
                f"固定睡眠时间检查 - 基础时间: {start_time_str}-{end_time_str}, "
                f"偏移后时间: {actual_start_time.strftime('%H:%M')}-{actual_end_time.strftime('%H:%M')}, "
                f"当前时间: {now_time.strftime('%H:%M')}"
            )

            if actual_start_time <= actual_end_time:
                if actual_start_time <= now_time < actual_end_time:
                    return (
                        True,
                        f"固定睡眠时间(偏移后: {actual_start_time.strftime('%H:%M')}-{actual_end_time.strftime('%H:%M')})",
                    )
            else:
                if now_time >= actual_start_time or now_time < actual_end_time:
                    return (
                        True,
                        f"固定睡眠时间(偏移后: {actual_start_time.strftime('%H:%M')}-{actual_end_time.strftime('%H:%M')})",
                    )
        except ValueError as e:
            logger.error(f"固定的睡眠时间格式不正确，请使用 HH:MM 格式: {e}")
        return False, None
