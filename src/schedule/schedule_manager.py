import asyncio
from datetime import datetime, time, timedelta
from typing import Any

import orjson
from sqlalchemy import select

from src.common.database.sqlalchemy_models import MonthlyPlan, Schedule, get_db_session
from src.common.logger import get_logger
from src.config.config import global_config
from src.manager.async_task_manager import AsyncTask, async_task_manager

from .database import update_plan_usage
from .llm_generator import ScheduleLLMGenerator
from .plan_manager import PlanManager
from .schemas import ScheduleData

logger = get_logger("schedule_manager")


class ScheduleManager:
    def __init__(self):
        self.today_schedule: list[dict[str, Any]] | None = None
        self.llm_generator = ScheduleLLMGenerator()
        self.plan_manager = PlanManager()
        self.daily_task_started = False
        self.schedule_generation_running = False

    async def initialize(self):
        if global_config.planning_system.schedule_enable:
            logger.info("日程表功能已启用，正在初始化管理器...")
            await self.load_or_generate_today_schedule()
            await self.start_daily_schedule_generation()
            logger.info("日程表管理器初始化成功。")

    async def start_daily_schedule_generation(self):
        if not self.daily_task_started:
            logger.info("正在启动每日日程生成任务...")
            task = DailyScheduleGenerationTask(self)
            await async_task_manager.add_task(task)
            self.daily_task_started = True
            logger.info("每日日程生成任务已成功启动。")
        else:
            logger.info("每日日程生成任务已在运行中。")

    async def load_or_generate_today_schedule(self):
        if not global_config.planning_system.schedule_enable:
            logger.info("日程管理功能已禁用，跳过日程加载和生成。")
            return

        today_str = datetime.now().strftime("%Y-%m-%d")
        try:
            schedule_data = await self._load_schedule_from_db(today_str)
            if schedule_data:
                self.today_schedule = schedule_data
                self._log_loaded_schedule(today_str)
                return

            logger.info(f"数据库中未找到今天的日程 ({today_str})，将调用 LLM 生成。")
            await self.generate_and_save_schedule()

        except Exception as e:
            logger.error(f"加载或生成日程时出错: {e}")
            logger.info("尝试生成日程作为备用方案...")
            await self.generate_and_save_schedule()

    async def _load_schedule_from_db(self, date_str: str) -> list[dict[str, Any]] | None:
        async with get_db_session() as session:
            result = await session.execute(select(Schedule).filter(Schedule.date == date_str))
            schedule_record = result.scalars().first()
            if schedule_record:
                logger.info(f"从数据库加载今天的日程 ({date_str})。")
                schedule_data = orjson.loads(str(schedule_record.schedule_data))
                if self._validate_schedule_with_pydantic(schedule_data):
                    return schedule_data
                else:
                    logger.warning("数据库中的日程数据格式无效，将重新生成日程")
        return None

    def _log_loaded_schedule(self, date_str: str):
        schedule_str = f"已成功加载今天的日程 ({date_str})：\n"
        if self.today_schedule:
            for item in self.today_schedule:
                schedule_str += f"  - {item.get('time_range', '未知时间')}: {item.get('activity', '未知活动')}\n"
        logger.info(schedule_str)

    async def generate_and_save_schedule(self):
        if self.schedule_generation_running:
            logger.info("日程生成任务已在运行中，跳过重复启动")
            return
        logger.info("检测到需要生成日程，已提交后台任务。")
        task = OnDemandScheduleGenerationTask(self)
        await async_task_manager.add_task(task)

    async def _async_generate_and_save_schedule(self):
        self.schedule_generation_running = True
        try:
            today_str = datetime.now().strftime("%Y-%m-%d")
            current_month_str = datetime.now().strftime("%Y-%m")

            sampled_plans = []
            if global_config.planning_system.monthly_plan_enable:
                await self.plan_manager.ensure_and_generate_plans_if_needed(current_month_str)
                sampled_plans = await self.plan_manager.get_plans_for_schedule(current_month_str, max_count=3)

            schedule_data = await self.llm_generator.generate_schedule_with_llm(sampled_plans)

            if schedule_data:
                await self._save_schedule_to_db(today_str, schedule_data)
                self.today_schedule = schedule_data
                self._log_generated_schedule(today_str, schedule_data, sampled_plans)

                if sampled_plans:
                    used_plan_ids = [plan.id for plan in sampled_plans]
                    logger.info(f"更新使用过的月度计划 {used_plan_ids} 的统计信息。")
                    await update_plan_usage(used_plan_ids, today_str)
        finally:
            self.schedule_generation_running = False
            logger.info("日程生成任务结束")

    @staticmethod
    async def _save_schedule_to_db(date_str: str, schedule_data: list[dict[str, Any]]):
        async with get_db_session() as session:
            schedule_json = orjson.dumps(schedule_data).decode("utf-8")
            result = await session.execute(select(Schedule).filter(Schedule.date == date_str))
            existing_schedule = result.scalars().first()
            if existing_schedule:
                existing_schedule.schedule_data = schedule_json
                existing_schedule.updated_at = datetime.now()
            else:
                new_schedule = Schedule(date=date_str, schedule_data=schedule_json)
                session.add(new_schedule)
            await session.commit()

    @staticmethod
    def _log_generated_schedule(
        date_str: str, schedule_data: list[dict[str, Any]], sampled_plans: list[MonthlyPlan]
    ):
        schedule_str = f"成功生成并保存今天的日程 ({date_str})：\n"

        if sampled_plans:
            plan_texts = "\n".join([f"  - {plan.plan_text}" for plan in sampled_plans])
            schedule_str += f"本次日程参考的月度计划:\n{plan_texts}\n"

        schedule_str += "今日日程详情:\n"
        for item in schedule_data:
            schedule_str += f"  - {item.get('time_range', '未知时间')}: {item.get('activity', '未知活动')}\n"
        logger.info(schedule_str)

    def get_current_activity(self) -> str | None:
        if not global_config.planning_system.schedule_enable or not self.today_schedule:
            return None
        now = datetime.now().time()
        for event in self.today_schedule:
            try:
                time_range = event.get("time_range")
                activity = event.get("activity")
                if not time_range or not activity:
                    continue
                start_str, end_str = time_range.split("-")
                start_time = datetime.strptime(start_str.strip(), "%H:%M").time()
                end_time = datetime.strptime(end_str.strip(), "%H:%M").time()
                if (start_time <= now < end_time) or (end_time < start_time and (now >= start_time or now < end_time)):
                    return activity
            except (ValueError, KeyError, AttributeError) as e:
                logger.warning(f"解析日程事件失败: {event}, 错误: {e}")
        return None

    @staticmethod
    def _validate_schedule_with_pydantic(schedule_data) -> bool:
        try:
            ScheduleData(schedule=schedule_data)
            return True
        except Exception:
            return False


class OnDemandScheduleGenerationTask(AsyncTask):
    def __init__(self, schedule_manager: "ScheduleManager"):
        task_name = f"OnDemandScheduleGenerationTask-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        super().__init__(task_name=task_name)
        self.schedule_manager = schedule_manager

    async def run(self):
        logger.info(f"后台任务 {self.task_name} 开始执行日程生成。")
        await self.schedule_manager._async_generate_and_save_schedule()
        logger.info(f"后台任务 {self.task_name} 完成。")


class DailyScheduleGenerationTask(AsyncTask):
    def __init__(self, schedule_manager: "ScheduleManager"):
        super().__init__(task_name="DailyScheduleGenerationTask")
        self.schedule_manager = schedule_manager

    async def run(self):
        while True:
            try:
                now = datetime.now()
                tomorrow = now.date() + timedelta(days=1)
                midnight = datetime.combine(tomorrow, time.min)
                sleep_seconds = (midnight - now).total_seconds()
                logger.info(
                    f"下一次日程生成任务将在 {sleep_seconds:.2f} 秒后运行 (北京时间 {midnight.strftime('%Y-%m-%d %H:%M:%S')})"
                )
                await asyncio.sleep(sleep_seconds)
                logger.info("到达每日零点，开始生成新的一天日程...")
                await self.schedule_manager._async_generate_and_save_schedule()
            except asyncio.CancelledError:
                logger.info("每日日程生成任务被取消。")
                break
            except Exception as e:
                logger.error(f"每日日程生成任务发生未知错误: {e}")
                await asyncio.sleep(300)


schedule_manager = ScheduleManager()
