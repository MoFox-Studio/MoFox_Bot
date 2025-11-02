"""
用户画像更新工具

通过LLM二步调用机制更新用户画像信息，包括别名、主观印象、偏好关键词和好感分数
"""

import time
from typing import Any, ClassVar

import orjson
from sqlalchemy import select

from src.common.database.compatibility import get_db_session
from src.common.database.core.models import UserRelationships
from src.common.logger import get_logger
from src.config.config import global_config, model_config
from src.llm_models.utils_model import LLMRequest
from src.plugin_system import BaseTool, ToolParamType
from src.utils.json_parser import extract_and_parse_json

logger = get_logger("user_profile_tool")


class UserProfileTool(BaseTool):
    """用户画像更新工具

    使用二步调用机制：
    1. LLM决定是否调用工具并传入初步参数
    2. 工具内部调用LLM，结合现有数据和传入参数，决定最终更新内容
    """

    name = "update_user_profile"
    description = "当你通过聊天记录对某个用户产生了新的认识或印象时使用此工具，更新该用户的画像信息。包括：用户别名、你对TA的主观印象、TA的偏好兴趣、你对TA的好感程度。调用时机：当你发现用户透露了新的个人信息、展现了性格特点、表达了兴趣偏好，或者你们的互动让你对TA的看法发生变化时。"
    parameters: ClassVar = [
        ("target_user_id", ToolParamType.STRING, "目标用户的ID（必须）", True, None),
        ("user_aliases", ToolParamType.STRING, "该用户的昵称或别名，如果发现用户自称或被他人称呼的其他名字时填写，多个别名用逗号分隔（可选）", False, None),
        ("impression_description", ToolParamType.STRING, "你对该用户的整体印象和性格感受，例如'这个用户很幽默开朗'、'TA对技术很有热情'等。当你通过对话了解到用户的性格、态度、行为特点时填写（可选）", False, None),
        ("preference_keywords", ToolParamType.STRING, "该用户表现出的兴趣爱好或偏好，如'编程,游戏,动漫'。当用户谈论自己喜欢的事物时填写，多个关键词用逗号分隔（可选）", False, None),
        ("affection_score", ToolParamType.FLOAT, "你对该用户的好感程度，0.0(陌生/不喜欢)到1.0(很喜欢/好友)。当你们的互动让你对TA的感觉发生变化时更新（可选）", False, None),
    ]
    available_for_llm = True
    history_ttl = 5

    def __init__(self, plugin_config: dict | None = None, chat_stream: Any = None):
        super().__init__(plugin_config, chat_stream)

        # 初始化用于二步调用的LLM
        try:
            self.profile_llm = LLMRequest(
                model_set=model_config.model_task_config.relationship_tracker,
                request_type="user_profile_update"
            )
        except AttributeError:
            # 降级处理
            available_models: ClassVar = [
                attr for attr in dir(model_config.model_task_config)
                if not attr.startswith("_") and attr != "model_dump"
            ]
            if available_models:
                fallback_model = available_models[0]
                logger.warning(f"relationship_tracker配置不存在，使用降级模型: {fallback_model}")
                self.profile_llm = LLMRequest(
                    model_set=getattr(model_config.model_task_config, fallback_model),
                    request_type="user_profile_update"
                )
            else:
                logger.error("无可用的模型配置")
                self.profile_llm = None

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

            # 从数据库获取现有用户画像
            existing_profile = await self._get_user_profile(target_user_id)

            # 如果LLM没有传入任何有效参数，返回提示
            if not any([new_aliases, new_impression, new_keywords, new_score is not None]):
                return {
                    "type": "info",
                    "id": target_user_id,
                    "content": "提示：需要提供至少一项更新内容（别名、印象描述、偏好关键词或好感分数）"
                }

            # 调用LLM进行二步决策
            if self.profile_llm is None:
                logger.error("LLM未正确初始化，无法执行二步调用")
                return {
                    "type": "error",
                    "id": target_user_id,
                    "content": "系统错误：LLM未正确初始化"
                }

            final_profile = await self._llm_decide_final_profile(
                target_user_id=target_user_id,
                existing_profile=existing_profile,
                new_aliases=new_aliases,
                new_impression=new_impression,
                new_keywords=new_keywords,
                new_score=new_score
            )

            if not final_profile:
                return {
                    "type": "error",
                    "id": target_user_id,
                    "content": "LLM决策失败，无法更新用户画像"
                }

            # 更新数据库
            await self._update_user_profile_in_db(target_user_id, final_profile)

            # 构建返回信息
            updates: ClassVar = []
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

    async def _llm_decide_final_profile(
        self,
        target_user_id: str,
        existing_profile: dict[str, Any],
        new_aliases: str,
        new_impression: str,
        new_keywords: str,
        new_score: float | None
    ) -> dict[str, Any] | None:
        """使用LLM决策最终的用户画像内容

        Args:
            target_user_id: 目标用户ID
            existing_profile: 现有画像数据
            new_aliases: LLM传入的新别名
            new_impression: LLM传入的新印象
            new_keywords: LLM传入的新关键词
            new_score: LLM传入的新分数

        Returns:
            dict: 最终决定的画像数据，如果失败返回None
        """
        try:
            # 获取bot人设
            from src.individuality.individuality import Individuality
            individuality = Individuality()
            bot_personality = await individuality.get_personality_block()

            prompt = f"""
你现在是一个有着特定性格和身份的AI助手。你的人设是：{bot_personality}

你正在更新对用户 {target_user_id} 的画像认识。

【当前画像信息】
- 用户名: {existing_profile.get('user_name', target_user_id)}
- 已知别名: {existing_profile.get('user_aliases', '无')}
- 当前印象: {existing_profile.get('relationship_text', '暂无印象')}
- 偏好关键词: {existing_profile.get('preference_keywords', '未知')}
- 当前好感分: {existing_profile.get('relationship_score', 0.3):.2f}

【本次想要更新的内容】
- 新增/更新别名: {new_aliases if new_aliases else '不更新'}
- 新的印象描述: {new_impression if new_impression else '不更新'}
- 新的偏好关键词: {new_keywords if new_keywords else '不更新'}
- 新的好感分数: {new_score if new_score is not None else '不更新'}

请综合考虑现有信息和新信息，决定最终的用户画像内容。注意：
1. 别名：如果提供了新别名，应该与现有别名合并（去重），而不是替换
2. 印象描述：如果提供了新印象，应该综合现有印象和新印象，形成更完整的认识（100-200字）
3. 偏好关键词：如果提供了新关键词，应该与现有关键词合并（去重），每个关键词简短
4. 好感分数：如果提供了新分数，需要结合现有分数合理调整（变化不宜过大，遵循现实逻辑）

请以JSON格式返回最终决定：
{{
    "user_aliases": "最终的别名列表，逗号分隔",
    "relationship_text": "最终的印象描述（100-200字），整体性、泛化的理解",
    "preference_keywords": "最终的偏好关键词，逗号分隔",
    "relationship_score": 最终的好感分数（0.0-1.0）,
    "reasoning": "你的决策理由"
}}
"""

            # 调用LLM
            llm_response, _ = await self.profile_llm.generate_response_async(prompt=prompt)

            if not llm_response:
                logger.warning("LLM未返回有效响应")
                return None

            # 使用统一的 JSON 解析工具
            response_data = extract_and_parse_json(llm_response, strict=False)
            if not response_data or not isinstance(response_data, dict):
                logger.error("LLM响应JSON解析失败")
                logger.debug(f"LLM原始响应: {llm_response[:500] if llm_response else 'N/A'}")
                return None

            # 提取最终决定的数据
            final_profile = {
                "user_aliases": response_data.get("user_aliases", existing_profile.get("user_aliases", "")),
                "relationship_text": response_data.get("relationship_text", existing_profile.get("relationship_text", "")),
                "preference_keywords": response_data.get("preference_keywords", existing_profile.get("preference_keywords", "")),
                "relationship_score": max(0.0, min(1.0, float(response_data.get("relationship_score", existing_profile.get("relationship_score", 0.3))))),
            }

            logger.info(f"LLM决策完成: {target_user_id}")
            logger.debug(f"决策理由: {response_data.get('reasoning', '无')}")

            return final_profile
        except Exception as e:
            logger.error(f"LLM决策失败: {e}", exc_info=True)
            return None

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

    # 已移除自定义的 _clean_llm_json_response 方法，统一使用 src.utils.json_parser.extract_and_parse_json
