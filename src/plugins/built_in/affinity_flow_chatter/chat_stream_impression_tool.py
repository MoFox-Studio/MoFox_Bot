"""
聊天流印象更新工具

通过LLM二步调用机制更新对聊天流（如QQ群）的整体印象，包括主观描述、聊天风格、话题关键词和兴趣分数
"""

import json
from typing import Any, ClassVar

from src.common.database.api.crud import CRUDBase
from src.common.database.core.models import ChatStreams
from src.common.logger import get_logger
from src.config.config import model_config
from src.llm_models.utils_model import LLMRequest
from src.plugin_system import BaseTool, ToolParamType
from src.utils.json_parser import extract_and_parse_json

logger = get_logger("chat_stream_impression_tool")


class ChatStreamImpressionTool(BaseTool):
    """聊天流印象更新工具

    使用二步调用机制：
    1. LLM决定是否调用工具并传入初步参数（stream_id会自动传入）
    2. 工具内部调用LLM，结合现有数据和传入参数，决定最终更新内容
    """

    name = "update_chat_stream_impression"
    description = "当你通过观察聊天记录对当前聊天环境（群聊或私聊）产生了整体印象或认识时使用此工具，更新对这个聊天流的看法。包括：环境氛围、聊天风格、常见话题、你的兴趣程度。调用时机：当你发现这个聊天环境有明显的氛围特点（如很活跃、很专业、很闲聊）、群成员经常讨论某类话题、或者你对这个环境的感受发生变化时。注意：这是对整个聊天环境的印象，而非对单个用户。"
    parameters: ClassVar = [
        (
            "impression_description",
            ToolParamType.STRING,
            "你对这个聊天环境的整体感受和印象，例如'这是个技术氛围浓厚的群'、'大家都很友好热情'。当你通过聊天记录感受到环境特点时填写（可选）",
            False,
            None,
        ),
        (
            "chat_style",
            ToolParamType.STRING,
            "这个聊天环境的风格特征，如'活跃热闹,互帮互助'、'严肃专业,深度讨论'、'轻松闲聊,段子频出'等。当你发现聊天方式有明显特点时填写（可选）",
            False,
            None,
        ),
        (
            "topic_keywords",
            ToolParamType.STRING,
            "这个聊天环境中经常出现的话题，如'编程,AI,技术分享'或'游戏,动漫,娱乐'。当你观察到群里反复讨论某些主题时填写，多个关键词用逗号分隔（可选）",
            False,
            None,
        ),
        (
            "interest_score",
            ToolParamType.FLOAT,
            "你对这个聊天环境的兴趣和喜欢程度，0.0(无聊/不喜欢)到1.0(很有趣/很喜欢)。当你对这个环境的感觉发生变化时更新（可选）",
            False,
            None,
        ),
    ]
    available_for_llm = True
    history_ttl = 5

    def __init__(self, plugin_config: dict | None = None, chat_stream: Any = None):
        super().__init__(plugin_config, chat_stream)

        # 初始化用于二步调用的LLM
        try:
            self.impression_llm = LLMRequest(
                model_set=model_config.model_task_config.relationship_tracker,
                request_type="chat_stream_impression_update",
            )
        except AttributeError:
            # 降级处理
            available_models = [
                attr
                for attr in dir(model_config.model_task_config)
                if not attr.startswith("_") and attr != "model_dump"
            ]
            if available_models:
                fallback_model = available_models[0]
                logger.warning(f"relationship_tracker配置不存在，使用降级模型: {fallback_model}")
                self.impression_llm = LLMRequest(
                    model_set=getattr(model_config.model_task_config, fallback_model),
                    request_type="chat_stream_impression_update",
                )
            else:
                logger.error("无可用的模型配置")
                self.impression_llm = None

    async def execute(self, function_args: dict[str, Any]) -> dict[str, Any]:
        """执行聊天流印象更新

        Args:
            function_args: 工具参数

        Returns:
            dict: 执行结果
        """
        try:
            # 优先从 function_args 获取 stream_id
            stream_id = function_args.get("stream_id")

            # 如果没有，从 chat_stream 对象获取
            if not stream_id and self.chat_stream:
                try:
                    stream_id = self.chat_stream.stream_id
                    logger.debug(f"从 chat_stream 获取到 stream_id: {stream_id}")
                except AttributeError:
                    logger.warning("chat_stream 对象没有 stream_id 属性")

            # 如果还是没有，返回错误
            if not stream_id:
                logger.error("无法获取 stream_id：function_args 和 chat_stream 都没有提供")
                return {"type": "error", "id": "chat_stream_impression", "content": "错误：无法获取当前聊天流ID"}

            # 从LLM传入的参数
            new_impression = function_args.get("impression_description", "")
            new_style = function_args.get("chat_style", "")
            new_topics = function_args.get("topic_keywords", "")
            new_score = function_args.get("interest_score")

            # 从数据库获取现有聊天流印象
            existing_impression = await self._get_stream_impression(stream_id)

            # 如果LLM没有传入任何有效参数，返回提示
            if not any([new_impression, new_style, new_topics, new_score is not None]):
                return {
                    "type": "info",
                    "id": stream_id,
                    "content": "提示：需要提供至少一项更新内容（印象描述、聊天风格、话题关键词或兴趣分数）",
                }

            # 调用LLM进行二步决策
            if self.impression_llm is None:
                logger.error("LLM未正确初始化，无法执行二步调用")
                return {"type": "error", "id": stream_id, "content": "系统错误：LLM未正确初始化"}

            final_impression = await self._llm_decide_final_impression(
                stream_id=stream_id,
                existing_impression=existing_impression,
                new_impression=new_impression,
                new_style=new_style,
                new_topics=new_topics,
                new_score=new_score,
            )

            if not final_impression:
                return {"type": "error", "id": stream_id, "content": "LLM决策失败，无法更新聊天流印象"}

            # 更新数据库
            await self._update_stream_impression_in_db(stream_id, final_impression)

            # 构建返回信息
            updates = []
            if final_impression.get("stream_impression_text"):
                updates.append(f"印象: {final_impression['stream_impression_text'][:50]}...")
            if final_impression.get("stream_chat_style"):
                updates.append(f"风格: {final_impression['stream_chat_style']}")
            if final_impression.get("stream_topic_keywords"):
                updates.append(f"话题: {final_impression['stream_topic_keywords']}")
            if final_impression.get("stream_interest_score") is not None:
                updates.append(f"兴趣分: {final_impression['stream_interest_score']:.2f}")

            result_text = f"已更新聊天流 {stream_id} 的印象：\n" + "\n".join(updates)
            logger.info(f"聊天流印象更新成功: {stream_id}")

            return {"type": "chat_stream_impression_update", "id": stream_id, "content": result_text}

        except Exception as e:
            logger.error(f"聊天流印象更新失败: {e}", exc_info=True)
            return {
                "type": "error",
                "id": function_args.get("stream_id", "unknown"),
                "content": f"聊天流印象更新失败: {e!s}",
            }

    async def _get_stream_impression(self, stream_id: str) -> dict[str, Any]:
        """从数据库获取聊天流现有印象

        Args:
            stream_id: 聊天流ID

        Returns:
            dict: 聊天流印象数据
        """
        try:
            # 使用CRUD进行查询
            crud = CRUDBase(ChatStreams)
            stream = await crud.get_by(stream_id=stream_id)

            if stream:
                return {
                    "stream_impression_text": stream.stream_impression_text or "",
                    "stream_chat_style": stream.stream_chat_style or "",
                    "stream_topic_keywords": stream.stream_topic_keywords or "",
                    "stream_interest_score": float(stream.stream_interest_score)
                    if stream.stream_interest_score is not None
                    else 0.5,
                    "group_name": stream.group_name or "私聊",
                }
            else:
                # 聊天流不存在，返回默认值
                return {
                    "stream_impression_text": "",
                    "stream_chat_style": "",
                    "stream_topic_keywords": "",
                    "stream_interest_score": 0.5,
                    "group_name": "未知",
                }
        except Exception as e:
            logger.error(f"获取聊天流印象失败: {e}")
            return {
                "stream_impression_text": "",
                "stream_chat_style": "",
                "stream_topic_keywords": "",
                "stream_interest_score": 0.5,
                "group_name": "未知",
            }

    async def _llm_decide_final_impression(
        self,
        stream_id: str,
        existing_impression: dict[str, Any],
        new_impression: str,
        new_style: str,
        new_topics: str,
        new_score: float | None,
    ) -> dict[str, Any] | None:
        """使用LLM决策最终的聊天流印象内容

        Args:
            stream_id: 聊天流ID
            existing_impression: 现有印象数据
            new_impression: LLM传入的新印象
            new_style: LLM传入的新风格
            new_topics: LLM传入的新话题
            new_score: LLM传入的新分数

        Returns:
            dict: 最终决定的印象数据，如果失败返回None
        """
        try:
            # 获取bot人设
            from src.individuality.individuality import Individuality

            individuality = Individuality()
            bot_personality = await individuality.get_personality_block()

            prompt = f"""
你现在是一个有着特定性格和身份的AI助手。你的人设是：{bot_personality}

你正在更新对聊天流 {stream_id} 的整体印象。

【当前聊天流信息】
- 聊天环境: {existing_impression.get("group_name", "未知")}
- 当前印象: {existing_impression.get("stream_impression_text", "暂无印象")}
- 聊天风格: {existing_impression.get("stream_chat_style", "未知")}
- 常见话题: {existing_impression.get("stream_topic_keywords", "未知")}
- 当前兴趣分: {existing_impression.get("stream_interest_score", 0.5):.2f}

【本次想要更新的内容】
- 新的印象描述: {new_impression if new_impression else "不更新"}
- 新的聊天风格: {new_style if new_style else "不更新"}
- 新的话题关键词: {new_topics if new_topics else "不更新"}
- 新的兴趣分数: {new_score if new_score is not None else "不更新"}

请综合考虑现有信息和新信息，决定最终的聊天流印象内容。注意：
1. 印象描述：如果提供了新印象，应该综合现有印象和新印象，形成对这个聊天环境的整体认知（100-200字）
2. 聊天风格：如果提供了新风格，应该用简洁的词语概括，如"活跃轻松"、"严肃专业"、"幽默随性"等
3. 话题关键词：如果提供了新话题，应该与现有话题合并（去重），保留最核心和频繁的话题
4. 兴趣分数：如果提供了新分数，需要结合现有分数合理调整（0.0表示完全不感兴趣，1.0表示非常感兴趣）

请以JSON格式返回最终决定：
{{
    "stream_impression_text": "最终的印象描述（100-200字），整体性的对这个聊天环境的认知",
    "stream_chat_style": "最终的聊天风格，简洁概括",
    "stream_topic_keywords": "最终的话题关键词，逗号分隔",
    "stream_interest_score": 最终的兴趣分数（0.0-1.0）,
    "reasoning": "你的决策理由"
}}
"""

            # 调用LLM
            if not self.impression_llm:
                logger.info("未初始化impression_llm")
                return None
            llm_response, _ = await self.impression_llm.generate_response_async(prompt=prompt)

            if not llm_response:
                logger.warning("LLM未返回有效响应")
                return None

            # 使用统一的 JSON 解析工具
            response_data = extract_and_parse_json(llm_response, strict=False)
            if not response_data or not isinstance(response_data, dict):
                logger.warning("解析LLM响应失败")
                return None

            # 提取最终决定的数据
            final_impression = {
                "stream_impression_text": response_data.get(
                    "stream_impression_text", existing_impression.get("stream_impression_text", "")
                ),
                "stream_chat_style": response_data.get(
                    "stream_chat_style", existing_impression.get("stream_chat_style", "")
                ),
                "stream_topic_keywords": response_data.get(
                    "stream_topic_keywords", existing_impression.get("stream_topic_keywords", "")
                ),
                "stream_interest_score": max(
                    0.0,
                    min(
                        1.0,
                        float(
                            response_data.get(
                                "stream_interest_score", existing_impression.get("stream_interest_score", 0.5)
                            )
                        ),
                    ),
                ),
            }

            logger.info(f"LLM决策完成: {stream_id}")
            logger.debug(f"决策理由: {response_data.get('reasoning', '无')}")

            return final_impression

        except json.JSONDecodeError as e:
            logger.error(f"LLM响应JSON解析失败: {e}")
            logger.debug(f"LLM原始响应: {llm_response if 'llm_response' in locals() else 'N/A'}")
            return None
        except Exception as e:
            logger.error(f"LLM决策失败: {e}", exc_info=True)
            return None

    async def _update_stream_impression_in_db(self, stream_id: str, impression: dict[str, Any]):
        """更新数据库中的聊天流印象

        Args:
            stream_id: 聊天流ID
            impression: 印象数据
        """
        try:
            # 使用CRUD进行更新
            crud = CRUDBase(ChatStreams)
            existing = await crud.get_by(stream_id=stream_id)

            if existing:
                # 更新现有记录
                await crud.update(
                    existing.id,
                    {
                        "stream_impression_text": impression.get("stream_impression_text", ""),
                        "stream_chat_style": impression.get("stream_chat_style", ""),
                        "stream_topic_keywords": impression.get("stream_topic_keywords", ""),
                        "stream_interest_score": impression.get("stream_interest_score", 0.5),
                    }
                )

                # 使缓存失效
                from src.common.database.optimization.cache_manager import get_cache
                from src.common.database.utils.decorators import generate_cache_key
                cache = await get_cache()
                await cache.delete(generate_cache_key("stream_impression", stream_id))
                await cache.delete(generate_cache_key("chat_stream", stream_id))

                logger.info(f"聊天流印象已更新到数据库: {stream_id}")
            else:
                error_msg = f"聊天流 {stream_id} 不存在于数据库中，无法更新印象"
                logger.error(error_msg)
                # 注意：通常聊天流应该在消息处理时就已创建，这里不创建新记录
                raise ValueError(error_msg)

        except Exception as e:
            logger.error(f"更新聊天流印象到数据库失败: {e}", exc_info=True)
            raise

    # 已移除自定义的 _clean_llm_json_response 方法，统一使用 src.utils.json_parser.extract_and_parse_json
    
    def _clean_llm_json_response_deprecated(self, response: str) -> str:
        """已废弃，保留仅用于兼容性
        
        请使用 src.utils.json_parser.extract_and_parse_json 替代
        """
        from src.utils.json_parser import extract_and_parse_json
        try:
            import json
            result = extract_and_parse_json(response, strict=False)
            return json.dumps(result) if result else response
        except Exception as e:
            logger.warning(f"清理LLM响应失败: {e}")
            return response
