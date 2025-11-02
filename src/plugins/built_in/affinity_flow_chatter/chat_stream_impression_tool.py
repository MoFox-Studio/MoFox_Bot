"""
聊天流印象更新工具

直接更新对聊天流（如QQ群）的整体印象，包括主观描述、聊天风格、话题关键词和兴趣分数
现在依赖工具调用历史记录，LLM可以看到之前的调用结果，因此直接覆盖更新即可
"""

from typing import Any, ClassVar

from src.common.database.api.crud import CRUDBase
from src.common.database.core.models import ChatStreams
from src.common.logger import get_logger
from src.plugin_system import BaseTool, ToolParamType

logger = get_logger("chat_stream_impression_tool")


class ChatStreamImpressionTool(BaseTool):
    """聊天流印象更新工具

    直接使用LLM传入的参数更新聊天流印象。
    由于工具执行器现在支持历史记录，LLM可以看到之前的调用结果，因此无需再次调用LLM进行合并。
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

            # 从数据库获取现有聊天流印象（用于返回信息）
            existing_impression = await self._get_stream_impression(stream_id)

            # 如果LLM没有传入任何有效参数，返回提示
            if not any([new_impression, new_style, new_topics, new_score is not None]):
                return {
                    "type": "info",
                    "id": stream_id,
                    "content": "提示：需要提供至少一项更新内容（印象描述、聊天风格、话题关键词或兴趣分数）",
                }

            # 直接使用LLM传入的值进行覆盖更新（保留未更新的字段）
            final_impression = {
                "stream_impression_text": new_impression if new_impression else existing_impression.get("stream_impression_text", ""),
                "stream_chat_style": new_style if new_style else existing_impression.get("stream_chat_style", ""),
                "stream_topic_keywords": new_topics if new_topics else existing_impression.get("stream_topic_keywords", ""),
                "stream_interest_score": new_score if new_score is not None else existing_impression.get("stream_interest_score", 0.5),
            }

            # 确保分数在有效范围内
            final_impression["stream_interest_score"] = max(0.0, min(1.0, float(final_impression["stream_interest_score"])))

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


