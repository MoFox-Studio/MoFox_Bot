# mmc/src/schedule/plan_manager.py

from datetime import datetime

from src.common.logger import get_logger
from src.config.config import global_config

from .database import (
    add_new_plans,
    archive_active_plans_for_month,
    delete_plans_by_ids,
    get_active_plans_for_month,
    get_archived_plans_for_month,
    get_smart_plans_for_daily_schedule,
    has_active_plans,
)
from .llm_generator import MonthlyPlanLLMGenerator

logger = get_logger("plan_manager")


class PlanManager:
    def __init__(self):
        self.llm_generator = MonthlyPlanLLMGenerator()
        self.generation_running = False

    async def ensure_and_generate_plans_if_needed(self, target_month: str | None = None) -> bool:
        if target_month is None:
            target_month = datetime.now().strftime("%Y-%m")

        if not await has_active_plans(target_month):
            logger.info(f" {target_month} 没有任何有效的月度计划，将触发同步生成。")
            generation_successful = await self._generate_monthly_plans_logic(target_month)
            return generation_successful
        else:
            logger.info(f"{target_month} 已存在有效的月度计划。")
            plans = await get_active_plans_for_month(target_month)
            max_plans = global_config.planning_system.max_plans_per_month
            if len(plans) > max_plans:
                logger.warning(f"当前月度计划数量 ({len(plans)}) 超出上限 ({max_plans})，将自动删除多余的计划。")
                plans_to_delete = plans[: len(plans) - max_plans]
                delete_ids = [p.id for p in plans_to_delete]
                await delete_plans_by_ids(delete_ids)  # type: ignore
                plans = await get_active_plans_for_month(target_month)

            return True

    async def _generate_monthly_plans_logic(self, target_month: str | None = None) -> bool:
        if self.generation_running:
            logger.info("月度计划生成任务已在运行中，跳过重复启动")
            return False

        self.generation_running = True
        try:
            if target_month is None:
                target_month = datetime.now().strftime("%Y-%m")

            logger.info(f"开始为 {target_month} 生成月度计划...")
            if not global_config.planning_system.monthly_plan_enable:
                logger.info(" 月度计划系统已禁用，跳过计划生成。")
                return False

            last_month = self._get_previous_month(target_month)
            archived_plans = await get_archived_plans_for_month(last_month)
            plans = await self.llm_generator.generate_plans_with_llm(target_month, archived_plans)

            if plans:
                await add_new_plans(plans, target_month)
                logger.info(f"成功为 {target_month} 生成并保存了 {len(plans)} 条月度计划。")
                return True
            else:
                logger.warning(f"未能为 {target_month} 生成有效的月度计划。")
                return False
        except Exception as e:
            logger.error(f" 生成 {target_month} 月度计划时发生错误: {e}")
            return False
        finally:
            self.generation_running = False

    def _get_previous_month(self, current_month: str) -> str:
        try:
            year, month = map(int, current_month.split("-"))
            if month == 1:
                return f"{year - 1}-12"
            else:
                return f"{year}-{month - 1:02d}"
        except Exception:
            return "1900-01"

    async def archive_current_month_plans(self, target_month: str | None = None):
        try:
            if target_month is None:
                target_month = datetime.now().strftime("%Y-%m")
            logger.info(f" 开始归档 {target_month} 的活跃月度计划...")
            archived_count = await archive_active_plans_for_month(target_month)
            logger.info(f" 成功归档了 {archived_count} 条 {target_month} 的月度计划。")
        except Exception as e:
            logger.error(f" 归档 {target_month} 月度计划时发生错误: {e}")

    async def get_plans_for_schedule(self, month: str, max_count: int) -> list:
        avoid_days = global_config.planning_system.avoid_repetition_days
        return await get_smart_plans_for_daily_schedule(month, max_count=max_count, avoid_days=avoid_days)
