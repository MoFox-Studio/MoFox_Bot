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

    async def _schedule_loop(self):
        """
        定时任务的核心循环。
        每隔一段时间检查当前是否有日程活动，并判断是否需要触发发送流程。
        """
        while self.is_running:
            try:
                # 1. 检查定时任务总开关是否开启
                if not self.get_config("schedule.enable_schedule", False):
                    await asyncio.sleep(60)  # 如果被禁用，则每分钟检查一次状态
                    continue

                # 2. 获取当前时间的日程活动
                current_activity = schedule_manager.get_current_activity()
                logger.info(f"当前检测到的日程活动: {current_activity}")

                if current_activity:
                    # 3. 检查当前时间是否在禁止发送的时间段内
                    now = datetime.datetime.now()
                    forbidden_start = self.get_config("schedule.forbidden_hours_start", 2)
                    forbidden_end = self.get_config("schedule.forbidden_hours_end", 6)

                    is_forbidden_time = False
                    if forbidden_start < forbidden_end:
                        # 例如，2点到6点
                        is_forbidden_time = forbidden_start <= now.hour < forbidden_end
                    else:
                        # 例如，23点到第二天7点
                        is_forbidden_time = now.hour >= forbidden_start or now.hour < forbidden_end

                    if is_forbidden_time:
                        logger.info(
                            f"当前时间 {now.hour}点 处于禁止发送时段 ({forbidden_start}-{forbidden_end})，本次跳过。"
                        )
                        self.last_processed_activity = current_activity

                    # 4. 检查活动是否是新的活动
                    elif current_activity != self.last_processed_activity:
                        logger.info(f"检测到新的日程活动: '{current_activity}'，准备发送说说。")

                        # 5. 调用QZoneService执行完整的发送流程
                        result = await self.qzone_service.send_feed_from_activity(current_activity)

                        # 6. 将处理结果记录到数据库
                        now = datetime.datetime.now()
                        hour_str = now.strftime("%Y-%m-%d %H")
                        await self._mark_as_processed(
                            hour_str, current_activity, result.get("success", False), result.get("message", "")
                        )

                        # 7. 更新上一个处理的活动
                        self.last_processed_activity = current_activity
                    else:
                        logger.info(f"活动 '{current_activity}' 与上次相同，本次跳过。")

                # 8. 计算并等待一个随机的时间间隔
                min_minutes = self.get_config("schedule.random_interval_min_minutes", 5)
                max_minutes = self.get_config("schedule.random_interval_max_minutes", 15)
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

        :param hour_str: 时间字符串，格式为 "YYYY-MM-DD HH"。
        :param activity: 活动名称。
        :return: 如果已处理过，返回 True，否则返回 False。
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

        :param hour_str: 时间字符串。
        :param activity: 活动名称。
        :param success: 发送是否成功。
        :param content: 最终发送的说说内容或错误信息。
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
