"""
记忆图系统核心数据模型

定义节点、边、记忆等核心数据结构
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

import numpy as np


class NodeType(Enum):
    """节点类型枚举"""

    SUBJECT = "主体"  # 记忆的主语（我、小明、老师）
    TOPIC = "主题"  # 动作或状态（吃饭、情绪、学习）
    OBJECT = "客体"  # 宾语（白米饭、学校、书）
    ATTRIBUTE = "属性"  # 延伸属性（时间、地点、原因）
    VALUE = "值"  # 属性的具体值（2025-11-05、不开心）


class MemoryType(Enum):
    """记忆类型枚举"""

    EVENT = "事件"  # 有时间点的动作
    FACT = "事实"  # 相对稳定的状态
    RELATION = "关系"  # 人际关系
    OPINION = "观点"  # 主观评价


class EdgeType(Enum):
    """边类型枚举"""

    MEMORY_TYPE = "记忆类型"  # 主体 → 主题
    CORE_RELATION = "核心关系"  # 主题 → 客体（是/做/有）
    ATTRIBUTE = "属性关系"  # 任意节点 → 属性
    CAUSALITY = "因果关系"  # 记忆 → 记忆
    REFERENCE = "引用关系"  # 记忆 → 记忆（转述）
    RELATION = "关联关系"  # 记忆 → 记忆（自动关联发现的关系）


class MemoryStatus(Enum):
    """记忆状态枚举"""

    STAGED = "staged"  # 临时状态，未整理
    CONSOLIDATED = "consolidated"  # 已整理
    ARCHIVED = "archived"  # 已归档（低价值，很少访问）


@dataclass
class MemoryNode:
    """记忆节点"""

    id: str  # 节点唯一ID
    content: str  # 节点内容（如："我"、"吃饭"、"白米饭"）
    node_type: NodeType  # 节点类型
    embedding: np.ndarray | None = None  # 语义向量（仅主题/客体需要）
    metadata: dict[str, Any] = field(default_factory=dict)  # 扩展元数据
    created_at: datetime = field(default_factory=datetime.now)

    def __post_init__(self):
        """后初始化处理"""
        if not self.id:
            self.id = str(uuid.uuid4())

    def to_dict(self) -> dict[str, Any]:
        """转换为字典（用于序列化）"""
        # 不序列化 embedding 数据，向量数据由专门的向量数据库管理
        return {
            "id": self.id,
            "content": self.content,
            "node_type": self.node_type.value,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemoryNode:
        """从字典创建节点"""
        # 不从 JSON 中读取 embedding 数据，向量数据由专门的向量数据库管理
        return cls(
            id=data["id"],
            content=data["content"],
            node_type=NodeType(data["node_type"]),
            embedding=None,  # 向量数据需要从向量数据库中单独加载
            metadata=data.get("metadata", {}),
            created_at=datetime.fromisoformat(data["created_at"]),
        )

    def has_embedding(self) -> bool:
        """是否有语义向量"""
        return self.embedding is not None

    def __str__(self) -> str:
        return f"Node({self.node_type.value}: {self.content})"


@dataclass
class MemoryEdge:
    """记忆边（节点之间的关系）"""

    id: str  # 边唯一ID
    source_id: str  # 源节点ID
    target_id: str  # 目标节点ID（或目标记忆ID）
    relation: str  # 关系名称（如："是"、"做"、"时间"、"因为"）
    edge_type: EdgeType  # 边类型
    importance: float = 0.5  # 重要性 [0-1]
    metadata: dict[str, Any] = field(default_factory=dict)  # 扩展元数据
    created_at: datetime = field(default_factory=datetime.now)

    def __post_init__(self):
        """后初始化处理"""
        if not self.id:
            self.id = str(uuid.uuid4())
        # 确保重要性在有效范围内
        self.importance = max(0.0, min(1.0, self.importance))

    def to_dict(self) -> dict[str, Any]:
        """转换为字典（用于序列化）"""
        return {
            "id": self.id,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "relation": self.relation,
            "edge_type": self.edge_type.value,
            "importance": self.importance,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemoryEdge:
        """从字典创建边"""
        return cls(
            id=data["id"],
            source_id=data["source_id"],
            target_id=data["target_id"],
            relation=data["relation"],
            edge_type=EdgeType(data["edge_type"]),
            importance=data.get("importance", 0.5),
            metadata=data.get("metadata", {}),
            created_at=datetime.fromisoformat(data["created_at"]),
        )

    def __str__(self) -> str:
        return f"Edge({self.source_id} --{self.relation}--> {self.target_id})"


@dataclass
class Memory:
    """完整记忆（由节点和边组成的子图）"""

    id: str  # 记忆唯一ID
    subject_id: str  # 主体节点ID
    memory_type: MemoryType  # 记忆类型
    nodes: list[MemoryNode]  # 该记忆包含的所有节点
    edges: list[MemoryEdge]  # 该记忆包含的所有边
    importance: float = 0.5  # 整体重要性 [0-1]
    activation: float = 0.0  # 激活度 [0-1]，用于记忆整合和遗忘
    status: MemoryStatus = MemoryStatus.STAGED  # 记忆状态
    created_at: datetime = field(default_factory=datetime.now)
    last_accessed: datetime = field(default_factory=datetime.now)  # 最后访问时间
    access_count: int = 0  # 访问次数
    decay_factor: float = 1.0  # 衰减因子（随时间变化）
    metadata: dict[str, Any] = field(default_factory=dict)  # 扩展元数据

    def __post_init__(self):
        """后初始化处理"""
        if not self.id:
            self.id = str(uuid.uuid4())
        # 确保重要性和激活度在有效范围内
        self.importance = max(0.0, min(1.0, self.importance))
        self.activation = max(0.0, min(1.0, self.activation))

    def to_dict(self) -> dict[str, Any]:
        """转换为字典（用于序列化）"""
        return {
            "id": self.id,
            "subject_id": self.subject_id,
            "memory_type": self.memory_type.value,
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
            "importance": self.importance,
            "activation": self.activation,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "last_accessed": self.last_accessed.isoformat(),
            "access_count": self.access_count,
            "decay_factor": self.decay_factor,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Memory:
        """从字典创建记忆"""
        metadata = data.get("metadata", {})

        # 优先从 metadata 中获取激活度信息
        activation_level = 0.0
        activation_info = metadata.get("activation", {})
        if activation_info and "level" in activation_info:
            activation_level = activation_info["level"]
        else:
            # 备选：使用直接的 activation 字段
            activation_level = data.get("activation", 0.0)

        return cls(
            id=data["id"],
            subject_id=data["subject_id"],
            memory_type=MemoryType(data["memory_type"]),
            nodes=[MemoryNode.from_dict(n) for n in data["nodes"]],
            edges=[MemoryEdge.from_dict(e) for e in data["edges"]],
            importance=data.get("importance", 0.5),
            activation=activation_level,  # 使用统一的激活度值
            status=MemoryStatus(data.get("status", "staged")),
            created_at=datetime.fromisoformat(data["created_at"]),
            last_accessed=datetime.fromisoformat(data.get("last_accessed", data["created_at"])),
            access_count=data.get("access_count", 0),
            decay_factor=data.get("decay_factor", 1.0),
            metadata=metadata,
        )

    def update_access(self) -> None:
        """更新访问记录"""
        self.last_accessed = datetime.now()
        self.access_count += 1

    def get_node_by_id(self, node_id: str) -> MemoryNode | None:
        """根据ID获取节点"""
        for node in self.nodes:
            if node.id == node_id:
                return node
        return None

    def get_subject_node(self) -> MemoryNode | None:
        """获取主体节点"""
        return self.get_node_by_id(self.subject_id)

    def to_text(self) -> str:
        """转换为文本描述（用于显示和LLM处理）"""
        subject_node = self.get_subject_node()
        if not subject_node:
            return f"[记忆 {self.id[:8]}]"

        # 简单的文本生成逻辑
        parts = [f"{subject_node.content}"]

        # 查找主题节点（通过记忆类型边连接）
        topic_node = None
        for edge in self.edges:
            if edge.edge_type == EdgeType.MEMORY_TYPE and edge.source_id == self.subject_id:
                topic_node = self.get_node_by_id(edge.target_id)
                break

        if topic_node:
            parts.append(topic_node.content)

            # 查找客体节点（通过核心关系边连接）
            for edge in self.edges:
                if edge.edge_type == EdgeType.CORE_RELATION and edge.source_id == topic_node.id:
                    obj_node = self.get_node_by_id(edge.target_id)
                    if obj_node:
                        parts.append(f"{edge.relation} {obj_node.content}")
                        break

        return " ".join(parts)

    def __str__(self) -> str:
        return f"Memory({self.memory_type.value}: {self.to_text()})"


@dataclass
class StagedMemory:
    """临时记忆（未整理状态）"""

    memory: Memory  # 原始记忆对象
    status: MemoryStatus = MemoryStatus.STAGED  # 状态
    created_at: datetime = field(default_factory=datetime.now)
    consolidated_at: datetime | None = None  # 整理时间
    merge_history: list[str] = field(default_factory=list)  # 被合并的节点ID列表

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "memory": self.memory.to_dict(),
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "consolidated_at": self.consolidated_at.isoformat() if self.consolidated_at else None,
            "merge_history": self.merge_history,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StagedMemory:
        """从字典创建临时记忆"""
        return cls(
            memory=Memory.from_dict(data["memory"]),
            status=MemoryStatus(data.get("status", "staged")),
            created_at=datetime.fromisoformat(data["created_at"]),
            consolidated_at=datetime.fromisoformat(data["consolidated_at"]) if data.get("consolidated_at") else None,
            merge_history=data.get("merge_history", []),
        )
