"""
记忆系统管理器
替代原有的 Hippocampus 和 instant_memory 系统
"""

import re
from dataclasses import dataclass
from typing import Any

from src.chat.memory_system.memory_chunk import MemoryChunk, MemoryType
from src.chat.memory_system.memory_system import MemorySystem
from src.chat.memory_system.message_collection_processor import MessageCollectionProcessor
from src.chat.memory_system.message_collection_storage import MessageCollectionStorage
from src.common.logger import get_logger

logger = get_logger(__name__)


@dataclass
class MemoryResult:
    """记忆查询结果"""

    content: str
    memory_type: str
    confidence: float
    importance: float
    timestamp: float
    source: str = "memory"
    relevance_score: float = 0.0
    structure: dict[str, Any] | None = None


class MemoryManager:
    """记忆系统管理器 - 替代原有的 HippocampusManager"""

    def __init__(self):
        self.memory_system: MemorySystem | None = None
        self.message_collection_storage: MessageCollectionStorage | None = None
        self.message_collection_processor: MessageCollectionProcessor | None = None
        self.is_initialized = False
        self.user_cache = {}  # 用户记忆缓存

    def _clean_text(self, text: Any) -> str:
        if text is None:
            return ""

        cleaned = re.sub(r"[\s\u3000]+", " ", str(text)).strip()
        cleaned = re.sub(r"[、，,；;]+$", "", cleaned)
        return cleaned

    async def initialize(self):
        """初始化记忆系统"""
        if self.is_initialized:
            return

        try:
            from src.config.config import global_config

            # 检查是否启用记忆系统
            if not global_config.memory.enable_memory:
                logger.info("记忆系统已禁用，跳过初始化")
                self.is_initialized = True
                return

            logger.info("正在初始化记忆系统...")

            # 初始化记忆系统
            from src.chat.memory_system.memory_system import get_memory_system
            self.memory_system = get_memory_system()

            # 初始化消息集合系统
            self.message_collection_storage = MessageCollectionStorage()
            self.message_collection_processor = MessageCollectionProcessor(self.message_collection_storage)

            self.is_initialized = True
            logger.info(" 记忆系统初始化完成")

        except Exception as e:
            logger.error(f"记忆系统初始化失败: {e}")
            # 如果系统初始化失败，创建一个空的管理器避免系统崩溃
            self.memory_system = None
            self.message_collection_storage = None
            self.message_collection_processor = None
            self.is_initialized = True  # 标记为已初始化但系统不可用

    def get_hippocampus(self):
        """兼容原有接口 - 返回空"""
        logger.debug("get_hippocampus 调用 - 记忆系统不使用此方法")
        return {}

    async def build_memory(self):
        """兼容原有接口 - 构建记忆"""
        if not self.is_initialized or not self.memory_system:
            return

        try:
            # 记忆系统使用实时构建，不需要定时构建
            logger.debug("build_memory 调用 - 记忆系统使用实时构建")
        except Exception as e:
            logger.error(f"build_memory 失败: {e}")

    async def forget_memory(self, percentage: float = 0.005):
        """兼容原有接口 - 遗忘机制"""
        if not self.is_initialized or not self.memory_system:
            return

        try:
            # 增强记忆系统有内置的遗忘机制
            logger.debug(f"forget_memory 调用 - 参数: {percentage}")
            # 可以在这里调用增强系统的维护功能
            await self.memory_system.maintenance()
        except Exception as e:
            logger.error(f"forget_memory 失败: {e}")

    async def get_memory_from_text(
        self,
        text: str,
        chat_id: str,
        user_id: str,
        max_memory_num: int = 3,
        max_memory_length: int = 2,
        time_weight: float = 1.0,
        keyword_weight: float = 1.0,
    ) -> list[tuple[str, str]]:
        """从文本获取相关记忆 - 兼容原有接口"""
        if not self.is_initialized or not self.memory_system:
            return []

        try:
            # 使用增强记忆系统检索
            context = {
                "chat_id": chat_id,
                "expected_memory_types": [MemoryType.PERSONAL_FACT, MemoryType.EVENT, MemoryType.PREFERENCE],
            }

            relevant_memories = await self.memory_system.retrieve_relevant_memories(
                query=text, user_id=user_id, context=context, limit=max_memory_num
            )

            # 转换为原有格式 (topic, content)
            results = []
            for memory in relevant_memories:
                topic = memory.memory_type.value
                content = memory.text_content
                results.append((topic, content))

            logger.debug(f"从文本检索到 {len(results)} 条相关记忆")

            # 如果检索到有效记忆，打印详细信息
            if results:
                logger.info(f"📚 从文本 '{text[:50]}...' 检索到 {len(results)} 条有效记忆:")
                for i, (topic, content) in enumerate(results, 1):
                    # 处理长内容，如果超过150字符则截断
                    display_content = content
                    if len(content) > 150:
                        display_content = content[:150] + "..."
                    logger.info(f"  记忆#{i} [{topic}]: {display_content}")

            return results

        except Exception as e:
            logger.error(f"get_memory_from_text 失败: {e}")
            return []

    async def get_memory_from_topic(
        self, valid_keywords: list[str], max_memory_num: int = 3, max_memory_length: int = 2, max_depth: int = 3
    ) -> list[tuple[str, str]]:
        """从关键词获取记忆 - 兼容原有接口"""
        if not self.is_initialized or not self.memory_system:
            return []

        try:
            # 将关键词转换为查询文本
            query_text = " ".join(valid_keywords)

            # 使用增强记忆系统检索
            context = {
                "keywords": valid_keywords,
                "expected_memory_types": [
                    MemoryType.PERSONAL_FACT,
                    MemoryType.EVENT,
                    MemoryType.PREFERENCE,
                    MemoryType.OPINION,
                ],
            }

            relevant_memories = await self.memory_system.retrieve_relevant_memories(
                query_text=query_text,
                user_id="default_user",  # 可以根据实际需要传递
                context=context,
                limit=max_memory_num,
            )

            # 转换为原有格式 (topic, content)
            results = []
            for memory in relevant_memories:
                topic = memory.memory_type.value
                content = memory.text_content
                results.append((topic, content))

            logger.debug(f"从关键词 {valid_keywords} 检索到 {len(results)} 条相关记忆")

            # 如果检索到有效记忆，打印详细信息
            if results:
                keywords_str = ", ".join(valid_keywords[:5])  # 最多显示5个关键词
                if len(valid_keywords) > 5:
                    keywords_str += f" ... (共{len(valid_keywords)}个关键词)"
                logger.info(f"🔍 从关键词 [{keywords_str}] 检索到 {len(results)} 条有效记忆:")
                for i, (topic, content) in enumerate(results, 1):
                    # 处理长内容，如果超过150字符则截断
                    display_content = content
                    if len(content) > 150:
                        display_content = content[:150] + "..."
                    logger.info(f"  记忆#{i} [{topic}]: {display_content}")

            return results

        except Exception as e:
            logger.error(f"get_memory_from_topic 失败: {e}")
            return []

    def get_memory_from_keyword(self, keyword: str, max_depth: int = 2) -> list:
        """从单个关键词获取记忆 - 兼容原有接口"""
        if not self.is_initialized or not self.memory_system:
            return []

        try:
            # 同步方法，返回空列表
            logger.debug(f"get_memory_from_keyword 调用 - 关键词: {keyword}")
            return []
        except Exception as e:
            logger.error(f"get_memory_from_keyword 失败: {e}")
            return []

    async def process_conversation(
        self, conversation_text: str, context: dict[str, Any], user_id: str, timestamp: float | None = None
    ) -> list[MemoryChunk]:
        """处理对话并构建记忆 - 新增功能"""
        if not self.is_initialized or not self.memory_system:
            return []

        try:
            # 将消息添加到消息集合处理器
            chat_id = context.get("chat_id")
            if self.message_collection_processor and chat_id:
                await self.message_collection_processor.add_message(conversation_text, chat_id)

            payload_context = dict(context or {})
            payload_context.setdefault("conversation_text", conversation_text)
            if timestamp is not None:
                payload_context.setdefault("timestamp", timestamp)

            result = await self.memory_system.process_conversation_memory(payload_context)

            # 从结果中提取记忆块
            memory_chunks = []
            if result.get("success"):
                memory_chunks = result.get("created_memories", [])

            logger.info(f"从对话构建了 {len(memory_chunks)} 条记忆")
            return memory_chunks

        except Exception as e:
            logger.error(f"process_conversation 失败: {e}")
            return []

    async def get_enhanced_memory_context(
        self, query_text: str, user_id: str, context: dict[str, Any] | None = None, limit: int = 5
    ) -> list[MemoryResult]:
        """获取增强记忆上下文 - 新增功能"""
        if not self.is_initialized or not self.memory_system:
            return []

        try:
            relevant_memories = await self.memory_system.retrieve_relevant_memories(
                query=query_text, user_id=None, context=context or {}, limit=limit
            )

            results = []
            for memory in relevant_memories:
                formatted_content, structure = self._format_memory_chunk(memory)
                result = MemoryResult(
                    content=formatted_content,
                    memory_type=memory.memory_type.value,
                    confidence=memory.metadata.confidence.value,
                    importance=memory.metadata.importance.value,
                    timestamp=memory.metadata.created_at,
                    source="enhanced_memory",
                    relevance_score=memory.metadata.relevance_score,
                    structure=structure,
                )
                results.append(result)

            return results

        except Exception as e:
            logger.error(f"get_enhanced_memory_context 失败: {e}")
            return []

    def _format_memory_chunk(self, memory: MemoryChunk) -> tuple[str, dict[str, Any]]:
        """将记忆块转换为更易读的文本描述"""
        structure = memory.content.to_dict()
        if memory.display:
            return self._clean_text(memory.display), structure

        subject = structure.get("subject")
        predicate = structure.get("predicate") or ""
        obj = structure.get("object")

        subject_display = self._format_subject(subject, memory)
        formatted = self._apply_predicate_format(subject_display, predicate, obj)

        if not formatted:
            predicate_display = self._format_predicate(predicate)
            object_display = self._format_object(obj)
            formatted = f"{subject_display}{predicate_display}{object_display}".strip()

        formatted = self._clean_text(formatted)

        return formatted, structure

    def _format_subject(self, subject: str | None, memory: MemoryChunk) -> str:
        if not subject:
            return "该用户"

        if subject == memory.metadata.user_id:
            return "该用户"
        if memory.metadata.chat_id and subject == memory.metadata.chat_id:
            return "该聊天"
        return self._clean_text(subject)

    def _apply_predicate_format(self, subject: str, predicate: str, obj: Any) -> str | None:
        predicate = (predicate or "").strip()
        obj_value = obj

        if predicate == "is_named":
            name = self._extract_from_object(obj_value, ["name", "nickname"]) or self._format_object(obj_value)
            name = self._clean_text(name)
            if not name:
                return None
            name_display = name if (name.startswith("「") and name.endswith("」")) else f"「{name}」"
            return f"{subject}的昵称是{name_display}"
        if predicate == "is_age":
            age = self._extract_from_object(obj_value, ["age"]) or self._format_object(obj_value)
            age = self._clean_text(age)
            if not age:
                return None
            return f"{subject}今年{age}岁"
        if predicate == "is_profession":
            profession = self._extract_from_object(obj_value, ["profession", "job"]) or self._format_object(obj_value)
            profession = self._clean_text(profession)
            if not profession:
                return None
            return f"{subject}的职业是{profession}"
        if predicate == "lives_in":
            location = self._extract_from_object(obj_value, ["location", "city", "place"]) or self._format_object(
                obj_value
            )
            location = self._clean_text(location)
            if not location:
                return None
            return f"{subject}居住在{location}"
        if predicate == "has_phone":
            phone = self._extract_from_object(obj_value, ["phone", "number"]) or self._format_object(obj_value)
            phone = self._clean_text(phone)
            if not phone:
                return None
            return f"{subject}的电话号码是{phone}"
        if predicate == "has_email":
            email = self._extract_from_object(obj_value, ["email"]) or self._format_object(obj_value)
            email = self._clean_text(email)
            if not email:
                return None
            return f"{subject}的邮箱是{email}"
        if predicate == "likes":
            liked = self._format_object(obj_value)
            if not liked:
                return None
            return f"{subject}喜欢{liked}"
        if predicate == "likes_food":
            food = self._format_object(obj_value)
            if not food:
                return None
            return f"{subject}爱吃{food}"
        if predicate == "dislikes":
            disliked = self._format_object(obj_value)
            if not disliked:
                return None
            return f"{subject}不喜欢{disliked}"
        if predicate == "hates":
            hated = self._format_object(obj_value)
            if not hated:
                return None
            return f"{subject}讨厌{hated}"
        if predicate == "favorite_is":
            favorite = self._format_object(obj_value)
            if not favorite:
                return None
            return f"{subject}最喜欢{favorite}"
        if predicate == "mentioned_event":
            event_text = self._extract_from_object(obj_value, ["event_text", "description"]) or self._format_object(
                obj_value
            )
            event_text = self._clean_text(self._truncate(event_text))
            if not event_text:
                return None
            return f"{subject}提到了计划或事件：{event_text}"
        if predicate in {"正在", "在", "正在进行"}:
            action = self._format_object(obj_value)
            if not action:
                return None
            return f"{subject}{predicate}{action}"
        if predicate in {"感到", "觉得", "表示", "提到", "说道", "说"}:
            feeling = self._format_object(obj_value)
            if not feeling:
                return None
            return f"{subject}{predicate}{feeling}"
        if predicate in {"与", "和", "跟"}:
            counterpart = self._format_object(obj_value)
            if counterpart:
                return f"{subject}{predicate}{counterpart}"
            return f"{subject}{predicate}"

        return None

    def _format_predicate(self, predicate: str) -> str:
        if not predicate:
            return ""
        predicate_map = {
            "is_named": "的昵称是",
            "is_profession": "的职业是",
            "lives_in": "居住在",
            "has_phone": "的电话是",
            "has_email": "的邮箱是",
            "likes": "喜欢",
            "dislikes": "不喜欢",
            "likes_food": "爱吃",
            "hates": "讨厌",
            "favorite_is": "最喜欢",
            "mentioned_event": "提到的事件",
        }
        if predicate in predicate_map:
            connector = predicate_map[predicate]
            if connector.startswith("的"):
                return connector
            return f" {connector} "
        cleaned = predicate.replace("_", " ").strip()
        if re.search(r"[\u4e00-\u9fff]", cleaned):
            return cleaned
        return f" {cleaned} "

    def _format_object(self, obj: Any) -> str:
        if obj is None:
            return ""
        if isinstance(obj, dict):
            parts = []
            for key, value in obj.items():
                formatted_value = self._format_object(value)
                if not formatted_value:
                    continue
                pretty_key = {
                    "name": "名字",
                    "profession": "职业",
                    "location": "位置",
                    "event_text": "内容",
                    "timestamp": "时间",
                }.get(key, key)
                parts.append(f"{pretty_key}: {formatted_value}")
            return self._clean_text("；".join(parts))
        if isinstance(obj, list):
            formatted_items = [self._format_object(item) for item in obj]
            filtered = [item for item in formatted_items if item]
            return self._clean_text("、".join(filtered)) if filtered else ""
        if isinstance(obj, int | float):
            return str(obj)
        text = self._truncate(str(obj).strip())
        return self._clean_text(text)

    def _extract_from_object(self, obj: Any, keys: list[str]) -> str | None:
        if isinstance(obj, dict):
            for key in keys:
                if obj.get(key):
                    value = obj[key]
                    if isinstance(value, dict | list):
                        return self._clean_text(self._format_object(value))
                    return self._clean_text(value)
        if isinstance(obj, list) and obj:
            return self._clean_text(self._format_object(obj[0]))
        if isinstance(obj, str | int | float):
            return self._clean_text(obj)
        return None

    def _truncate(self, text: str, max_length: int = 80) -> str:
        if len(text) <= max_length:
            return text
        return text[: max_length - 1] + "…"

    async def shutdown(self):
        """关闭增强记忆系统"""
        if not self.is_initialized:
            return

        try:
            if self.memory_system:
                await self.memory_system.shutdown()
            logger.info(" 记忆系统已关闭")
        except Exception as e:
            logger.error(f"关闭记忆系统失败: {e}")


# 全局记忆管理器实例
memory_manager = MemoryManager()
