# mmc/src/schedule/llm_generator.py

import asyncio
from datetime import datetime
from typing import Any

import orjson
from json_repair import repair_json
from lunar_python import Lunar

from src.chat.utils.prompt import global_prompt_manager
from src.common.database.core.models import MonthlyPlan
from src.common.logger import get_logger
from src.config.config import global_config, model_config
from src.llm_models.utils_model import LLMRequest

from .prompts import DEFAULT_MONTHLY_PLAN_GUIDELINES, DEFAULT_SCHEDULE_GUIDELINES
from .schemas import ScheduleData

logger = get_logger("schedule_llm_generator")


class ScheduleLLMGenerator:
    """
    使用大型语言模型（LLM）生成每日日程。
    """
    def __init__(self):
        """
        初始化 ScheduleLLMGenerator。
        """
        # 根据配置初始化 LLM 请求处理器
        self.llm = LLMRequest(model_set=model_config.model_task_config.schedule_generator, request_type="schedule")

    async def generate_schedule_with_llm(self, sampled_plans: list[MonthlyPlan]) -> list[dict[str, Any]] | None:
        """
        调用 LLM 生成当天的日程安排。

        Args:
            sampled_plans (list[MonthlyPlan]]): 从月度计划中抽取的参考计划列表。

        Returns:
            list[dict[str, Any]] | None: 成功生成并验证后的日程数据，或在失败时返回 None。
        """
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        weekday = now.strftime("%A")

        # 使用 lunar_python 库获取农历和节日信息
        lunar = Lunar.fromDate(now)
        festivals = lunar.getFestivals()
        other_festivals = lunar.getOtherFestivals()
        all_festivals = festivals + other_festivals

        # 构建节日信息提示块
        festival_block = ""
        if all_festivals:
            festival_text = "、".join(all_festivals)
            festival_block = f"**今天也是一个特殊的日子: {festival_text}！请在日程中考虑和庆祝这个节日。**"

        # 构建月度计划参考提示块
        monthly_plans_block = ""
        if sampled_plans:
            plan_texts = "\n".join([f"- {plan.plan_text}" for plan in sampled_plans])
            monthly_plans_block = f"""
**我这个月的一些小目标/计划 (请在今天的日程中适当体现)**:
{plan_texts}
"""

        guidelines = global_config.planning_system.schedule_guidelines or DEFAULT_SCHEDULE_GUIDELINES

        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                logger.info(f"正在生成日程 (第 {attempt}/{max_retries} 次尝试)")

                failure_hint = ""
                if attempt > 1:
                    failure_hint = f"""
**重要提醒 (第{attempt}次尝试)**:
- 前面{attempt - 1}次生成都失败了，请务必严格按照要求生成完整的24小时日程
- 确保JSON格式正确，所有时间段连续覆盖24小时
- 时间格式必须为HH:MM-HH:MM，不能有时间间隙或重叠
- 不要输出任何解释文字，只输出纯JSON数组
- 确保输出完整，不要被截断
"""
                prompt = await global_prompt_manager.format_prompt(
                    "schedule_generation",
                    bot_nickname=global_config.bot.nickname,
                    today_str=today_str,
                    weekday=weekday,
                    festival_block=festival_block,
                    personality=global_config.personality.personality_core,
                    personality_side=global_config.personality.personality_side,
                    monthly_plans_block=monthly_plans_block,
                    guidelines=guidelines,
                    failure_hint=failure_hint,
                )

                response, _ = await self.llm.generate_response_async(prompt)
                # 使用 json_repair 修复可能不规范的 JSON 字符串
                schedule_data = orjson.loads(repair_json(response))

                # 使用 Pydantic 模型验证修复后的 JSON 数据
                if self._validate_schedule_with_pydantic(schedule_data):
                    return schedule_data
                else:
                    logger.warning(f"第 {attempt} 次生成的日程验证失败，继续重试...")

            except Exception as e:
                logger.error(f"第 {attempt} 次生成日程失败: {e}")

            if attempt < max_retries:
                logger.info("2秒后继续重试...")
                await asyncio.sleep(2)

        logger.error("所有尝试都失败，无法生成日程，将会在下次启动时自动重试")
        return None

    @staticmethod
    def _validate_schedule_with_pydantic(schedule_data) -> bool:
        """
        使用 Pydantic 模型验证日程数据的格式和内容。

        Args:
            schedule_data: 从 LLM 返回并解析后的日程数据。

        Returns:
            bool: 验证通过返回 True，否则返回 False。
        """
        try:
            ScheduleData(schedule=schedule_data)
            logger.info("日程数据Pydantic验证通过")
            return True
        except Exception as e:
            logger.warning(f"日程数据Pydantic验证失败: {e}")
            return False


class MonthlyPlanLLMGenerator:
    """
    使用大型语言模型（LLM）生成月度计划。
    """
    def __init__(self):
        """
        初始化 MonthlyPlanLLMGenerator。
        """
        # 根据配置初始化 LLM 请求处理器
        self.llm = LLMRequest(model_set=model_config.model_task_config.schedule_generator, request_type="monthly_plan")

    async def generate_plans_with_llm(self, target_month: str, archived_plans: list[MonthlyPlan]) -> list[str]:
        """
        调用 LLM 生成指定月份的计划列表。

        Args:
            target_month (str): 目标月份，格式 "YYYY-MM"。
            archived_plans (list[MonthlyPlan]]): 上个月归档的未完成计划，作为参考。

        Returns:
            list[str]: 成功生成并解析后的计划字符串列表。
        """
        guidelines = global_config.planning_system.monthly_plan_guidelines or DEFAULT_MONTHLY_PLAN_GUIDELINES
        max_plans = global_config.planning_system.max_plans_per_month

        archived_plans_block = ""
        if archived_plans:
            archived_texts = [f"- {plan.plan_text}" for plan in archived_plans[:5]]
            archived_plans_block = f"""
**上个月未完成的一些计划（可作为参考）**:
{chr(10).join(archived_texts)}

你可以考虑是否要在这个月继续推进这些计划，或者制定全新的计划。
"""

        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                logger.info(f" 正在生成月度计划 (第 {attempt} 次尝试)")
                prompt = await global_prompt_manager.format_prompt(
                    "monthly_plan_generation",
                    bot_nickname=global_config.bot.nickname,
                    target_month=target_month,
                    personality=global_config.personality.personality_core,
                    personality_side=global_config.personality.personality_side,
                    archived_plans_block=archived_plans_block,
                    guidelines=guidelines,
                    max_plans=max_plans,
                )
                response, _ = await self.llm.generate_response_async(prompt)
                # 解析返回的纯文本响应
                plans = self._parse_plans_response(response)
                if plans:
                    logger.info(f"成功生成 {len(plans)} 条月度计划")
                    return plans
                else:
                    logger.warning(f"第 {attempt} 次生成的计划为空，继续重试...")
            except Exception as e:
                logger.error(f"第 {attempt} 次生成月度计划失败: {e}")

            if attempt < max_retries:
                await asyncio.sleep(2)

        logger.error(" 所有尝试都失败，无法生成月度计划")
        return []

    @staticmethod
    def _parse_plans_response(response: str) -> list[str]:
        """
        解析 LLM 返回的纯文本月度计划响应。

        Args:
            response (str): LLM 返回的原始字符串。

        Returns:
            list[str]: 清理和解析后的计划列表。
        """
        try:
            response = response.strip()
            # 按行分割，并去除空行
            lines = [line.strip() for line in response.split("\n") if line.strip()]
            plans = []
            for line in lines:
                # 过滤掉一些可能的 Markdown 标记或解释性文字
                if any(marker in line for marker in ["**", "##", "```", "---", "===", "###"]):
                    continue
                # 去除行首的数字、点、短横线等列表标记
                line = line.lstrip("0123456789.- ")
                # 过滤掉一些明显不是计划的句子
                if len(line) > 5 and not line.startswith(("请", "以上", "总结", "注意")):
                    plans.append(line)

            # 根据配置限制最大计划数量
            max_plans = global_config.planning_system.max_plans_per_month
            if len(plans) > max_plans:
                plans = plans[:max_plans]
            return plans
        except Exception as e:
            logger.error(f"解析月度计划响应时发生错误: {e}")
            return []
