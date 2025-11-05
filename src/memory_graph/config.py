"""
记忆图系统配置管理
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional


@dataclass
class ConsolidationConfig:
    """记忆整理配置"""

    interval_hours: int = 6  # 整理间隔（小时）
    batch_size: int = 100  # 每次处理记忆数量
    enable_auto_discovery: bool = True  # 是否启用自动关联发现
    enable_conflict_detection: bool = True  # 是否启用冲突检测


@dataclass
class RetrievalConfig:
    """记忆检索配置"""

    default_mode: str = "auto"  # auto/fast/deep
    max_expand_depth: int = 2  # 最大图扩展深度
    vector_weight: float = 0.4  # 向量相似度权重
    graph_distance_weight: float = 0.2  # 图距离权重
    importance_weight: float = 0.2  # 重要性权重
    recency_weight: float = 0.2  # 时效性权重

    def __post_init__(self):
        """验证权重总和"""
        total = self.vector_weight + self.graph_distance_weight + self.importance_weight + self.recency_weight
        if abs(total - 1.0) > 0.01:
            raise ValueError(f"权重总和必须为1.0，当前为 {total}")


@dataclass
class NodeMergerConfig:
    """节点去重配置"""

    similarity_threshold: float = 0.85  # 相似度阈值
    context_match_required: bool = True  # 是否要求上下文匹配
    merge_batch_size: int = 50  # 批量处理大小

    def __post_init__(self):
        """验证阈值范围"""
        if not 0.0 <= self.similarity_threshold <= 1.0:
            raise ValueError(f"相似度阈值必须在 [0, 1] 范围内，当前为 {self.similarity_threshold}")


@dataclass
class StorageConfig:
    """存储配置"""

    data_dir: Path = field(default_factory=lambda: Path("data/memory_graph"))
    vector_collection_name: str = "memory_nodes"
    graph_file_name: str = "memory_graph.json"
    enable_persistence: bool = True  # 是否启用持久化
    auto_save_interval: int = 300  # 自动保存间隔（秒）


@dataclass
class MemoryGraphConfig:
    """记忆图系统总配置"""

    consolidation: ConsolidationConfig = field(default_factory=ConsolidationConfig)
    retrieval: RetrievalConfig = field(default_factory=RetrievalConfig)
    node_merger: NodeMergerConfig = field(default_factory=NodeMergerConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)

    # 时间衰减配置
    decay_rates: Dict[str, float] = field(
        default_factory=lambda: {
            "EVENT": 0.05,  # 事件衰减较快
            "FACT": 0.01,  # 事实衰减慢
            "RELATION": 0.005,  # 关系衰减很慢
            "OPINION": 0.03,  # 观点中等衰减
        }
    )

    # 嵌入模型配置
    embedding_model: Optional[str] = None  # 如果为None，则使用系统默认
    embedding_dimension: int = 384  # 默认使用 sentence-transformers 的维度

    # 调试和日志
    enable_debug_logging: bool = False
    enable_visualization: bool = False  # 是否启用记忆可视化

    @classmethod
    def from_dict(cls, config_dict: Dict) -> MemoryGraphConfig:
        """从字典创建配置"""
        return cls(
            consolidation=ConsolidationConfig(**config_dict.get("consolidation", {})),
            retrieval=RetrievalConfig(**config_dict.get("retrieval", {})),
            node_merger=NodeMergerConfig(**config_dict.get("node_merger", {})),
            storage=StorageConfig(**config_dict.get("storage", {})),
            decay_rates=config_dict.get("decay_rates", cls().decay_rates),
            embedding_model=config_dict.get("embedding_model"),
            embedding_dimension=config_dict.get("embedding_dimension", 384),
            enable_debug_logging=config_dict.get("enable_debug_logging", False),
            enable_visualization=config_dict.get("enable_visualization", False),
        )

    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            "consolidation": {
                "interval_hours": self.consolidation.interval_hours,
                "batch_size": self.consolidation.batch_size,
                "enable_auto_discovery": self.consolidation.enable_auto_discovery,
                "enable_conflict_detection": self.consolidation.enable_conflict_detection,
            },
            "retrieval": {
                "default_mode": self.retrieval.default_mode,
                "max_expand_depth": self.retrieval.max_expand_depth,
                "vector_weight": self.retrieval.vector_weight,
                "graph_distance_weight": self.retrieval.graph_distance_weight,
                "importance_weight": self.retrieval.importance_weight,
                "recency_weight": self.retrieval.recency_weight,
            },
            "node_merger": {
                "similarity_threshold": self.node_merger.similarity_threshold,
                "context_match_required": self.node_merger.context_match_required,
                "merge_batch_size": self.node_merger.merge_batch_size,
            },
            "storage": {
                "data_dir": str(self.storage.data_dir),
                "vector_collection_name": self.storage.vector_collection_name,
                "graph_file_name": self.storage.graph_file_name,
                "enable_persistence": self.storage.enable_persistence,
                "auto_save_interval": self.storage.auto_save_interval,
            },
            "decay_rates": self.decay_rates,
            "embedding_model": self.embedding_model,
            "embedding_dimension": self.embedding_dimension,
            "enable_debug_logging": self.enable_debug_logging,
            "enable_visualization": self.enable_visualization,
        }


# 默认配置实例
DEFAULT_CONFIG = MemoryGraphConfig()
