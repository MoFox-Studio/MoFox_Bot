"""
日程表与月度计划查询API模块

本模块提供了一系列用于查询日程和月度计划的只读接口。
所有对外接口均为异步函数，专为插件开发者设计，以便在异步环境中无缝集成。

核心功能：
- 查询指定日期的日程安排。
- 获取当前正在进行的活动。
- 筛选特定时间范围内的活动。
- 查询月度计划，支持随机抽样和计数。
- 所有查询接口均提供格式化输出选项。

使用方式：
    import asyncio
    from src.plugin_system.apis import schedule_api

    async def main():
        # 获取今天的日程（原始数据）
        today_schedule = await schedule_api.get_schedule()
        if today_schedule:
            print("今天的日程:", today_schedule)

        # 获取昨天的日程，并格式化为字符串
        from datetime import datetime, timedelta
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        formatted_schedule = await schedule_api.get_schedule(date=yesterday, formatted=True)
        if formatted_schedule:
            print(f"\\n昨天的日程 (格式化):\\n{formatted_schedule}")

        # 获取当前活动
        current_activity = await schedule_api.get_current_activity()
        if current_activity:
            print(f"\\n当前活动: {current_activity.get('activity')}")

        # 获取本月月度计划总数
        plan_count = await schedule_api.count_monthly_plans()
        print(f"\\n本月月度计划总数: {plan_count}")

        # 随机获取本月的2个计划
        random_plans = await schedule_api.get_monthly_plans(random_count=2)
        if random_plans:
            print("\\n随机的2个计划:", [p.plan_text for p in random_plans])

    asyncio.run(main())
"""

import random
from datetime import datetime, time
from typing import Any, List, Optional, Union

import orjson
from sqlalchemy import func, select

from src.common.database.sqlalchemy_models import MonthlyPlan, Schedule, get_db_session
from src.common.logger import get_logger
from src.schedule.database import get_active_plans_for_month

logger = get_logger("schedule_api")


# --- 内部辅助函数 ---

def _format_schedule_list(
    items: Union[List[dict[str, Any]], List[MonthlyPlan]],
    template: str,
    item_type: str,
) -> str:
    """将日程或计划列表格式化为字符串"""
    if not items:
        return "无"

    lines = []
    for item in items:
        if item_type == "schedule" and isinstance(item, dict):
            lines.append(template.format(time_range=item.get("time_range", ""), activity=item.get("activity", "")))
        elif item_type == "plan" and isinstance(item, MonthlyPlan):
            lines.append(template.format(plan_text=item.plan_text))
    return "\\n".join(lines)


async def _get_schedule_from_db(date_str: str) -> Optional[List[dict[str, Any]]]:
    """从数据库中获取并解析指定日期的日程"""
    async with get_db_session() as session:
        result = await session.execute(select(Schedule).filter(Schedule.date == date_str))
        schedule_record = result.scalars().first()
        if schedule_record and schedule_record.schedule_data:
            try:
                return orjson.loads(str(schedule_record.schedule_data))
            except orjson.JSONDecodeError:
                logger.warning(f"无法解析数据库中的日程数据 (日期: {date_str})")
    return None


# --- API实现 ---


class ScheduleAPI:
    """日程表与月度计划查询API"""

    @staticmethod
    async def get_schedule(
        date: Optional[str] = None,
        formatted: bool = False,
        format_template: str = "{time_range}: {activity}",
    ) -> Union[List[dict[str, Any]], str, None]:
        """
        (异步) 获取指定日期的日程安排。

        Args:
            date (Optional[str]): 目标日期，格式 "YYYY-MM-DD"。如果为None，则使用当前日期。
            formatted (bool): 如果为True，返回格式化的字符串；否则返回原始数据列表。
            format_template (str): 当 formatted=True 时使用的格式化模板。

        Returns:
            Union[List[Dict[str, Any]], str, None]: 日程数据或None。
        """
        target_date = date or datetime.now().strftime("%Y-%m-%d")
        try:
            logger.debug(f"[ScheduleAPI] 正在获取 {target_date} 的日程安排...")
            schedule_data = await _get_schedule_from_db(target_date)
            if schedule_data is None:
                return None
            if formatted:
                return _format_schedule_list(schedule_data, format_template, "schedule")
            return schedule_data
        except Exception as e:
            logger.error(f"[ScheduleAPI] 获取 {target_date} 日程失败: {e}")
            return None

    @staticmethod
    async def get_current_activity(
        formatted: bool = False,
        format_template: str = "{time_range}: {activity}",
    ) -> Union[dict[str, Any], str, None]:
        """
        (异步) 获取当前正在进行的活动。

        Args:
            formatted (bool): 如果为True，返回格式化的字符串；否则返回活动字典。
            format_template (str): 当 formatted=True 时使用的格式化模板。

        Returns:
            Union[Dict[str, Any], str, None]: 当前活动数据或None。
        """
        try:
            logger.debug("[ScheduleAPI] 正在获取当前活动...")
            today_schedule = await _get_schedule_from_db(datetime.now().strftime("%Y-%m-%d"))
            if not today_schedule:
                return None

            now = datetime.now().time()
            for event in today_schedule:
                time_range = event.get("time_range")
                if not time_range:
                    continue
                try:
                    start_str, end_str = time_range.split("-")
                    start_time = datetime.strptime(start_str.strip(), "%H:%M").time()
                    end_time = datetime.strptime(end_str.strip(), "%H:%M").time()
                    if (start_time <= now < end_time) or \
                       (end_time < start_time and (now >= start_time or now < end_time)):
                        if formatted:
                            return _format_schedule_list([event], format_template, "schedule")
                        return event
                except (ValueError, KeyError):
                    continue
            return None
        except Exception as e:
            logger.error(f"[ScheduleAPI] 获取当前活动失败: {e}")
            return None

    @staticmethod
    async def get_activities_between(
        start_time: str,
        end_time: str,
        date: Optional[str] = None,
        formatted: bool = False,
        format_template: str = "{time_range}: {activity}",
    ) -> Union[List[dict[str, Any]], str, None]:
        """
        (异步) 获取指定日期和时间范围内的所有活动。

        Args:
            start_time (str): 开始时间，格式 "HH:MM"。
            end_time (str): 结束时间，格式 "HH:MM"。
            date (Optional[str]): 目标日期，格式 "YYYY-MM-DD"。如果为None，则使用当前日期。
            formatted (bool): 如果为True，返回格式化的字符串；否则返回活动列表。
            format_template (str): 当 formatted=True 时使用的格式化模板。

        Returns:
            Union[List[Dict[str, Any]], str, None]: 在时间范围内的活动列表或None。
        """
        target_date = date or datetime.now().strftime("%Y-%m-%d")
        try:
            logger.debug(f"[ScheduleAPI] 正在获取 {target_date} 从 {start_time} 到 {end_time} 的活动...")
            schedule_data = await _get_schedule_from_db(target_date)
            if not schedule_data:
                return None

            start = datetime.strptime(start_time, "%H:%M").time()
            end = datetime.strptime(end_time, "%H:%M").time()
            activities_in_range = []

            for event in schedule_data:
                time_range = event.get("time_range")
                if not time_range:
                    continue
                try:
                    event_start_str, event_end_str = time_range.split("-")
                    event_start = datetime.strptime(event_start_str.strip(), "%H:%M").time()
                    if start <= event_start < end:
                        activities_in_range.append(event)
                except (ValueError, KeyError):
                    continue

            if formatted:
                return _format_schedule_list(activities_in_range, format_template, "schedule")
            return activities_in_range
        except Exception as e:
            logger.error(f"[ScheduleAPI] 获取时间段内活动失败: {e}")
            return None

    @staticmethod
    async def get_monthly_plans(
        target_month: Optional[str] = None,
        random_count: Optional[int] = None,
        formatted: bool = False,
        format_template: str = "- {plan_text}",
    ) -> Union[List[MonthlyPlan], str, None]:
        """
        (异步) 获取指定月份的有效月度计划。

        Args:
            target_month (Optional[str]): 目标月份，格式 "YYYY-MM"。如果为None，则使用当前月份。
            random_count (Optional[int]): 如果设置，将随机返回指定数量的计划。
            formatted (bool): 如果为True，返回格式化的字符串；否则返回对象列表。
            format_template (str): 当 formatted=True 时使用的格式化模板。

        Returns:
            Union[List[MonthlyPlan], str, None]: 月度计划列表、格式化字符串或None。
        """
        month = target_month or datetime.now().strftime("%Y-%m")
        try:
            logger.debug(f"[ScheduleAPI] 正在获取 {month} 的月度计划...")
            plans = await get_active_plans_for_month(month)
            if not plans:
                return None

            if random_count is not None and random_count > 0 and len(plans) > random_count:
                plans = random.sample(plans, random_count)

            if formatted:
                return _format_schedule_list(plans, format_template, "plan")
            return plans
        except Exception as e:
            logger.error(f"[ScheduleAPI] 获取 {month} 月度计划失败: {e}")
            return None

    @staticmethod
    async def count_monthly_plans(target_month: Optional[str] = None) -> int:
        """
        (异步) 获取指定月份的有效月度计划总数。

        Args:
            target_month (Optional[str]): 目标月份，格式 "YYYY-MM"。如果为None，则使用当前月份。

        Returns:
            int: 有效月度计划的数量。
        """
        month = target_month or datetime.now().strftime("%Y-%m")
        try:
            logger.debug(f"[ScheduleAPI] 正在统计 {month} 的月度计划数量...")
            async with get_db_session() as session:
                result = await session.execute(
                    select(func.count(MonthlyPlan.id)).where(
                        MonthlyPlan.target_month == month, MonthlyPlan.status == "active"
                    )
                )
                return result.scalar_one() or 0
        except Exception as e:
            logger.error(f"[ScheduleAPI] 统计 {month} 月度计划数量失败: {e}")
            return 0


# =============================================================================
# 模块级别的便捷函数 (全部为异步)
# =============================================================================

async def get_schedule(
    date: Optional[str] = None,
    formatted: bool = False,
    format_template: str = "{time_range}: {activity}",
) -> Union[List[dict[str, Any]], str, None]:
    """(异步) 获取指定日期的日程安排的便捷函数。"""
    return await ScheduleAPI.get_schedule(date, formatted, format_template)


async def get_current_activity(
    formatted: bool = False,
    format_template: str = "{time_range}: {activity}",
) -> Union[dict[str, Any], str, None]:
    """(异步) 获取当前正在进行的活动的便捷函数。"""
    return await ScheduleAPI.get_current_activity(formatted, format_template)


async def get_activities_between(
    start_time: str,
    end_time: str,
    date: Optional[str] = None,
    formatted: bool = False,
    format_template: str = "{time_range}: {activity}",
) -> Union[List[dict[str, Any]], str, None]:
    """(异步) 获取指定时间范围内活动的便捷函数。"""
    return await ScheduleAPI.get_activities_between(start_time, end_time, date, formatted, format_template)


async def get_monthly_plans(
    target_month: Optional[str] = None,
    random_count: Optional[int] = None,
    formatted: bool = False,
    format_template: str = "- {plan_text}",
) -> Union[List[MonthlyPlan], str, None]:
    """(异步) 获取月度计划的便捷函数。"""
    return await ScheduleAPI.get_monthly_plans(target_month, random_count, formatted, format_template)


async def count_monthly_plans(target_month: Optional[str] = None) -> int:
    """(异步) 获取月度计划总数的便捷函数。"""
    return await ScheduleAPI.count_monthly_plans(target_month)
