"""
结构化记忆单元设计
实现高质量、结构化的记忆单元，符合文档设计规范
"""

import hashlib
import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import numpy as np
import orjson

from src.common.logger import get_logger

logger = get_logger(__name__)


class MemoryType(Enum):
    """记忆类型分类"""

    PERSONAL_FACT = "personal_fact"  # 个人事实（姓名、职业、住址等）
    EVENT = "event"  # 事件（重要经历、约会等）
    PREFERENCE = "preference"  # 偏好（喜好、习惯等）
    OPINION = "opinion"  # 观点（对事物的看法）
    RELATIONSHIP = "relationship"  # 关系（与他人的关系）
    EMOTION = "emotion"  # 情感状态
    KNOWLEDGE = "knowledge"  # 知识信息
    SKILL = "skill"  # 技能能力
    GOAL = "goal"  # 目标计划
    EXPERIENCE = "experience"  # 经验教训
    CONTEXTUAL = "contextual"  # 上下文信息


class ConfidenceLevel(Enum):
    """置信度等级"""

    LOW = 1  # 低置信度，可能不准确
    MEDIUM = 2  # 中等置信度，有一定依据
    HIGH = 3  # 高置信度，有明确来源
    VERIFIED = 4  # 已验证，非常可靠


class ImportanceLevel(Enum):
    """重要性等级"""

    LOW = 1  # 低重要性，普通信息
    NORMAL = 2  # 一般重要性，日常信息
    HIGH = 3  # 高重要性，重要信息
    CRITICAL = 4  # 关键重要性，核心信息


@dataclass
class ContentStructure:
    """主谓宾结构，包含自然语言描述"""

    subject: str | list[str]
    predicate: str
    object: str | dict
    display: str = ""

    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式"""
        return {"subject": self.subject, "predicate": self.predicate, "object": self.object, "display": self.display}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ContentStructure":
        """从字典创建实例"""
        return cls(
            subject=data.get("subject", ""),
            predicate=data.get("predicate", ""),
            object=data.get("object", ""),
            display=data.get("display", ""),
        )

    def to_subject_list(self) -> list[str]:
        """将主语转换为列表形式"""
        if isinstance(self.subject, list):
            return [s for s in self.subject if isinstance(s, str) and s.strip()]
        if isinstance(self.subject, str) and self.subject.strip():
            return [self.subject.strip()]
        return []

    def __str__(self) -> str:
        """字符串表示"""
        if self.display:
            return self.display
        subjects = "、".join(self.to_subject_list()) or str(self.subject)
        object_str = self.object if isinstance(self.object, str) else str(self.object)
        return f"{subjects} {self.predicate} {object_str}".strip()


@dataclass
class MemoryMetadata:
    """记忆元数据 - 简化版本"""

    # 基础信息
    memory_id: str  # 唯一标识符
    user_id: str  # 用户ID
    chat_id: str | None = None  # 聊天ID（群聊或私聊）

    # 时间信息
    created_at: float = 0.0  # 创建时间戳
    last_accessed: float = 0.0  # 最后访问时间
    last_modified: float = 0.0  # 最后修改时间

    # 激活频率管理
    last_activation_time: float = 0.0  # 最后激活时间
    activation_frequency: int = 0  # 激活频率（单位时间内的激活次数）
    total_activations: int = 0  # 总激活次数

    # 统计信息
    access_count: int = 0  # 访问次数
    relevance_score: float = 0.0  # 相关度评分

    # 信心和重要性（核心字段）
    confidence: ConfidenceLevel = ConfidenceLevel.MEDIUM
    importance: ImportanceLevel = ImportanceLevel.NORMAL

    # 遗忘机制相关
    forgetting_threshold: float = 0.0  # 遗忘阈值（动态计算）
    last_forgetting_check: float = 0.0  # 上次遗忘检查时间

    # 来源信息
    source_context: str | None = None  # 来源上下文片段
    # 兼容旧字段: 一些代码或旧版本可能直接访问 metadata.source
    source: str | None = None

    def __post_init__(self):
        """后初始化处理"""
        if not self.memory_id:
            self.memory_id = str(uuid.uuid4())

        current_time = time.time()

        if self.created_at == 0:
            self.created_at = current_time

        if self.last_accessed == 0:
            self.last_accessed = current_time

        if self.last_modified == 0:
            self.last_modified = current_time

        if self.last_activation_time == 0:
            self.last_activation_time = current_time

        if self.last_forgetting_check == 0:
            self.last_forgetting_check = current_time

        # 兼容性：如果旧字段 source 被使用，保证 source 与 source_context 同步
        if not getattr(self, "source", None) and getattr(self, "source_context", None):
            try:
                self.source = str(self.source_context)
            except Exception:
                self.source = None
        # 如果有 source 字段但 source_context 为空，也同步回去
        if not getattr(self, "source_context", None) and getattr(self, "source", None):
            try:
                self.source_context = str(self.source)
            except Exception:
                self.source_context = None

    def update_access(self):
        """更新访问信息"""
        current_time = time.time()
        self.last_accessed = current_time
        self.access_count += 1
        self.total_activations += 1

        # 更新激活频率
        self._update_activation_frequency(current_time)

    def _update_activation_frequency(self, current_time: float):
        """更新激活频率（24小时内的激活次数）"""

        # 如果超过24小时，重置激活频率
        if current_time - self.last_activation_time > 86400:  # 24小时 = 86400秒
            self.activation_frequency = 1
        else:
            self.activation_frequency += 1

        self.last_activation_time = current_time

    def update_relevance(self, new_score: float):
        """更新相关度评分"""
        self.relevance_score = max(0.0, min(1.0, new_score))
        self.last_modified = time.time()

    def calculate_forgetting_threshold(self) -> float:
        """计算遗忘阈值（天数）"""
        # 基础天数
        base_days = 30.0

        # 重要性权重 (1-4 -> 0-3)
        importance_weight = (self.importance.value - 1) * 15  # 0, 15, 30, 45

        # 置信度权重 (1-4 -> 0-3)
        confidence_weight = (self.confidence.value - 1) * 10  # 0, 10, 20, 30

        # 激活频率权重（每5次激活增加1天）
        frequency_weight = min(self.activation_frequency, 20) * 0.5  # 最多10天

        # 计算最终阈值
        threshold = base_days + importance_weight + confidence_weight + frequency_weight

        # 设置最小和最大阈值
        return max(7.0, min(threshold, 365.0))  # 7天到1年之间

    def should_forget(self, current_time: float | None = None) -> bool:
        """判断是否应该遗忘"""
        if current_time is None:
            current_time = time.time()

        # 计算遗忘阈值
        self.forgetting_threshold = self.calculate_forgetting_threshold()

        # 计算距离最后激活的时间
        days_since_activation = (current_time - self.last_activation_time) / 86400

        return days_since_activation > self.forgetting_threshold

    def is_dormant(self, current_time: float | None = None, inactive_days: int = 90) -> bool:
        """判断是否处于休眠状态（长期未激活）"""
        if current_time is None:
            current_time = time.time()

        days_since_last_access = (current_time - self.last_accessed) / 86400
        return days_since_last_access > inactive_days

    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式"""
        return {
            "memory_id": self.memory_id,
            "user_id": self.user_id,
            "chat_id": self.chat_id,
            "created_at": self.created_at,
            "last_accessed": self.last_accessed,
            "last_modified": self.last_modified,
            "last_activation_time": self.last_activation_time,
            "activation_frequency": self.activation_frequency,
            "total_activations": self.total_activations,
            "access_count": self.access_count,
            "relevance_score": self.relevance_score,
            "confidence": self.confidence.value,
            "importance": self.importance.value,
            "forgetting_threshold": self.forgetting_threshold,
            "last_forgetting_check": self.last_forgetting_check,
            "source_context": self.source_context,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemoryMetadata":
        """从字典创建实例"""
        return cls(
            memory_id=data.get("memory_id", ""),
            user_id=data.get("user_id", ""),
            chat_id=data.get("chat_id"),
            created_at=data.get("created_at", 0),
            last_accessed=data.get("last_accessed", 0),
            last_modified=data.get("last_modified", 0),
            last_activation_time=data.get("last_activation_time", 0),
            activation_frequency=data.get("activation_frequency", 0),
            total_activations=data.get("total_activations", 0),
            access_count=data.get("access_count", 0),
            relevance_score=data.get("relevance_score", 0.0),
            confidence=ConfidenceLevel(data.get("confidence", ConfidenceLevel.MEDIUM.value)),
            importance=ImportanceLevel(data.get("importance", ImportanceLevel.NORMAL.value)),
            forgetting_threshold=data.get("forgetting_threshold", 0.0),
            last_forgetting_check=data.get("last_forgetting_check", 0),
            source_context=data.get("source_context"),
        )


@dataclass
class MemoryChunk:
    """结构化记忆单元 - 核心数据结构"""

    # 元数据
    metadata: MemoryMetadata

    # 内容结构
    content: ContentStructure  # 主谓宾结构
    memory_type: MemoryType  # 记忆类型

    # 扩展信息
    keywords: list[str] = field(default_factory=list)  # 关键词列表
    tags: list[str] = field(default_factory=list)  # 标签列表
    categories: list[str] = field(default_factory=list)  # 分类列表

    # 语义信息
    embedding: list[float] | None = None  # 语义向量
    semantic_hash: str | None = None  # 语义哈希值

    # 关联信息
    related_memories: list[str] = field(default_factory=list)  # 关联记忆ID列表
    temporal_context: dict[str, Any] | None = None  # 时间上下文

    def __post_init__(self):
        """后初始化处理"""
        if self.embedding and len(self.embedding) > 0:
            self._generate_semantic_hash()

    def _generate_semantic_hash(self):
        """生成语义哈希值"""
        if not self.embedding:
            return

        try:
            # 使用向量和内容生成稳定的哈希
            content_str = f"{self.content.subject}:{self.content.predicate}:{self.content.object!s}"
            embedding_str = ",".join(map(str, [round(x, 6) for x in self.embedding]))

            hash_input = f"{content_str}|{embedding_str}"
            hash_object = hashlib.sha256(hash_input.encode("utf-8"))
            self.semantic_hash = hash_object.hexdigest()[:16]

        except Exception as e:
            logger.warning(f"生成语义哈希失败: {e}")
            self.semantic_hash = str(uuid.uuid4())[:16]

    @property
    def memory_id(self) -> str:
        """获取记忆ID"""
        return self.metadata.memory_id

    @property
    def user_id(self) -> str:
        """获取用户ID"""
        return self.metadata.user_id

    @property
    def text_content(self) -> str:
        """获取文本内容（优先使用display）"""
        return str(self.content)

    @property
    def display(self) -> str:
        """获取展示文本"""
        return self.content.display or str(self.content)

    @property
    def subjects(self) -> list[str]:
        """获取主语列表"""
        return self.content.to_subject_list()

    def update_access(self):
        """更新访问信息"""
        self.metadata.update_access()

    def update_relevance(self, new_score: float):
        """更新相关度评分"""
        self.metadata.update_relevance(new_score)

    def should_forget(self, current_time: float | None = None) -> bool:
        """判断是否应该遗忘"""
        return self.metadata.should_forget(current_time)

    def is_dormant(self, current_time: float | None = None, inactive_days: int = 90) -> bool:
        """判断是否处于休眠状态（长期未激活）"""
        return self.metadata.is_dormant(current_time, inactive_days)

    def calculate_forgetting_threshold(self) -> float:
        """计算遗忘阈值（天数）"""
        return self.metadata.calculate_forgetting_threshold()

    def add_keyword(self, keyword: str):
        """添加关键词"""
        if keyword and keyword not in self.keywords:
            self.keywords.append(keyword.strip())

    def add_tag(self, tag: str):
        """添加标签"""
        if tag and tag not in self.tags:
            self.tags.append(tag.strip())

    def add_category(self, category: str):
        """添加分类"""
        if category and category not in self.categories:
            self.categories.append(category.strip())

    def add_related_memory(self, memory_id: str):
        """添加关联记忆"""
        if memory_id and memory_id not in self.related_memories:
            self.related_memories.append(memory_id)

    def set_embedding(self, embedding: list[float]):
        """设置语义向量"""
        self.embedding = embedding
        self._generate_semantic_hash()

    def calculate_similarity(self, other: "MemoryChunk") -> float:
        """计算与另一个记忆块的相似度"""
        if not self.embedding or not other.embedding:
            return 0.0

        try:
            # 计算余弦相似度
            v1 = np.array(self.embedding)
            v2 = np.array(other.embedding)

            dot_product = np.dot(v1, v2)
            norm1 = np.linalg.norm(v1)
            norm2 = np.linalg.norm(v2)

            if norm1 == 0 or norm2 == 0:
                return 0.0

            similarity = dot_product / (norm1 * norm2)
            return max(0.0, min(1.0, similarity))

        except Exception as e:
            logger.warning(f"计算记忆相似度失败: {e}")
            return 0.0

    def to_dict(self) -> dict[str, Any]:
        """转换为完整的字典格式"""
        return {
            "metadata": self.metadata.to_dict(),
            "content": self.content.to_dict(),
            "memory_type": self.memory_type.value,
            "keywords": self.keywords,
            "tags": self.tags,
            "categories": self.categories,
            "embedding": self.embedding,
            "semantic_hash": self.semantic_hash,
            "related_memories": self.related_memories,
            "temporal_context": self.temporal_context,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemoryChunk":
        """从字典创建实例"""
        metadata = MemoryMetadata.from_dict(data.get("metadata", {}))
        content = ContentStructure.from_dict(data.get("content", {}))

        chunk = cls(
            metadata=metadata,
            content=content,
            memory_type=MemoryType(data.get("memory_type", MemoryType.CONTEXTUAL.value)),
            keywords=data.get("keywords", []),
            tags=data.get("tags", []),
            categories=data.get("categories", []),
            embedding=data.get("embedding"),
            semantic_hash=data.get("semantic_hash"),
            related_memories=data.get("related_memories", []),
            temporal_context=data.get("temporal_context"),
        )

        return chunk

    def to_json(self) -> str:
        """转换为JSON字符串"""
        return orjson.dumps(self.to_dict()).decode("utf-8")

    @classmethod
    def from_json(cls, json_str: str) -> "MemoryChunk":
        """从JSON字符串创建实例"""
        try:
            data = orjson.loads(json_str)
            return cls.from_dict(data)
        except Exception as e:
            logger.error(f"从JSON创建记忆块失败: {e}")
            raise

    def is_similar_to(self, other: "MemoryChunk", threshold: float = 0.8) -> bool:
        """判断是否与另一个记忆块相似"""
        if self.semantic_hash and other.semantic_hash:
            return self.semantic_hash == other.semantic_hash

        return self.calculate_similarity(other) >= threshold

    def merge_with(self, other: "MemoryChunk") -> bool:
        """与另一个记忆块合并（如果相似）"""
        if not self.is_similar_to(other):
            return False

        try:
            # 合并关键词
            for keyword in other.keywords:
                self.add_keyword(keyword)

            # 合并标签
            for tag in other.tags:
                self.add_tag(tag)

            # 合并分类
            for category in other.categories:
                self.add_category(category)

            # 合并关联记忆
            for memory_id in other.related_memories:
                self.add_related_memory(memory_id)

            # 更新元数据
            self.metadata.last_modified = time.time()
            self.metadata.access_count += other.metadata.access_count
            self.metadata.relevance_score = max(self.metadata.relevance_score, other.metadata.relevance_score)

            # 更新置信度
            if other.metadata.confidence.value > self.metadata.confidence.value:
                self.metadata.confidence = other.metadata.confidence

            # 更新重要性
            if other.metadata.importance.value > self.metadata.importance.value:
                self.metadata.importance = other.metadata.importance

            logger.debug(f"记忆块 {self.memory_id} 合并了记忆块 {other.memory_id}")
            return True

        except Exception as e:
            logger.error(f"合并记忆块失败: {e}")
            return False

    def __str__(self) -> str:
        """字符串表示"""
        type_emoji = {
            MemoryType.PERSONAL_FACT: "👤",
            MemoryType.EVENT: "📅",
            MemoryType.PREFERENCE: "❤️",
            MemoryType.OPINION: "💭",
            MemoryType.RELATIONSHIP: "👥",
            MemoryType.EMOTION: "😊",
            MemoryType.KNOWLEDGE: "📚",
            MemoryType.SKILL: "🛠️",
            MemoryType.GOAL: "🎯",
            MemoryType.EXPERIENCE: "💡",
            MemoryType.CONTEXTUAL: "📝",
        }

        emoji = type_emoji.get(self.memory_type, "📝")
        confidence_icon = "●" * self.metadata.confidence.value
        importance_icon = "★" * self.metadata.importance.value

        return f"{emoji} [{self.memory_type.value}] {self.display} {confidence_icon} {importance_icon}"

    def __repr__(self) -> str:
        """调试表示"""
        return f"MemoryChunk(id={self.memory_id[:8]}..., type={self.memory_type.value}, user={self.user_id})"


def _build_display_text(subjects: Iterable[str], predicate: str, obj: str | dict) -> str:
    """根据主谓宾生成自然语言描述"""
    subjects_clean = [s.strip() for s in subjects if s and isinstance(s, str)]
    subject_part = "、".join(subjects_clean) if subjects_clean else "对话参与者"

    if isinstance(obj, dict):
        object_candidates = []
        for key, value in obj.items():
            if isinstance(value, str | int | float):
                object_candidates.append(f"{key}:{value}")
            elif isinstance(value, list):
                compact = "、".join(str(item) for item in value[:3])
                object_candidates.append(f"{key}:{compact}")
        object_part = "，".join(object_candidates) if object_candidates else str(obj)
    else:
        object_part = str(obj).strip()

    predicate_clean = predicate.strip()
    if not predicate_clean:
        return f"{subject_part} {object_part}".strip()

    if object_part:
        return f"{subject_part}{predicate_clean}{object_part}".strip()
    return f"{subject_part}{predicate_clean}".strip()


def create_memory_chunk(
    user_id: str,
    subject: str | list[str],
    predicate: str,
    obj: str | dict,
    memory_type: MemoryType,
    chat_id: str | None = None,
    source_context: str | None = None,
    importance: ImportanceLevel = ImportanceLevel.NORMAL,
    confidence: ConfidenceLevel = ConfidenceLevel.MEDIUM,
    display: str | None = None,
    **kwargs,
) -> MemoryChunk:
    """便捷的内存块创建函数"""
    metadata = MemoryMetadata(
        memory_id="",
        user_id=user_id,
        chat_id=chat_id,
        created_at=time.time(),
        last_accessed=0,
        last_modified=0,
        confidence=confidence,
        importance=importance,
        source_context=source_context,
    )

    subjects: list[str]
    if isinstance(subject, list):
        subjects = [s for s in subject if isinstance(s, str) and s.strip()]
        subject_payload: str | list[str] = subjects
    else:
        cleaned = subject.strip() if isinstance(subject, str) else ""
        subjects = [cleaned] if cleaned else []
        subject_payload = cleaned

    display_text = display or _build_display_text(subjects, predicate, obj)

    content = ContentStructure(subject=subject_payload, predicate=predicate, object=obj, display=display_text)

    chunk = MemoryChunk(metadata=metadata, content=content, memory_type=memory_type, **kwargs)

    return chunk


@dataclass
class MessageCollection:
    """消息集合数据结构"""

    collection_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    chat_id: str | None = None  # 聊天ID（群聊或私聊）
    messages: list[str] = field(default_factory=list)
    combined_text: str = ""
    created_at: float = field(default_factory=time.time)
    embedding: list[float] | None = None

    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式"""
        return {
            "collection_id": self.collection_id,
            "chat_id": self.chat_id,
            "messages": self.messages,
            "combined_text": self.combined_text,
            "created_at": self.created_at,
            "embedding": self.embedding,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MessageCollection":
        """从字典创建实例"""
        return cls(
            collection_id=data.get("collection_id", str(uuid.uuid4())),
            chat_id=data.get("chat_id"),
            messages=data.get("messages", []),
            combined_text=data.get("combined_text", ""),
            created_at=data.get("created_at", time.time()),
            embedding=data.get("embedding"),
        )
