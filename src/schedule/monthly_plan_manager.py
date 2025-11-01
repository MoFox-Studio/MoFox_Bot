import asyncio
from datetime import datetime, timedelta

from dateutil.relativedelta import relativedelta

from src.common.logger import get_logger
from src.manager.async_task_manager import AsyncTask, async_task_manager

from . import database
from .plan_manager import PlanManager

logger = get_logger("monthly_plan_manager")


class MonthlyPlanManager:
    """
    负责管理月度计划的生成和维护。
    它主要通过一个后台任务来确保每个月都能自动生成新的计划。
    """

    def __init__(self):
        """
        初始化 MonthlyPlanManager。
        """
        self.plan_manager = PlanManager()  # 核心的计划逻辑处理器
        self.monthly_task_started = False  # 标记每月自动生成任务是否已启动

    async def initialize(self):
        """
        异步初始化月度计划管理器。
        会启动一个每月的后台任务来自动生成计划。
        """
        logger.info("正在初始化月度计划管理器...")

        # 在启动时清理两个月前的旧计划
        two_months_ago = datetime.now() - relativedelta(months=2)
        cleanup_month_str = two_months_ago.strftime("%Y-%m")
        logger.info(f"执行启动时月度计划清理任务，将删除 {cleanup_month_str} 之前的计划...")
        await database.delete_plans_older_than(cleanup_month_str)

        await self.start_monthly_plan_generation()
        logger.info("月度计划管理器初始化成功")

    async def start_monthly_plan_generation(self):
        """
        启动每月一次的月度计划生成后台任务。
        同时，在启动时会立即检查并确保当前月份的计划是存在的。
        """
        if not self.monthly_task_started:
            logger.info(" 正在启动每月月度计划生成任务...")
            task = MonthlyPlanGenerationTask(self)
            await async_task_manager.add_task(task)
            self.monthly_task_started = True
            logger.info(" 每月月度计划生成任务已成功启动。")
            # 在程序启动时，也执行一次检查，确保当前月份的计划存在
            logger.info(" 执行启动时月度计划检查...")
            await self.plan_manager.ensure_and_generate_plans_if_needed()
        else:
            logger.info(" 每月月度计划生成任务已在运行中。")

    async def ensure_and_generate_plans_if_needed(self, target_month: str | None = None) -> bool:
        """
        一个代理方法，调用 PlanManager 中的核心逻辑来确保月度计划的存在。

        Args:
            target_month (str | None): 目标月份，格式 "YYYY-MM"。如果为 None，则使用当前月份。

        Returns:
            bool: 如果生成了新的计划则返回 True，否则返回 False。
        """
        return await self.plan_manager.ensure_and_generate_plans_if_needed(target_month)


class MonthlyPlanGenerationTask(AsyncTask):
    """
    一个周期性的后台任务，在每个月的第一天零点自动触发，用于生成新的月度计划。
    """
    def __init__(self, monthly_plan_manager: MonthlyPlanManager):
        """
        初始化每月计划生成任务。

        Args:
            monthly_plan_manager (MonthlyPlanManager): MonthlyPlanManager 的实例。
        """
        super().__init__(task_name="MonthlyPlanGenerationTask")
        self.monthly_plan_manager = monthly_plan_manager

    async def run(self):
        """
        任务的执行体，无限循环直到被取消。
        计算到下个月第一天零点的时间并休眠，然后在月初触发：
        1. 归档上个月未完成的计划。
        2. 为新月份生成新的计划。
        """
        while True:
            try:
                now = datetime.now()
                # 计算下个月第一天的零点
                if now.month == 12:
                    next_month = datetime(now.year + 1, 1, 1)
                else:
                    next_month = datetime(now.year, now.month + 1, 1)
                
                sleep_seconds = (next_month - now).total_seconds()
                logger.info(
                    f" 下一次月度计划生成任务将在 {sleep_seconds:.2f} 秒后运行 (北京时间 {next_month.strftime('%Y-%m-%d %H:%M:%S')})"
                )
                await asyncio.sleep(sleep_seconds)

                # 到达月初，先归档上个月的计划
                last_month = (next_month - timedelta(days=1)).strftime("%Y-%m")
                await self.monthly_plan_manager.plan_manager.archive_current_month_plans(last_month)
                
                # 为当前月生成新计划
                current_month = next_month.strftime("%Y-%m")
                logger.info(f" 到达月初，开始生成 {current_month} 的月度计划...")
                await self.monthly_plan_manager.plan_manager._generate_monthly_plans_logic(current_month)

            except asyncio.CancelledError:
                logger.info(" 每月月度计划生成任务被取消。")
                break
            except Exception as e:
                logger.error(f" 每月月度计划生成任务发生未知错误: {e}")
                await asyncio.sleep(3600)  # 发生错误时，休眠一小时后重试


# 创建 MonthlyPlanManager 的单例
monthly_plan_manager = MonthlyPlanManager()
