"""
定时任务服务
根据日程表定时发送说说。
"""

import asyncio
import datetime
import random
import traceback
from collections.abc import Callable

from sqlalchemy import select

from src.common.database.compatibility import get_db_session
from src.common.database.core.models import MaiZoneScheduleStatus
from src.common.logger import get_logger
from src.config.config import model_config as global_model_config
from src.plugin_system.apis import llm_api
from src.schedule.schedule_manager import schedule_manager

from .qzone_service import QZoneService

logger = get_logger("MaiZone.SchedulerService")


class SchedulerService:
    """
    定时任务管理器，负责根据全局日程表定时触发说说发送任务。
    """

    def __init__(self, get_config: Callable, qzone_service: QZoneService):
        """
        初始化定时任务服务。

        :param get_config: 用于获取插件配置的函数。
        :param qzone_service: QQ空间服务实例，用于执行发送任务。
        """
        self.get_config = get_config
        self.qzone_service = qzone_service
        self.is_running = False
        self.task = None
        self.last_processed_activity = None

    async def start(self):
        """启动定时任务的主循环。"""
        if self.is_running:
            logger.warning("定时任务已在运行中，无需重复启动。")
            return
        self.is_running = True
        self.task = asyncio.create_task(self._schedule_loop())
        logger.info("基于日程表的说说定时发送任务已启动。")

    async def stop(self):
        """停止定时任务的主循环。"""
        if not self.is_running:
            return
        self.is_running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass  # 任务取消是正常操作
        logger.info("基于日程表的说说定时发送任务已停止。")

    async def _generate_random_topic(self) -> str | None:
        """
        使用小模型生成一个随机的说说主题。
        """
        try:
            logger.info("尝试生成随机说说主题...")
            prompt = "请生成一个有趣、简短、积极向上的日常一句话，适合作为社交媒体的动态内容，例如关于天气、心情、动漫、游戏或者某个小发现。请直接返回这句话，不要包含任何多余的解释或标签。"

            task_config = global_model_config.model_task_config.get_task("utils_small")
            if not task_config:
                logger.error("未找到名为 'utils_small' 的模型任务配置。")
                return None

            success, content, _, _ = await llm_api.generate_with_model(
                model_config=task_config,
                prompt=prompt,
                max_tokens=150,
                temperature=0.9,
            )

            if success and content and content.strip():
                logger.info(f"成功生成随机主题: {content.strip()}")
                return content.strip()
            logger.warning("LLM未能生成有效的主题。")
            return None
        except Exception as e:
            logger.error(f"生成随机主题时发生错误: {e}")
            return None

    async def _schedule_loop(self):
        """
        定时任务的核心循环。
        每隔一段时间检查当前是否有日程活动，并判断是否需要触发发送流程。
        也支持在没有日程时，根据配置进行不定时发送。
        """
        while self.is_running:
            try:
                # 1. 检查定时任务总开关是否开启
                if not self.get_config("schedule.enable_schedule", False):
                    await asyncio.sleep(60)  # 如果被禁用，则每分钟检查一次状态
                    continue

                now = datetime.datetime.now()
                hour_str = now.strftime("%Y-%m-%d %H")

                # 2. 检查是否在禁止发送的时间段内
                forbidden_start = self.get_config("schedule.forbidden_hours_start", 2)
                forbidden_end = self.get_config("schedule.forbidden_hours_end", 6)
                is_forbidden_time = (
                    (forbidden_start < forbidden_end and forbidden_start <= now.hour < forbidden_end)
                    or (forbidden_start > forbidden_end and (now.hour >= forbidden_start or now.hour < forbidden_end))
                )

                if is_forbidden_time:
                    logger.info(f"当前时间 {now.hour}点 处于禁止发送时段 ({forbidden_start}-{forbidden_end})，本次跳过。")
                else:
                    # 3. 获取当前时间的日程活动
                    current_activity_dict = schedule_manager.get_current_activity()
                    logger.info(f"当前检测到的日程活动: {current_activity_dict}")

                    if current_activity_dict:
                        # --- 有日程活动时的逻辑 ---
                        current_activity_name = current_activity_dict.get("activity", str(current_activity_dict))
                        if current_activity_dict != self.last_processed_activity:
                            logger.info(f"检测到新的日程活动: '{current_activity_name}'，准备发送说说。")
                            result = await self.qzone_service.send_feed_from_activity(current_activity_name)
                            await self._mark_as_processed(
                                hour_str, current_activity_name, result.get("success", False), result.get("message", "")
                            )
                            self.last_processed_activity = current_activity_dict
                        else:
                            logger.info(f"活动 '{current_activity_name}' 与上次相同，本次跳过。")
                    else:
                        # --- 没有日程活动时的逻辑 ---
                            activity_placeholder = "No Schedule - Random"
                            if not await self._is_processed(hour_str, activity_placeholder):
                                logger.info("没有日程活动，但开启了无日程发送功能，准备生成随机主题。")
                                topic = await self._generate_random_topic()
                                if topic:
                                    result = await self.qzone_service.send_feed(topic=topic, stream_id=None)
                                    await self._mark_as_processed(
                                        hour_str,
                                        activity_placeholder,
                                        result.get("success", False),
                                        result.get("message", ""),
                                    )
                                else:
                                    logger.error("未能生成随机主题，本次不发送。")
                                    # 即使生成失败，也标记为已处理，防止本小时内反复尝试
                                    await self._mark_as_processed(
                                        hour_str, activity_placeholder, False, "Failed to generate topic"
                                    )
                            else:
                                logger.info(f"当前小时 {hour_str} 已执行过无日程发送任务，本次跳过。")

                # 4. 计算并等待一个随机的时间间隔
                min_minutes = self.get_config("schedule.random_interval_min_minutes", 15)
                max_minutes = self.get_config("schedule.random_interval_max_minutes", 45)
                wait_seconds = random.randint(min_minutes * 60, max_minutes * 60)
                logger.info(f"下一次检查将在 {wait_seconds / 60:.2f} 分钟后进行。")
                await asyncio.sleep(wait_seconds)

            except asyncio.CancelledError:
                logger.info("定时任务循环被取消。")
                break
            except Exception as e:
                logger.error(f"定时任务循环中发生未知错误: {e}\n{traceback.format_exc()}")
                await asyncio.sleep(300)  # 发生错误后，等待一段时间再重试

    async def _is_processed(self, hour_str: str, activity: str) -> bool:
        """
        检查指定的任务（某个小时的某个活动）是否已经被成功处理过。
        """
        try:
            async with get_db_session() as session:
                stmt = select(MaiZoneScheduleStatus).where(
                    MaiZoneScheduleStatus.datetime_hour == hour_str,
                    MaiZoneScheduleStatus.is_processed == True,  # noqa: E712
                )
                result = await session.execute(stmt)
                record = result.scalar_one_or_none()
                return record is not None
        except Exception as e:
            logger.error(f"检查日程处理状态时发生数据库错误: {e}")
            return False  # 数据库异常时，默认为未处理，允许重试

    async def _mark_as_processed(self, hour_str: str, activity: str, success: bool, content: str):
        """
        将任务的处理状态和结果写入数据库。
        """
        try:
            async with get_db_session() as session:
                # 查找是否已存在该记录
                stmt = select(MaiZoneScheduleStatus).where(MaiZoneScheduleStatus.datetime_hour == hour_str)
                result = await session.execute(stmt)
                record = result.scalar_one_or_none()

                if record:
                    # 如果存在，则更新状态
                    record.is_processed = True  # type: ignore
                    record.processed_at = datetime.datetime.now()  # type: ignore
                    record.send_success = success  # type: ignore
                    record.story_content = content  # type: ignore
                else:
                    # 如果不存在，则创建新记录
                    # 如果activity是字典，只提取activity字段
                    activity_str = activity.get("activity", str(activity)) if isinstance(activity, dict) else str(activity)
                    new_record = MaiZoneScheduleStatus(
                        datetime_hour=hour_str,
                        activity=activity_str,
                        is_processed=True,
                        processed_at=datetime.datetime.now(),
                        story_content=content,
                        send_success=success,
                    )
                    session.add(new_record)
                await session.commit()
                logger.info(f"已更新日程处理状态: {hour_str} - {activity} - 成功: {success}")
        except Exception as e:
            logger.error(f"更新日程处理状态时发生数据库错误: {e}")
