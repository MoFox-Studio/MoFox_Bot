"""
路径评分扩展算法

基于图路径传播的记忆检索优化方案：
1. 从向量搜索的TopK节点出发，创建初始路径
2. 沿边扩展路径，分数通过边权重和节点相似度传播
3. 路径相遇时合并，相交时剪枝
4. 最终根据路径质量对记忆评分

核心特性：
- 指数衰减的分数传播
- 动态分叉数量控制
- 路径合并与剪枝优化
- 多维度最终评分
"""

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from src.common.logger import get_logger
from src.memory_graph.utils.similarity import cosine_similarity

if TYPE_CHECKING:
    import numpy as np

    from src.memory_graph.models import Memory
    from src.memory_graph.storage.graph_store import GraphStore
    from src.memory_graph.storage.vector_store import VectorStore

logger = get_logger(__name__)


@dataclass
class Path:
    """表示一条路径"""

    nodes: list[str] = field(default_factory=list)  # 节点ID序列
    edges: list[Any] = field(default_factory=list)  # 边序列
    score: float = 0.0  # 当前路径分数
    depth: int = 0  # 路径深度
    parent: "Path | None" = None  # 父路径（用于追踪）
    is_merged: bool = False  # 是否为合并路径
    merged_from: list["Path"] = field(default_factory=list)  # 合并来源路径

    def __hash__(self):
        """使路径可哈希（基于节点序列）"""
        return hash(tuple(self.nodes))

    def get_leaf_node(self) -> str | None:
        """获取叶子节点（路径终点）"""
        return self.nodes[-1] if self.nodes else None

    def contains_node(self, node_id: str) -> bool:
        """检查路径是否包含某个节点"""
        return node_id in self.nodes


@dataclass
class PathExpansionConfig:
    """路径扩展配置"""

    max_hops: int = 2  # 最大跳数
    damping_factor: float = 0.85  # 衰减因子（PageRank风格）
    max_branches_per_node: int = 10  # 每节点最大分叉数
    path_merge_strategy: str = "weighted_geometric"  # 路径合并策略: weighted_geometric, max_bonus
    pruning_threshold: float = 0.9  # 剪枝阈值（新路径分数需达到已有路径的90%）
    high_score_threshold: float = 0.7  # 高分路径阈值
    medium_score_threshold: float = 0.4  # 中分路径阈值
    max_active_paths: int = 1000  # 最大活跃路径数（防止爆炸）
    top_paths_retain: int = 500  # 超限时保留的top路径数
    
    # 🚀 性能优化参数
    enable_early_stop: bool = True  # 启用早停（如果路径增长很少则提前结束）
    early_stop_growth_threshold: float = 0.1  # 早停阈值（路径增长率低于10%则停止）
    max_candidate_memories: int = 200  # 🆕 最大候选记忆数（在最终评分前过滤）
    min_path_count_for_memory: int = 1  # 🆕 记忆至少需要的路径数（过滤弱关联记忆）

    # 边类型权重配置
    edge_type_weights: dict[str, float] = field(
        default_factory=lambda: {
            "REFERENCE": 1.3,  # 引用关系权重最高
            "ATTRIBUTE": 1.2,  # 属性关系
            "HAS_PROPERTY": 1.2,  # 属性关系
            "RELATION": 0.9,  # 一般关系适中降权
            "TEMPORAL": 0.7,  # 时间关系降权
            "DEFAULT": 1.0,  # 默认权重
        }
    )

    # 最终评分权重
    final_scoring_weights: dict[str, float] = field(
        default_factory=lambda: {
            "path_score": 0.50,  # 路径分数占50%
            "importance": 0.30,  # 重要性占30%
            "recency": 0.20,  # 时效性占20%
        }
    )


class PathScoreExpansion:
    """路径评分扩展算法实现"""

    def __init__(
        self,
        graph_store: "GraphStore",
        vector_store: "VectorStore",
        config: PathExpansionConfig | None = None,
    ):
        """
        初始化路径扩展器

        Args:
            graph_store: 图存储
            vector_store: 向量存储
            config: 扩展配置
        """
        self.graph_store = graph_store
        self.vector_store = vector_store
        self.config = config or PathExpansionConfig()
        self.prefer_node_types: list[str] = []  # 🆕 偏好节点类型
        
        # 🚀 性能优化：邻居边缓存
        self._neighbor_cache: dict[str, list[Any]] = {}
        self._node_score_cache: dict[str, float] = {}

        logger.info(
            f"PathScoreExpansion 初始化: max_hops={self.config.max_hops}, "
            f"damping={self.config.damping_factor}, "
            f"merge_strategy={self.config.path_merge_strategy}"
        )

    async def expand_with_path_scoring(
        self,
        initial_nodes: list[tuple[str, float, dict[str, Any]]],  # (node_id, score, metadata)
        query_embedding: "np.ndarray | None",
        top_k: int = 20,
        prefer_node_types: list[str] | None = None,  # 🆕 偏好节点类型
    ) -> list[tuple[Any, float, list[Path]]]:
        """
        使用路径评分进行图扩展

        Args:
            initial_nodes: 初始节点列表（来自向量搜索）
            query_embedding: 查询向量（用于计算节点相似度）
            top_k: 返回的top记忆数量
            prefer_node_types: 偏好节点类型列表（由LLM识别），如 ["EVENT", "ENTITY"]

        Returns:
            [(Memory, final_score, contributing_paths), ...]
        """
        start_time = time.time()

        if not initial_nodes:
            logger.warning("初始节点为空，无法进行路径扩展")
            return []

        # 🚀 清空缓存（每次查询重新开始）
        self._neighbor_cache.clear()
        self._node_score_cache.clear()

        # 保存偏好类型
        self.prefer_node_types = prefer_node_types or []
        if self.prefer_node_types:
            logger.info(f"🎯 偏好节点类型: {self.prefer_node_types}")

        # 1. 初始化路径
        active_paths = []
        best_score_to_node: dict[str, float] = {}  # 记录每个节点的最佳到达分数

        for node_id, score, metadata in initial_nodes:
            path = Path(nodes=[node_id], edges=[], score=score, depth=0)
            active_paths.append(path)
            best_score_to_node[node_id] = score

        logger.info(f"🚀 路径扩展开始: {len(active_paths)} 条初始路径")

        # 2. 多跳扩展
        hop_stats = []  # 每跳统计信息

        for hop in range(self.config.max_hops):
            hop_start = time.time()
            next_paths = []
            branches_created = 0
            paths_merged = 0
            paths_pruned = 0

            # 🚀 性能优化：收集所有需要评分的候选节点，然后批量计算
            candidate_nodes_for_batch = set()
            path_candidates: list[tuple[Path, Any, str, float]] = []  # (path, edge, next_node, edge_weight)

            # 第一阶段：收集所有候选节点
            for path in active_paths:
                current_node = path.get_leaf_node()
                if not current_node:
                    continue

                # 获取排序后的邻居边
                neighbor_edges = await self._get_sorted_neighbor_edges(current_node)

                # 动态计算最大分叉数
                max_branches = self._calculate_max_branches(path.score)
                branch_count = 0

                for edge in neighbor_edges[:max_branches]:
                    next_node = edge.target_id if edge.source_id == current_node else edge.source_id

                    # 避免环路
                    if path.contains_node(next_node):
                        continue

                    edge_weight = self._get_edge_weight(edge)
                    
                    # 记录候选
                    path_candidates.append((path, edge, next_node, edge_weight))
                    candidate_nodes_for_batch.add(next_node)
                    
                    branch_count += 1
                    if branch_count >= max_branches:
                        break

            # 🚀 第二阶段：批量计算所有候选节点的分数
            if candidate_nodes_for_batch:
                batch_node_scores = await self._batch_get_node_scores(
                    list(candidate_nodes_for_batch), query_embedding
                )
            else:
                batch_node_scores = {}

            # 🚀 第三阶段：使用批量计算的分数创建路径
            for path, edge, next_node, edge_weight in path_candidates:
                node_score = batch_node_scores.get(next_node, 0.3)

                new_score = self._calculate_path_score(
                    old_score=path.score,
                    edge_weight=edge_weight,
                    node_score=node_score,
                    depth=hop + 1,
                )

                # 剪枝：如果到达该节点的分数远低于已有最优路径，跳过
                if next_node in best_score_to_node:
                    if new_score < best_score_to_node[next_node] * self.config.pruning_threshold:
                        paths_pruned += 1
                        continue

                # 更新最佳分数
                best_score_to_node[next_node] = max(best_score_to_node.get(next_node, 0), new_score)

                # 创建新路径
                new_path = Path(
                    nodes=path.nodes + [next_node],
                    edges=path.edges + [edge],
                    score=new_score,
                    depth=hop + 1,
                    parent=path,
                )

                # 尝试路径合并
                merged_path = self._try_merge_paths(new_path, next_paths)
                if merged_path:
                    next_paths.append(merged_path)
                    paths_merged += 1
                else:
                    next_paths.append(new_path)

                branches_created += 1

            # 路径数量控制：如果爆炸性增长，保留高分路径
            if len(next_paths) > self.config.max_active_paths:
                logger.warning(
                    f"⚠️  路径数量超限 ({len(next_paths)} > {self.config.max_active_paths})，"
                    f"保留 top {self.config.top_paths_retain}"
                )
                next_paths = sorted(next_paths, key=lambda p: p.score, reverse=True)[
                    : self.config.top_paths_retain
                ]

            # 🚀 早停检测：如果路径增长很少，提前终止
            prev_path_count = len(active_paths)
            active_paths = next_paths
            
            if self.config.enable_early_stop and prev_path_count > 0:
                growth_rate = (len(active_paths) - prev_path_count) / prev_path_count
                if growth_rate < self.config.early_stop_growth_threshold:
                    logger.info(
                        f"⏸️  早停触发: 路径增长率 {growth_rate:.2%} < {self.config.early_stop_growth_threshold:.0%}, "
                        f"在第 {hop+1}/{self.config.max_hops} 跳停止"
                    )
                    hop_time = time.time() - hop_start
                    hop_stats.append(
                        {
                            "hop": hop + 1,
                            "paths": len(active_paths),
                            "branches": branches_created,
                            "merged": paths_merged,
                            "pruned": paths_pruned,
                            "time": hop_time,
                            "early_stopped": True,
                        }
                    )
                    break

            hop_time = time.time() - hop_start
            hop_stats.append(
                {
                    "hop": hop + 1,
                    "paths": len(active_paths),
                    "branches": branches_created,
                    "merged": paths_merged,
                    "pruned": paths_pruned,
                    "time": hop_time,
                }
            )

            logger.debug(
                f"  Hop {hop+1}/{self.config.max_hops}: "
                f"{len(active_paths)} 条路径, "
                f"{branches_created} 分叉, "
                f"{paths_merged} 合并, "
                f"{paths_pruned} 剪枝, "
                f"{hop_time:.3f}s"
            )

            # 早停：如果没有新路径
            if not active_paths:
                logger.info(f"⏹️  提前停止：第 {hop+1} 跳无新路径")
                break

        # 3. 提取叶子路径（最小子路径）
        leaf_paths = self._extract_leaf_paths(active_paths)
        logger.info(f"📊 提取 {len(leaf_paths)} 条叶子路径")

        # 4. 路径到记忆的映射
        memory_paths = await self._map_paths_to_memories(leaf_paths)
        logger.info(f"🔗 映射到 {len(memory_paths)} 条候选记忆")

        # 🚀 4.5. 粗排过滤：在详细评分前过滤掉低质量记忆
        if len(memory_paths) > self.config.max_candidate_memories:
            # 按路径数量和路径最大分数进行粗排
            memory_scores_rough = []
            for mem_id, (memory, paths) in memory_paths.items():
                # 简单评分：路径数量 × 最高路径分数 × 重要性
                max_path_score = max(p.score for p in paths) if paths else 0
                rough_score = len(paths) * max_path_score * memory.importance
                memory_scores_rough.append((mem_id, rough_score))
            
            # 保留top候选
            memory_scores_rough.sort(key=lambda x: x[1], reverse=True)
            retained_mem_ids = set(mem_id for mem_id, _ in memory_scores_rough[:self.config.max_candidate_memories])
            
            # 过滤
            memory_paths = {
                mem_id: (memory, paths)
                for mem_id, (memory, paths) in memory_paths.items()
                if mem_id in retained_mem_ids
            }
            
            logger.info(
                f"⚡ 粗排过滤: {len(memory_scores_rough)} → {len(memory_paths)} 条候选记忆"
            )

        # 5. 最终评分
        scored_memories = await self._final_scoring(memory_paths)

        # 6. 排序并返回TopK
        scored_memories.sort(key=lambda x: x[1], reverse=True)
        result = scored_memories[:top_k]

        elapsed = time.time() - start_time
        logger.info(
            f"✅ 路径扩展完成: {len(initial_nodes)} 个初始节点 → "
            f"{len(result)} 条记忆 (耗时 {elapsed:.3f}s)"
        )

        # 输出每跳统计
        for stat in hop_stats:
            logger.debug(
                f"  统计 Hop{stat['hop']}: {stat['paths']}路径, "
                f"{stat['branches']}分叉, {stat['merged']}合并, "
                f"{stat['pruned']}剪枝, {stat['time']:.3f}s"
            )

        return result

    async def _get_sorted_neighbor_edges(self, node_id: str) -> list[Any]:
        """
        获取节点的排序邻居边（按边权重排序）- 带缓存优化

        Args:
            node_id: 节点ID

        Returns:
            排序后的边列表
        """
        # 🚀 缓存检查
        if node_id in self._neighbor_cache:
            return self._neighbor_cache[node_id]
        
        edges = []

        # 从图存储中获取与该节点相关的所有边
        # 需要遍历所有记忆找到包含该节点的边
        for memory_id in self.graph_store.node_to_memories.get(node_id, []):
            memory = self.graph_store.get_memory_by_id(memory_id)
            if memory:
                for edge in memory.edges:
                    if edge.source_id == node_id or edge.target_id == node_id:
                        edges.append(edge)

        # 去重（同一条边可能出现多次）
        unique_edges = list({(e.source_id, e.target_id, e.edge_type): e for e in edges}.values())

        # 按边权重排序
        unique_edges.sort(key=lambda e: self._get_edge_weight(e), reverse=True)

        # 🚀 存入缓存
        self._neighbor_cache[node_id] = unique_edges

        return unique_edges

    def _get_edge_weight(self, edge: Any) -> float:
        """
        获取边的权重

        Args:
            edge: 边对象

        Returns:
            边权重
        """
        # 基础权重：边自身的重要性
        base_weight = getattr(edge, "importance", 0.5)

        # 边类型权重
        edge_type_str = edge.edge_type.value if hasattr(edge.edge_type, "value") else str(edge.edge_type)
        type_weight = self.config.edge_type_weights.get(edge_type_str, self.config.edge_type_weights["DEFAULT"])

        # 综合权重
        return base_weight * type_weight

    async def _get_node_score(self, node_id: str, query_embedding: "np.ndarray | None") -> float:
        """
        获取节点分数（基于与查询的相似度 + 偏好类型加成）

        Args:
            node_id: 节点ID
            query_embedding: 查询向量

        Returns:
            节点分数（0.0-1.0，偏好类型节点可能超过1.0）
        """
        # 从向量存储获取节点数据
        node_data = await self.vector_store.get_node_by_id(node_id)
        
        if query_embedding is None:
            base_score = 0.5  # 默认中等分数
        else:
            if not node_data or "embedding" not in node_data:
                base_score = 0.3  # 无向量的节点给低分
            else:
                node_embedding = node_data["embedding"]
                similarity = cosine_similarity(query_embedding, node_embedding)
                base_score = max(0.0, min(1.0, similarity))  # 限制在[0, 1]

        # 🆕 偏好类型加成
        if self.prefer_node_types and node_data:
            metadata = node_data.get("metadata", {})
            node_type = metadata.get("node_type")
            if node_type and node_type in self.prefer_node_types:
                # 给予20%的分数加成
                bonus = base_score * 0.2
                logger.debug(f"节点 {node_id[:8]} 类型 {node_type} 匹配偏好，加成 {bonus:.3f}")
                return base_score + bonus

        return base_score

    async def _batch_get_node_scores(
        self, node_ids: list[str], query_embedding: "np.ndarray | None"
    ) -> dict[str, float]:
        """
        批量获取节点分数（性能优化版本）

        Args:
            node_ids: 节点ID列表
            query_embedding: 查询向量

        Returns:
            {node_id: score} 字典
        """
        import numpy as np

        scores = {}
        
        if query_embedding is None:
            # 无查询向量时，返回默认分数
            return {nid: 0.5 for nid in node_ids}
        
        # 批量获取节点数据
        node_data_list = await asyncio.gather(
            *[self.vector_store.get_node_by_id(nid) for nid in node_ids],
            return_exceptions=True
        )
        
        # 收集有效的嵌入向量
        valid_embeddings = []
        valid_node_ids = []
        node_metadata_map = {}
        
        for nid, node_data in zip(node_ids, node_data_list):
            if isinstance(node_data, Exception):
                scores[nid] = 0.3
                continue
            
            # 类型守卫：确保 node_data 是字典
            if not node_data or not isinstance(node_data, dict) or "embedding" not in node_data:
                scores[nid] = 0.3
            else:
                valid_embeddings.append(node_data["embedding"])
                valid_node_ids.append(nid)
                node_metadata_map[nid] = node_data.get("metadata", {})
        
        if valid_embeddings:
            # 批量计算相似度（使用矩阵运算）
            embeddings_matrix = np.array(valid_embeddings)
            query_norm = np.linalg.norm(query_embedding)
            embeddings_norms = np.linalg.norm(embeddings_matrix, axis=1)
            
            # 向量化计算余弦相似度
            similarities = np.dot(embeddings_matrix, query_embedding) / (embeddings_norms * query_norm + 1e-8)
            similarities = np.clip(similarities, 0.0, 1.0)
            
            # 应用偏好类型加成
            for nid, sim in zip(valid_node_ids, similarities):
                base_score = float(sim)
                
                # 偏好类型加成
                if self.prefer_node_types and nid in node_metadata_map:
                    node_type = node_metadata_map[nid].get("node_type")
                    if node_type and node_type in self.prefer_node_types:
                        bonus = base_score * 0.2
                        scores[nid] = base_score + bonus
                    else:
                        scores[nid] = base_score
                else:
                    scores[nid] = base_score
        
        return scores

    def _calculate_path_score(self, old_score: float, edge_weight: float, node_score: float, depth: int) -> float:
        """
        计算路径分数（核心公式）

        使用指数衰减 + 边权重传播 + 节点分数注入

        Args:
            old_score: 旧路径分数
            edge_weight: 边权重
            node_score: 新节点分数
            depth: 当前深度

        Returns:
            新路径分数
        """
        # 指数衰减因子
        decay = self.config.damping_factor**depth

        # 传播分数：旧分数 × 边权重 × 衰减
        propagated_score = old_score * edge_weight * decay

        # 新鲜分数：节点分数 × (1 - 衰减)
        fresh_score = node_score * (1 - decay)

        return propagated_score + fresh_score

    def _calculate_max_branches(self, path_score: float) -> int:
        """
        动态计算最大分叉数

        Args:
            path_score: 路径分数

        Returns:
            最大分叉数
        """
        if path_score > self.config.high_score_threshold:
            return int(self.config.max_branches_per_node * 1.5)  # 高分路径多探索
        elif path_score > self.config.medium_score_threshold:
            return self.config.max_branches_per_node
        else:
            return int(self.config.max_branches_per_node * 0.5)  # 低分路径少探索

    def _try_merge_paths(self, new_path: Path, existing_paths: list[Path]) -> Path | None:
        """
        尝试路径合并（端点相遇）

        Args:
            new_path: 新路径
            existing_paths: 现有路径列表

        Returns:
            合并后的路径，如果不合并则返回 None
        """
        endpoint = new_path.get_leaf_node()
        if not endpoint:
            return None

        for existing in existing_paths:
            if existing.get_leaf_node() == endpoint and not existing.is_merged:
                # 端点相遇，合并路径
                merged_score = self._merge_score(new_path.score, existing.score)

                merged_path = Path(
                    nodes=new_path.nodes,  # 保留新路径的节点序列
                    edges=new_path.edges,
                    score=merged_score,
                    depth=new_path.depth,
                    parent=new_path.parent,
                    is_merged=True,
                    merged_from=[new_path, existing],
                )

                # 从现有列表中移除被合并的路径
                existing_paths.remove(existing)

                logger.debug(f"🔀 路径合并: {new_path.score:.3f} + {existing.score:.3f} → {merged_score:.3f}")

                return merged_path

        return None

    def _merge_score(self, score1: float, score2: float) -> float:
        """
        合并两条路径的分数

        Args:
            score1: 路径1分数
            score2: 路径2分数

        Returns:
            合并后分数
        """
        if self.config.path_merge_strategy == "weighted_geometric":
            # 几何平均 × 1.2 加成
            return (score1 * score2) ** 0.5 * 1.2
        elif self.config.path_merge_strategy == "max_bonus":
            # 取最大值 × 1.3 加成
            return max(score1, score2) * 1.3
        else:
            # 默认：算术平均 × 1.15 加成
            return (score1 + score2) / 2 * 1.15

    def _extract_leaf_paths(self, all_paths: list[Path]) -> list[Path]:
        """
        提取叶子路径（最小子路径）

        Args:
            all_paths: 所有路径

        Returns:
            叶子路径列表
        """
        # 构建父子关系映射
        children_map: dict[int, list[Path]] = {}
        for path in all_paths:
            if path.parent:
                parent_id = id(path.parent)
                if parent_id not in children_map:
                    children_map[parent_id] = []
                children_map[parent_id].append(path)

        # 提取没有子路径的路径
        leaf_paths = [p for p in all_paths if id(p) not in children_map]

        return leaf_paths

    async def _map_paths_to_memories(self, paths: list[Path]) -> dict[str, tuple[Any, list[Path]]]:
        """
        将路径映射到记忆 - 优化版

        Args:
            paths: 路径列表

        Returns:
            {memory_id: (Memory, [contributing_paths])}
        """
        # 使用临时字典存储路径列表
        temp_paths: dict[str, list[Path]] = {}
        temp_memories: dict[str, Any] = {}  # 存储 Memory 对象
        
        # 🚀 性能优化：收集所有需要获取的记忆ID，然后批量获取
        all_memory_ids = set()
        path_to_memory_ids: dict[int, set[str]] = {}  # path对象id -> 记忆ID集合

        for path in paths:
            memory_ids_in_path = set()
            
            # 收集路径中所有节点涉及的记忆
            for node_id in path.nodes:
                memory_ids = self.graph_store.node_to_memories.get(node_id, [])
                memory_ids_in_path.update(memory_ids)
            
            all_memory_ids.update(memory_ids_in_path)
            path_to_memory_ids[id(path)] = memory_ids_in_path

        # 🚀 批量获取记忆对象（如果graph_store支持批量获取）
        # 注意：这里假设逐个获取，如果有批量API可以进一步优化
        memory_cache: dict[str, Any] = {}
        for mem_id in all_memory_ids:
            memory = self.graph_store.get_memory_by_id(mem_id)
            if memory:
                memory_cache[mem_id] = memory
        
        # 构建映射关系
        for path in paths:
            memory_ids_in_path = path_to_memory_ids[id(path)]
            
            for mem_id in memory_ids_in_path:
                if mem_id in memory_cache:
                    if mem_id not in temp_paths:
                        temp_paths[mem_id] = []
                        temp_memories[mem_id] = memory_cache[mem_id]
                    temp_paths[mem_id].append(path)

        # 构建最终返回的字典
        memory_paths: dict[str, tuple[Any, list[Path]]] = {
            mem_id: (temp_memories[mem_id], paths_list)
            for mem_id, paths_list in temp_paths.items()
        }

        return memory_paths

    async def _final_scoring(
        self, memory_paths: dict[str, tuple[Any, list[Path]]]
    ) -> list[tuple[Any, float, list[Path]]]:
        """
        最终评分（结合路径分数、重要性、时效性 + 偏好类型加成）- 优化版

        Args:
            memory_paths: 记忆ID到(记忆, 路径列表)的映射

        Returns:
            [(Memory, final_score, paths), ...]
        """
        scored_memories = []
        
        # 🚀 性能优化：如果需要偏好类型加成，批量预加载所有节点的类型信息
        node_type_cache: dict[str, str | None] = {}
        
        if self.prefer_node_types:
            # 收集所有需要查询的节点ID
            all_node_ids = set()
            for memory, _ in memory_paths.values():
                memory_nodes = getattr(memory, "nodes", [])
                for node in memory_nodes:
                    node_id = node.id if hasattr(node, "id") else str(node)
                    all_node_ids.add(node_id)
            
            # 批量获取节点数据
            if all_node_ids:
                logger.debug(f"🔍 批量预加载 {len(all_node_ids)} 个节点的类型信息")
                node_data_list = await asyncio.gather(
                    *[self.vector_store.get_node_by_id(nid) for nid in all_node_ids],
                    return_exceptions=True
                )
                
                # 构建类型缓存
                for nid, node_data in zip(all_node_ids, node_data_list):
                    if isinstance(node_data, Exception) or not node_data or not isinstance(node_data, dict):
                        node_type_cache[nid] = None
                    else:
                        metadata = node_data.get("metadata", {})
                        node_type_cache[nid] = metadata.get("node_type")

        # 遍历所有记忆进行评分
        for mem_id, (memory, paths) in memory_paths.items():
            # 1. 聚合路径分数
            path_score = self._aggregate_path_scores(paths)

            # 2. 计算重要性分数
            importance_score = memory.importance

            # 3. 计算时效性分数
            recency_score = self._calculate_recency(memory)

            # 4. 综合评分
            weights = self.config.final_scoring_weights
            base_final_score = (
                path_score * weights["path_score"]
                + importance_score * weights["importance"]
                + recency_score * weights["recency"]
            )

            # 🆕 5. 偏好类型加成（使用缓存的类型信息）
            type_bonus = 0.0
            if self.prefer_node_types and node_type_cache:
                memory_nodes = getattr(memory, "nodes", [])
                if memory_nodes:
                    # 使用缓存快速计算匹配数
                    matched_count = 0
                    for node in memory_nodes:
                        node_id = node.id if hasattr(node, "id") else str(node)
                        node_type = node_type_cache.get(node_id)
                        if node_type and node_type in self.prefer_node_types:
                            matched_count += 1
                    
                    if matched_count > 0:
                        match_ratio = matched_count / len(memory_nodes)
                        # 根据匹配比例给予加成（最高10%）
                        type_bonus = base_final_score * match_ratio * 0.1
                        logger.debug(
                            f"记忆 {mem_id[:8]} 包含 {matched_count}/{len(memory_nodes)} 个偏好类型节点，"
                            f"加成 {type_bonus:.3f}"
                        )

            final_score = base_final_score + type_bonus
            scored_memories.append((memory, final_score, paths))

        return scored_memories

    def _aggregate_path_scores(self, paths: list[Path]) -> float:
        """
        聚合多条路径的分数

        Args:
            paths: 路径列表

        Returns:
            聚合分数
        """
        if not paths:
            return 0.0

        # 方案A: 总分（路径越多，分数越高）
        total_score = sum(p.score for p in paths)

        # 方案B: Top-K平均（关注最优路径）
        top3 = sorted(paths, key=lambda p: p.score, reverse=True)[:3]
        avg_top = sum(p.score for p in top3) / len(top3) if top3 else 0.0

        # 组合：40% 总分 + 60% Top均分
        return total_score * 0.4 + avg_top * 0.6

    def _calculate_recency(self, memory: Any) -> float:
        """
        计算时效性分数

        Args:
            memory: 记忆对象

        Returns:
            时效性分数 [0, 1]
        """
        now = datetime.now(timezone.utc)

        # 确保时间有时区信息
        if memory.created_at.tzinfo is None:
            memory_time = memory.created_at.replace(tzinfo=timezone.utc)
        else:
            memory_time = memory.created_at

        # 计算天数差
        age_days = (now - memory_time).total_seconds() / 86400

        # 30天半衰期
        recency_score = 1.0 / (1.0 + age_days / 30)

        return recency_score


__all__ = ["PathScoreExpansion", "PathExpansionConfig", "Path"]
