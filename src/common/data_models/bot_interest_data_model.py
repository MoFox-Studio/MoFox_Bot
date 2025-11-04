"""
机器人兴趣标签数据模型
定义机器人的兴趣标签和相关的embedding数据结构
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from . import BaseDataModel


@dataclass
class BotInterestTag(BaseDataModel):
    """机器人兴趣标签"""

    tag_name: str
    weight: float = 1.0  # 权重，表示对这个兴趣的喜好程度 (0.0-1.0)
    expanded: str | None = None  # 标签的扩展描述，用于更精准的语义匹配
    embedding: list[float] | None = None  # 标签的embedding向量
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    is_active: bool = True

    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式"""
        return {
            "tag_name": self.tag_name,
            "weight": self.weight,
            "expanded": self.expanded,
            "embedding": self.embedding,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "is_active": self.is_active,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BotInterestTag":
        """从字典创建对象"""
        return cls(
            tag_name=data["tag_name"],
            weight=data.get("weight", 1.0),
            expanded=data.get("expanded"),
            embedding=data.get("embedding"),
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else datetime.now(),
            updated_at=datetime.fromisoformat(data["updated_at"]) if data.get("updated_at") else datetime.now(),
            is_active=data.get("is_active", True),
        )


@dataclass
class BotPersonalityInterests(BaseDataModel):
    """机器人人格化兴趣配置"""

    personality_id: str
    personality_description: str  # 人设描述文本
    interest_tags: list[BotInterestTag] = field(default_factory=list)
    embedding_model: str = "text-embedding-ada-002"  # 使用的embedding模型
    last_updated: datetime = field(default_factory=datetime.now)
    version: int = 1  # 版本号，用于追踪更新

    def get_active_tags(self) -> list[BotInterestTag]:
        """获取活跃的兴趣标签"""
        return [tag for tag in self.interest_tags if tag.is_active]

    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式"""
        return {
            "personality_id": self.personality_id,
            "personality_description": self.personality_description,
            "interest_tags": [tag.to_dict() for tag in self.interest_tags],
            "embedding_model": self.embedding_model,
            "last_updated": self.last_updated.isoformat(),
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BotPersonalityInterests":
        """从字典创建对象"""
        return cls(
            personality_id=data["personality_id"],
            personality_description=data["personality_description"],
            interest_tags=[BotInterestTag.from_dict(tag_data) for tag_data in data.get("interest_tags", [])],
            embedding_model=data.get("embedding_model", "text-embedding-ada-002"),
            last_updated=datetime.fromisoformat(data["last_updated"]) if data.get("last_updated") else datetime.now(),
            version=data.get("version", 1),
        )


@dataclass
class InterestMatchResult(BaseDataModel):
    """兴趣匹配结果"""

    message_id: str
    matched_tags: list[str] = field(default_factory=list)
    match_scores: dict[str, float] = field(default_factory=dict)  # tag_name -> score
    overall_score: float = 0.0
    top_tag: str | None = None
    confidence: float = 0.0  # 匹配置信度 (0.0-1.0)
    matched_keywords: list[str] = field(default_factory=list)

    def add_match(self, tag_name: str, score: float, keywords: list[str] | None = None):
        """添加匹配结果"""
        self.matched_tags.append(tag_name)
        self.match_scores[tag_name] = score
        if keywords:
            self.matched_keywords.extend(keywords)

    def calculate_overall_score(self):
        """计算总体匹配分数"""
        if not self.match_scores:
            self.overall_score = 0.0
            self.top_tag = None
            return

        # 使用加权平均计算总体分数
        total_weight = len(self.match_scores)
        if total_weight > 0:
            self.overall_score = sum(self.match_scores.values()) / total_weight
            # 设置最佳匹配标签
            self.top_tag = max(self.match_scores.items(), key=lambda x: x[1])[0]
        else:
            self.overall_score = 0.0
            self.top_tag = None

        # 计算置信度（基于匹配标签数量和分数分布）
        if len(self.match_scores) > 0:
            avg_score = self.overall_score
            score_variance = sum((score - avg_score) ** 2 for score in self.match_scores.values()) / len(
                self.match_scores
            )
            # 分数越集中，置信度越高
            self.confidence = max(0.0, 1.0 - score_variance)
        else:
            self.confidence = 0.0

    def get_top_matches(self, top_n: int = 3) -> list[tuple]:
        """获取前N个最佳匹配"""
        sorted_matches = sorted(self.match_scores.items(), key=lambda x: x[1], reverse=True)
        return sorted_matches[:top_n]
