"""
用户画像更新工具

直接更新用户画像信息，包括别名、主观印象、偏好关键词和好感分数
现在依赖工具调用历史记录，LLM可以看到之前的调用结果，因此直接覆盖更新即可
"""

import time
from typing import Any

from sqlalchemy import select

from src.common.database.compatibility import get_db_session
from src.common.database.core.models import UserRelationships
from src.common.logger import get_logger
from src.config.config import global_config
from src.plugin_system import BaseTool, ToolParamType

logger = get_logger("user_profile_tool")


class UserProfileTool(BaseTool):
    """用户画像更新工具

    直接使用LLM传入的参数更新用户画像。
    由于工具执行器现在支持历史记录，LLM可以看到之前的调用结果，因此无需再次调用LLM进行合并。
    """

    name = "update_user_profile"
    description = "当你通过聊天记录对某个用户产生了新的认识或印象时使用此工具，更新该用户的画像信息。包括：用户别名、你对TA的主观印象、TA的偏好兴趣、你对TA的好感程度。调用时机：当你发现用户透露了新的个人信息、展现了性格特点、表达了兴趣偏好，或者你们的互动让你对TA的看法发生变化时。"
    parameters = [
        ("target_user_id", ToolParamType.STRING, "目标用户的ID（必须）", True, None),
        ("user_aliases", ToolParamType.STRING, "该用户的昵称或别名，如果发现用户自称或被他人称呼的其他名字时填写，多个别名用逗号分隔（可选）", False, None),
        ("impression_description", ToolParamType.STRING, "你对该用户的整体印象和性格感受，例如'这个用户很幽默开朗'、'TA对技术很有热情'等。当你通过对话了解到用户的性格、态度、行为特点时填写（可选）", False, None),
        ("preference_keywords", ToolParamType.STRING, "该用户表现出的兴趣爱好或偏好，如'编程,游戏,动漫'。当用户谈论自己喜欢的事物时填写，多个关键词用逗号分隔（可选）", False, None),
        ("affection_score", ToolParamType.FLOAT, "你对该用户的好感程度，0.0(陌生/不喜欢)到1.0(很喜欢/好友)。当你们的互动让你对TA的感觉发生变化时更新（可选）", False, None),
    ]
    available_for_llm = True
    history_ttl = 5

    async def execute(self, function_args: dict[str, Any]) -> dict[str, Any]:
        """执行用户画像更新

        Args:
            function_args: 工具参数

        Returns:
            dict: 执行结果
        """
        try:
            # 提取参数
            target_user_id = function_args.get("target_user_id")
            if not target_user_id:
                return {
                    "type": "error",
                    "id": "user_profile_update",
                    "content": "错误：必须提供目标用户ID"
                }

            # 从LLM传入的参数
            new_aliases = function_args.get("user_aliases", "")
            new_impression = function_args.get("impression_description", "")
            new_keywords = function_args.get("preference_keywords", "")
            new_score = function_args.get("affection_score")

            # 从数据库获取现有用户画像（用于返回信息）
            existing_profile = await self._get_user_profile(target_user_id)

            # 如果LLM没有传入任何有效参数，返回提示
            if not any([new_aliases, new_impression, new_keywords, new_score is not None]):
                return {
                    "type": "info",
                    "id": target_user_id,
                    "content": "提示：需要提供至少一项更新内容（别名、印象描述、偏好关键词或好感分数）"
                }

            # 直接使用LLM传入的值进行覆盖更新（保留未更新的字段）
            final_profile = {
                "user_aliases": new_aliases if new_aliases else existing_profile.get("user_aliases", ""),
                "relationship_text": new_impression if new_impression else existing_profile.get("relationship_text", ""),
                "preference_keywords": new_keywords if new_keywords else existing_profile.get("preference_keywords", ""),
                "relationship_score": new_score if new_score is not None else existing_profile.get("relationship_score", global_config.affinity_flow.base_relationship_score),
            }

            # 确保分数在有效范围内
            final_profile["relationship_score"] = max(0.0, min(1.0, float(final_profile["relationship_score"])))

            # 更新数据库
            await self._update_user_profile_in_db(target_user_id, final_profile)

            # 构建返回信息
            updates = []
            if final_profile.get("user_aliases"):
                updates.append(f"别名: {final_profile['user_aliases']}")
            if final_profile.get("relationship_text"):
                updates.append(f"印象: {final_profile['relationship_text'][:50]}...")
            if final_profile.get("preference_keywords"):
                updates.append(f"偏好: {final_profile['preference_keywords']}")
            if final_profile.get("relationship_score") is not None:
                updates.append(f"好感分: {final_profile['relationship_score']:.2f}")

            result_text = f"已更新用户 {target_user_id} 的画像：\n" + "\n".join(updates)
            logger.info(f"用户画像更新成功: {target_user_id}")

            return {
                "type": "user_profile_update",
                "id": target_user_id,
                "content": result_text
            }

        except Exception as e:
            logger.error(f"用户画像更新失败: {e}", exc_info=True)
            return {
                "type": "error",
                "id": function_args.get("target_user_id", "unknown"),
                "content": f"用户画像更新失败: {e!s}"
            }

    async def _get_user_profile(self, user_id: str) -> dict[str, Any]:
        """从数据库获取用户现有画像

        Args:
            user_id: 用户ID

        Returns:
            dict: 用户画像数据
        """
        try:
            async with get_db_session() as session:
                stmt = select(UserRelationships).where(UserRelationships.user_id == user_id)
                result = await session.execute(stmt)
                profile = result.scalar_one_or_none()

                if profile:
                    return {
                        "user_name": profile.user_name or user_id,
                        "user_aliases": profile.user_aliases or "",
                        "relationship_text": profile.relationship_text or "",
                        "preference_keywords": profile.preference_keywords or "",
                        "relationship_score": float(profile.relationship_score) if profile.relationship_score is not None else global_config.affinity_flow.base_relationship_score,
                    }
                else:
                    # 用户不存在，返回默认值
                    return {
                        "user_name": user_id,
                        "user_aliases": "",
                        "relationship_text": "",
                        "preference_keywords": "",
                        "relationship_score": global_config.affinity_flow.base_relationship_score,
                    }
        except Exception as e:
            logger.error(f"获取用户画像失败: {e}")
            return {
                "user_name": user_id,
                "user_aliases": "",
                "relationship_text": "",
                "preference_keywords": "",
                "relationship_score": global_config.affinity_flow.base_relationship_score,
            }



    async def _update_user_profile_in_db(self, user_id: str, profile: dict[str, Any]):
        """更新数据库中的用户画像

        Args:
            user_id: 用户ID
            profile: 画像数据
        """
        try:
            current_time = time.time()

            async with get_db_session() as session:
                stmt = select(UserRelationships).where(UserRelationships.user_id == user_id)
                result = await session.execute(stmt)
                existing = result.scalar_one_or_none()

                if existing:
                    # 更新现有记录
                    existing.user_aliases = profile.get("user_aliases", "")
                    existing.relationship_text = profile.get("relationship_text", "")
                    existing.preference_keywords = profile.get("preference_keywords", "")
                    existing.relationship_score = profile.get("relationship_score", global_config.affinity_flow.base_relationship_score)
                    existing.last_updated = current_time
                else:
                    # 创建新记录
                    new_profile = UserRelationships(
                        user_id=user_id,
                        user_name=user_id,
                        user_aliases=profile.get("user_aliases", ""),
                        relationship_text=profile.get("relationship_text", ""),
                        preference_keywords=profile.get("preference_keywords", ""),
                        relationship_score=profile.get("relationship_score", global_config.affinity_flow.base_relationship_score),
                        last_updated=current_time
                    )
                    session.add(new_profile)

                await session.commit()
                logger.info(f"用户画像已更新到数据库: {user_id}")

        except Exception as e:
            logger.error(f"更新用户画像到数据库失败: {e}", exc_info=True)
            raise


