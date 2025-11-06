"""
节点去重合并器：基于语义相似度合并重复节点
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np

from src.common.logger import get_logger
from src.config.official_configs import MemoryConfig
from src.memory_graph.models import MemoryNode, NodeType
from src.memory_graph.storage.graph_store import GraphStore
from src.memory_graph.storage.vector_store import VectorStore

logger = get_logger(__name__)


class NodeMerger:
    """
    节点合并器
    
    负责：
    1. 基于语义相似度查找重复节点
    2. 验证上下文匹配
    3. 执行节点合并操作
    """

    def __init__(
        self,
        vector_store: VectorStore,
        graph_store: GraphStore,
        config: MemoryConfig,
    ):
        """
        初始化节点合并器
        
        Args:
            vector_store: 向量存储
            graph_store: 图存储
            config: 记忆配置对象
        """
        self.vector_store = vector_store
        self.graph_store = graph_store
        self.config = config

        logger.info(
            f"初始化节点合并器: threshold={self.config.node_merger_similarity_threshold}, "
            f"context_match={self.config.node_merger_context_match_required}"
        )

    async def find_similar_nodes(
        self,
        node: MemoryNode,
        threshold: Optional[float] = None,
        limit: int = 5,
    ) -> List[Tuple[MemoryNode, float]]:
        """
        查找与指定节点相似的节点
        
        Args:
            node: 查询节点
            threshold: 相似度阈值（可选，默认使用配置值）
            limit: 返回结果数量
            
        Returns:
            List of (similar_node, similarity)
        """
        if not node.has_embedding():
            logger.warning(f"节点 {node.id} 没有 embedding，无法查找相似节点")
            return []

        threshold = threshold or self.config.node_merger_similarity_threshold

        try:
            # 在向量存储中搜索相似节点
            results = await self.vector_store.search_similar_nodes(
                query_embedding=node.embedding,
                limit=limit + 1,  # +1 因为可能包含节点自己
                node_types=[node.node_type],  # 只搜索相同类型的节点
                min_similarity=threshold,
            )

            # 过滤掉节点自己，并构建结果
            similar_nodes = []
            for node_id, similarity, metadata in results:
                if node_id == node.id:
                    continue  # 跳过自己

                # 从图存储中获取完整节点信息
                memories = self.graph_store.get_memories_by_node(node_id)
                if memories:
                    # 从第一个记忆中获取节点
                    target_node = memories[0].get_node_by_id(node_id)
                    if target_node:
                        similar_nodes.append((target_node, similarity))

            logger.debug(f"找到 {len(similar_nodes)} 个相似节点 (阈值: {threshold})")
            return similar_nodes

        except Exception as e:
            logger.error(f"查找相似节点失败: {e}", exc_info=True)
            return []

    async def should_merge(
        self,
        source_node: MemoryNode,
        target_node: MemoryNode,
        similarity: float,
    ) -> bool:
        """
        判断两个节点是否应该合并
        
        Args:
            source_node: 源节点
            target_node: 目标节点
            similarity: 语义相似度
            
        Returns:
            是否应该合并
        """
        # 1. 检查相似度阈值
        if similarity < self.config.node_merger_similarity_threshold:
            return False

        # 2. 非常高的相似度（>0.95）直接合并
        if similarity > 0.95:
            logger.debug(f"高相似度 ({similarity:.3f})，直接合并")
            return True

        # 3. 如果不要求上下文匹配，则通过相似度判断
        if not self.config.node_merger_context_match_required:
            return True

        # 4. 检查上下文匹配
        context_match = await self._check_context_match(source_node, target_node)

        if context_match:
            logger.debug(
                f"相似度 {similarity:.3f} + 上下文匹配，决定合并: "
                f"'{source_node.content}' → '{target_node.content}'"
            )
            return True

        logger.debug(
            f"相似度 {similarity:.3f} 但上下文不匹配，不合并: "
            f"'{source_node.content}' ≠ '{target_node.content}'"
        )
        return False

    async def _check_context_match(
        self,
        source_node: MemoryNode,
        target_node: MemoryNode,
    ) -> bool:
        """
        检查两个节点的上下文是否匹配
        
        上下文匹配的标准：
        1. 节点类型相同
        2. 邻居节点有重叠
        3. 邻居节点的内容相似
        
        Args:
            source_node: 源节点
            target_node: 目标节点
            
        Returns:
            是否匹配
        """
        # 1. 节点类型必须相同
        if source_node.node_type != target_node.node_type:
            return False

        # 2. 获取邻居节点
        source_neighbors = self.graph_store.get_neighbors(source_node.id, direction="both")
        target_neighbors = self.graph_store.get_neighbors(target_node.id, direction="both")

        # 如果都没有邻居，认为上下文不足，保守地不合并
        if not source_neighbors or not target_neighbors:
            return False

        # 3. 检查邻居内容是否有重叠
        source_neighbor_contents = set()
        for neighbor_id, edge_data in source_neighbors:
            neighbor_node = self._get_node_content(neighbor_id)
            if neighbor_node:
                source_neighbor_contents.add(neighbor_node.lower())

        target_neighbor_contents = set()
        for neighbor_id, edge_data in target_neighbors:
            neighbor_node = self._get_node_content(neighbor_id)
            if neighbor_node:
                target_neighbor_contents.add(neighbor_node.lower())

        # 计算重叠率
        intersection = source_neighbor_contents & target_neighbor_contents
        union = source_neighbor_contents | target_neighbor_contents

        if not union:
            return False

        overlap_ratio = len(intersection) / len(union)

        # 如果有 30% 以上的邻居重叠，认为上下文匹配
        return overlap_ratio > 0.3

    def _get_node_content(self, node_id: str) -> Optional[str]:
        """获取节点的内容"""
        memories = self.graph_store.get_memories_by_node(node_id)
        if memories:
            node = memories[0].get_node_by_id(node_id)
            if node:
                return node.content
        return None

    async def merge_nodes(
        self,
        source: MemoryNode,
        target: MemoryNode,
    ) -> bool:
        """
        合并两个节点
        
        将 source 节点的所有边转移到 target 节点，然后删除 source
        
        Args:
            source: 源节点（将被删除）
            target: 目标节点（保留）
            
        Returns:
            是否成功
        """
        try:
            logger.info(f"合并节点: '{source.content}' ({source.id}) → '{target.content}' ({target.id})")

            # 1. 在图存储中合并节点
            self.graph_store.merge_nodes(source.id, target.id)

            # 2. 在向量存储中删除源节点
            await self.vector_store.delete_node(source.id)

            # 3. 更新所有相关记忆的节点引用
            self._update_memory_references(source.id, target.id)

            logger.info(f"节点合并成功: {source.id} → {target.id}")
            return True

        except Exception as e:
            logger.error(f"节点合并失败: {e}", exc_info=True)
            return False

    def _update_memory_references(self, old_node_id: str, new_node_id: str) -> None:
        """
        更新记忆中的节点引用
        
        Args:
            old_node_id: 旧节点ID
            new_node_id: 新节点ID
        """
        # 获取所有包含旧节点的记忆
        memories = self.graph_store.get_memories_by_node(old_node_id)

        for memory in memories:
            # 移除旧节点
            memory.nodes = [n for n in memory.nodes if n.id != old_node_id]

            # 更新边的引用
            for edge in memory.edges:
                if edge.source_id == old_node_id:
                    edge.source_id = new_node_id
                if edge.target_id == old_node_id:
                    edge.target_id = new_node_id

            # 更新主体ID（如果是主体节点）
            if memory.subject_id == old_node_id:
                memory.subject_id = new_node_id

    async def batch_merge_similar_nodes(
        self,
        nodes: List[MemoryNode],
        progress_callback: Optional[callable] = None,
    ) -> dict:
        """
        批量处理节点合并
        
        Args:
            nodes: 要处理的节点列表
            progress_callback: 进度回调函数
            
        Returns:
            统计信息字典
        """
        stats = {
            "total": len(nodes),
            "checked": 0,
            "merged": 0,
            "skipped": 0,
        }

        for i, node in enumerate(nodes):
            try:
                # 只处理有 embedding 的主题和客体节点
                if not node.has_embedding() or node.node_type not in [
                    NodeType.TOPIC,
                    NodeType.OBJECT,
                ]:
                    stats["skipped"] += 1
                    continue

                # 查找相似节点
                similar_nodes = await self.find_similar_nodes(node, limit=5)

                if similar_nodes:
                    # 选择最相似的节点
                    best_match, similarity = similar_nodes[0]

                    # 判断是否应该合并
                    if await self.should_merge(node, best_match, similarity):
                        success = await self.merge_nodes(node, best_match)
                        if success:
                            stats["merged"] += 1

                stats["checked"] += 1

                # 调用进度回调
                if progress_callback:
                    progress_callback(i + 1, stats["total"], stats)

            except Exception as e:
                logger.error(f"处理节点 {node.id} 时失败: {e}", exc_info=True)
                stats["skipped"] += 1

        logger.info(
            f"批量合并完成: 总数={stats['total']}, 检查={stats['checked']}, "
            f"合并={stats['merged']}, 跳过={stats['skipped']}"
        )

        return stats

    def get_merge_candidates(
        self,
        min_similarity: float = 0.85,
        limit: int = 100,
    ) -> List[Tuple[str, str, float]]:
        """
        获取待合并的候选节点对
        
        Args:
            min_similarity: 最小相似度
            limit: 最大返回数量
            
        Returns:
            List of (node_id_1, node_id_2, similarity)
        """
        # TODO: 实现更智能的候选查找算法
        # 目前返回空列表，后续可以基于向量存储进行批量查询
        return []
