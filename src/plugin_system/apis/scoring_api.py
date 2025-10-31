"""
统一评分系统API
提供系统级的关系分和兴趣管理服务，供所有插件和主项目组件使用
"""

from typing import Any

from src.common.logger import get_logger
from src.plugin_system.services.interest_service import interest_service
from src.plugin_system.services.relationship_service import relationship_service

logger = get_logger("scoring_api")


class ScoringAPI:
    """
    统一评分系统API - 系统级服务

    提供关系分和兴趣管理的统一接口，替代原有的插件依赖方式。
    所有插件和主项目组件都应该通过此API访问评分功能。
    """

    @staticmethod
    async def get_user_relationship_score(user_id: str) -> float:
        """
        获取用户关系分

        Args:
            user_id: 用户ID

        Returns:
            关系分 (0.0 - 1.0)
        """
        return await relationship_service.get_user_relationship_score(user_id)

    @staticmethod
    async def get_user_relationship_data(user_id: str) -> dict:
        """
        获取用户完整关系数据

        Args:
            user_id: 用户ID

        Returns:
            包含关系分、关系文本等的字典
        """
        return await relationship_service.get_user_relationship_data(user_id)

    @staticmethod
    async def update_user_relationship(user_id: str, relationship_score: float, relationship_text: str | None = None, user_name: str | None = None):
        """
        更新用户关系数据

        Args:
            user_id: 用户ID
            relationship_score: 关系分 (0.0 - 1.0)
            relationship_text: 关系描述文本
            user_name: 用户名称
        """
        await relationship_service.update_user_relationship(user_id, relationship_score, relationship_text, user_name)

    @staticmethod
    async def initialize_smart_interests(personality_description: str, personality_id: str = "default"):
        """
        初始化智能兴趣系统

        Args:
            personality_description: 机器人性格描述
            personality_id: 性格ID
        """
        await interest_service.initialize_smart_interests(personality_description, personality_id)

    @staticmethod
    async def calculate_interest_match(content: str, keywords: list[str] | None = None):
        """
        计算内容与兴趣的匹配度

        Args:
            content: 消息内容
            keywords: 关键词列表

        Returns:
            匹配结果
        """
        return await interest_service.calculate_interest_match(content, keywords)

    @staticmethod
    def get_system_stats() -> dict[str, Any]:
        """
        获取系统统计信息

        Returns:
            包含各子系统统计的字典
        """
        return {
            "relationship_service": relationship_service.get_cache_stats(),
            "interest_service": interest_service.get_interest_stats()
        }

    @staticmethod
    def clear_caches(user_id: str | None = None):
        """
        清理缓存

        Args:
            user_id: 特定用户ID，如果为None则清理所有缓存
        """
        relationship_service.clear_cache(user_id)
        logger.info(f"清理缓存: {user_id if user_id else '全部'}")


# 创建全局API实例 - 系统级服务
scoring_api = ScoringAPI()
