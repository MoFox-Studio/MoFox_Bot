"""个人信息API模块

提供个人信息查询功能，用于插件获取用户相关信息
使用方式：
    from src.plugin_system.apis import person_api
    person_id = person_api.get_person_id("qq", 123456)
    info = await person_api.get_person_info(person_id)
"""

import asyncio
from typing import Any

from src.common.logger import get_logger
from src.person_info.person_info import PersonInfoManager, get_person_info_manager
from src.plugin_system.services.interest_service import interest_service
from src.plugin_system.services.relationship_service import relationship_service

logger = get_logger("person_api")


# =============================================================================
# 辅助函数
# =============================================================================


def get_person_id(platform: str, user_id: int | str) -> str:
    """根据平台和用户ID获取person_id (同步)

    这是一个核心的辅助函数，用于生成统一的用户标识。
    """
    try:
        return PersonInfoManager.get_person_id(platform, user_id)
    except Exception as e:
        logger.error(f"[PersonAPI] 获取person_id失败: platform={platform}, user_id={user_id}, error={e}")
        return ""


async def get_person_id_by_name(person_name: str) -> str:
    """根据用户名获取person_id"""
    try:
        person_info_manager = get_person_info_manager()
        return await person_info_manager.get_person_id_by_person_name(person_name)
    except Exception as e:
        logger.error(f"[PersonAPI] 根据用户名获取person_id失败: person_name={person_name}, error={e}")
        return ""


# =============================================================================
# 核心信息查询API
# =============================================================================


async def is_person_known(platform: str, user_id: int) -> bool:
    """判断是否认识某个用户"""
    try:
        person_info_manager = get_person_info_manager()
        return await person_info_manager.is_person_known(platform, user_id)
    except Exception as e:
        logger.error(f"[PersonAPI] 检查用户是否已知失败: platform={platform}, user_id={user_id}, error={e}")
        return False


async def get_person_info(person_id: str) -> dict[str, Any]:
    """获取用户的核心基础信息

    返回一个包含用户基础信息的字典，例如 person_name, nickname, know_times, attitude 等。
    """
    if not person_id:
        return {}
    try:
        person_info_manager = get_person_info_manager()
        fields = ["person_name", "nickname", "know_times", "know_since", "last_know", "attitude"]
        values = await person_info_manager.get_values(person_id, fields)
        return values
    except Exception as e:
        logger.error(f"[PersonAPI] 获取用户信息失败: person_id={person_id}, error={e}")
        return {}


async def get_person_impression(person_id: str, short: bool = False) -> str:
    """获取对用户的印象

    Args:
        person_id: 用户的唯一标识ID
        short: 是否获取简短版印象，默认为False

    Returns:
        一段描述性的文本。
    """
    if not person_id:
        return "用户ID为空，无法获取印象。"
    try:
        person_info_manager = get_person_info_manager()
        field = "short_impression" if short else "impression"
        impression = await person_info_manager.get_value(person_id, field)
        return impression or "还没有形成对该用户的印象。"
    except Exception as e:
        logger.error(f"[PersonAPI] 获取用户印象失败: person_id={person_id}, error={e}")
        return "获取用户印象时发生错误。"


async def get_person_points(person_id: str, limit: int = 5) -> list[tuple]:
    """获取关于用户的'记忆点'

    Args:
        person_id: 用户的唯一标识ID
        limit: 返回的记忆点数量上限，默认为5

    Returns:
        一个列表，每个元素是一个包含记忆点内容、权重和时间的元组。
    """
    if not person_id:
        return []
    try:
        person_info_manager = get_person_info_manager()
        points = await person_info_manager.get_value(person_id, "points")
        if not points:
            return []

        # 按权重和时间排序，返回最重要的几个点
        sorted_points = sorted(points, key=lambda x: (x[1], x[2]), reverse=True)
        return sorted_points[:limit]
    except Exception as e:
        logger.error(f"[PersonAPI] 获取用户记忆点失败: person_id={person_id}, error={e}")
        return []


# =============================================================================
# 关系查询API
# =============================================================================


async def get_user_relationship_score(user_id: str) -> float:
    """
    获取用户关系分

    Args:
        user_id: 用户ID

    Returns:
        关系分 (0.0 - 1.0)
    """
    return await relationship_service.get_user_relationship_score(user_id)


async def get_user_relationship_data(user_id: str) -> dict:
    """
    获取用户完整关系数据

    Args:
        user_id: 用户ID

    Returns:
        包含关系分、关系文本等的字典
    """
    return await relationship_service.get_user_relationship_data(user_id)


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


# =============================================================================
# 兴趣系统API
# =============================================================================


async def initialize_smart_interests(personality_description: str, personality_id: str = "default"):
    """
    初始化智能兴趣系统

    Args:
        personality_description: 机器人性格描述
        personality_id: 性格ID
    """
    await interest_service.initialize_smart_interests(personality_description, personality_id)


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


# =============================================================================
# 系统状态与缓存API
# =============================================================================


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


def clear_caches(user_id: str | None = None):
    """
    清理缓存

    Args:
        user_id: 特定用户ID，如果为None则清理所有缓存
    """
    relationship_service.clear_cache(user_id)
    logger.info(f"清理缓存: {user_id if user_id else '全部'}")


# =============================================================================
# 报告API
# =============================================================================


async def get_full_relationship_report(person_id: str) -> str:
    """生成一份关于你和用户的完整'关系报告'

    综合基础信息、印象、记忆点和关系分，提供一个全方位的关系概览。
    """
    if not person_id:
        return "无法生成报告，因为用户ID为空。"

    try:
        person_info_manager = get_person_info_manager()
        user_id = await person_info_manager.get_value(person_id, "user_id")

        if not user_id:
            return "无法生成报告，因为找不到对应的用户信息。"

        # 异步获取所有需要的信息
        info, impression, points, rel_data = await asyncio.gather(
            get_person_info(person_id),
            get_person_impression(person_id),
            get_person_points(person_id, limit=3),
            relationship_service.get_user_relationship_data(str(user_id)),
        )

        # 构建报告
        report = f"--- 与 {info.get('person_name', '未知用户')} 的关系报告 ---\n"
        report += f"昵称: {info.get('nickname', '未知')}\n"
        report += f"关系分数: {rel_data.get('relationship_score', 0.0):.2f}/1.0\n"
        report += f"关系描述: {rel_data.get('relationship_text', '暂无')}\n"
        report += f"我对ta的印象: {impression}\n"

        if points:
            report += "最近的重要记忆点:\n"
            for point in points:
                report += f"  - {point[0]} (重要性: {point[1]})\n"

        report += "----------------------------------------\n"
        return report

    except Exception as e:
        logger.error(f"[PersonAPI] 生成关系报告失败: person_id={person_id}, error={e}")
        return "生成关系报告时发生错误。"
