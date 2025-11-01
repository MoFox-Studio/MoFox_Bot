"""
用户封禁管理模块

负责用户封禁状态检查、违规记录管理等功能
"""

import datetime

from sqlalchemy import select

from src.common.database.core.models import BanUser, get_db_session
from src.common.logger import get_logger

from ..types import DetectionResult

logger = get_logger("anti_injector.user_ban")


class UserBanManager:
    """用户封禁管理器"""

    def __init__(self, config):
        """初始化封禁管理器

        Args:
            config: 反注入配置对象
        """
        self.config = config

    async def check_user_ban(self, user_id: str, platform: str) -> tuple[bool, str | None, str] | None:
        """检查用户是否被封禁

        Args:
            user_id: 用户ID
            platform: 平台名称

        Returns:
            如果用户被封禁则返回拒绝结果，否则返回None
        """
        try:
            async with get_db_session() as session:
                result = await session.execute(select(BanUser).filter_by(user_id=user_id, platform=platform))
                ban_record = result.scalar_one_or_none()

                if ban_record:
                    # 只有违规次数达到阈值时才算被封禁
                    if ban_record.violation_num >= self.config.auto_ban_violation_threshold:
                        # 检查封禁是否过期
                        ban_duration = datetime.timedelta(hours=self.config.auto_ban_duration_hours)
                        if datetime.datetime.now() - ban_record.created_at < ban_duration:
                            remaining_time = ban_duration - (datetime.datetime.now() - ban_record.created_at)
                            return False, None, f"用户被封禁中，剩余时间: {remaining_time}"
                        else:
                            # 封禁已过期，重置违规次数与时间（模型已使用 Mapped 类型，可直接赋值）
                            ban_record.violation_num = 0
                            ban_record.created_at = datetime.datetime.now()
                            await session.commit()
                            logger.info(f"用户 {platform}:{user_id} 封禁已过期，违规次数已重置")

            return None

        except Exception as e:
            logger.error(f"检查用户封禁状态失败: {e}", exc_info=True)
            return None

    async def record_violation(self, user_id: str, platform: str, detection_result: DetectionResult):
        """记录用户违规行为

        Args:
            user_id: 用户ID
            platform: 平台名称
            detection_result: 检测结果
        """
        try:
            async with get_db_session() as session:
                # 查找或创建违规记录
                result = await session.execute(select(BanUser).filter_by(user_id=user_id, platform=platform))
                ban_record = result.scalar_one_or_none()

                if ban_record:
                    ban_record.violation_num += 1
                    ban_record.reason = f"提示词注入攻击 (置信度: {detection_result.confidence:.2f})"
                else:
                    ban_record = BanUser(
                        platform=platform,
                        user_id=user_id,
                        violation_num=1,
                        reason=f"提示词注入攻击 (置信度: {detection_result.confidence:.2f})",
                        created_at=datetime.datetime.now(),
                    )
                    session.add(ban_record)

                await session.commit()

                if ban_record.violation_num >= self.config.auto_ban_violation_threshold:
                    logger.warning(f"用户 {platform}:{user_id} 违规次数达到 {ban_record.violation_num}，触发自动封禁")
                    # 只有在首次达到阈值时才更新封禁开始时间
                    if ban_record.violation_num == self.config.auto_ban_violation_threshold:
                        ban_record.created_at = datetime.datetime.now()
                    await session.commit()
                else:
                    logger.info(f"用户 {platform}:{user_id} 违规记录已更新，当前违规次数: {ban_record.violation_num}")

        except Exception as e:
            logger.error(f"记录违规行为失败: {e}", exc_info=True)
