"""
图扩展工具

提供记忆图的扩展算法，用于从初始记忆集合沿图结构扩展查找相关记忆
"""

from typing import TYPE_CHECKING

from src.common.logger import get_logger
from src.memory_graph.utils.similarity import cosine_similarity

if TYPE_CHECKING:
    import numpy as np

    from src.memory_graph.storage.graph_store import GraphStore
    from src.memory_graph.storage.vector_store import VectorStore

logger = get_logger(__name__)


async def expand_memories_with_semantic_filter(
    graph_store: "GraphStore",
    vector_store: "VectorStore",
    initial_memory_ids: list[str],
    query_embedding: "np.ndarray",
    max_depth: int = 2,
    semantic_threshold: float = 0.5,
    max_expanded: int = 20,
) -> list[tuple[str, float]]:
    """
    从初始记忆集合出发，沿图结构扩展，并用语义相似度过滤

    这个方法解决了纯向量搜索可能遗漏的"语义相关且图结构相关"的记忆。

    Args:
        graph_store: 图存储
        vector_store: 向量存储
        initial_memory_ids: 初始记忆ID集合（由向量搜索得到）
        query_embedding: 查询向量
        max_depth: 最大扩展深度（1-3推荐）
        semantic_threshold: 语义相似度阈值（0.5推荐）
        max_expanded: 最多扩展多少个记忆

    Returns:
        List[(memory_id, relevance_score)] 按相关度排序
    """
    if not initial_memory_ids or query_embedding is None:
        return []

    try:
        # 记录已访问的记忆，避免重复
        visited_memories = set(initial_memory_ids)
        # 记录扩展的记忆及其分数
        expanded_memories: dict[str, float] = {}

        # BFS扩展
        current_level = initial_memory_ids

        for depth in range(max_depth):
            next_level = []

            for memory_id in current_level:
                memory = graph_store.get_memory_by_id(memory_id)
                if not memory:
                    continue

                # 遍历该记忆的所有节点
                for node in memory.nodes:
                    if not node.has_embedding():
                        continue

                    # 获取邻居节点
                    try:
                        neighbors = list(graph_store.graph.neighbors(node.id))
                    except Exception:
                        continue

                    for neighbor_id in neighbors:
                        # 获取邻居节点信息
                        neighbor_node_data = graph_store.graph.nodes.get(neighbor_id)
                        if not neighbor_node_data:
                            continue

                        # 获取邻居节点的向量（从向量存储）
                        neighbor_vector_data = await vector_store.get_node_by_id(neighbor_id)
                        if not neighbor_vector_data or neighbor_vector_data.get("embedding") is None:
                            continue

                        neighbor_embedding = neighbor_vector_data["embedding"]

                        # 计算与查询的语义相似度
                        semantic_sim = cosine_similarity(query_embedding, neighbor_embedding)

                        # 获取边的权重
                        try:
                            edge_data = graph_store.graph.get_edge_data(node.id, neighbor_id)
                            edge_importance = edge_data.get("importance", 0.5) if edge_data else 0.5
                        except Exception:
                            edge_importance = 0.5

                        # 综合评分：语义相似度(70%) + 图结构权重(20%) + 深度衰减(10%)
                        depth_decay = 1.0 / (depth + 1)  # 深度越深，权重越低
                        relevance_score = semantic_sim * 0.7 + edge_importance * 0.2 + depth_decay * 0.1

                        # 只保留超过阈值的节点
                        if relevance_score < semantic_threshold:
                            continue

                        # 提取邻居节点所属的记忆
                        neighbor_memory_ids = neighbor_node_data.get("memory_ids", [])
                        if isinstance(neighbor_memory_ids, str):
                            import json

                            try:
                                neighbor_memory_ids = json.loads(neighbor_memory_ids)
                            except Exception:
                                neighbor_memory_ids = [neighbor_memory_ids]

                        for neighbor_mem_id in neighbor_memory_ids:
                            if neighbor_mem_id in visited_memories:
                                continue

                            # 记录这个扩展记忆
                            if neighbor_mem_id not in expanded_memories:
                                expanded_memories[neighbor_mem_id] = relevance_score
                                visited_memories.add(neighbor_mem_id)
                                next_level.append(neighbor_mem_id)
                            else:
                                # 如果已存在，取最高分
                                expanded_memories[neighbor_mem_id] = max(
                                    expanded_memories[neighbor_mem_id], relevance_score
                                )

            # 如果没有新节点或已达到数量限制，提前终止
            if not next_level or len(expanded_memories) >= max_expanded:
                break

            current_level = next_level[:max_expanded]  # 限制每层的扩展数量

        # 排序并返回
        sorted_results = sorted(expanded_memories.items(), key=lambda x: x[1], reverse=True)[:max_expanded]

        logger.info(
            f"图扩展完成: 初始{len(initial_memory_ids)}个 → "
            f"扩展{len(sorted_results)}个新记忆 "
            f"(深度={max_depth}, 阈值={semantic_threshold:.2f})"
        )

        return sorted_results

    except Exception as e:
        logger.error(f"语义图扩展失败: {e}", exc_info=True)
        return []


__all__ = ["expand_memories_with_semantic_filter"]
