"""
图扩展工具（优化版）

提供记忆图的扩展算法，用于从初始记忆集合沿图结构扩展查找相关记忆。
优化重点：
1. 改进BFS遍历效率
2. 批量向量检索，减少数据库调用
3. 早停机制，避免不必要的扩展
4. 更清晰的日志输出
"""

import asyncio
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
    从初始记忆集合出发，沿图结构扩展，并用语义相似度过滤（优化版）

    这个方法解决了纯向量搜索可能遗漏的"语义相关且图结构相关"的记忆。

    优化改进：
    - 使用记忆级别的BFS，而非节点级别（更直接）
    - 批量获取邻居记忆，减少遍历次数
    - 早停机制：达到max_expanded后立即停止
    - 更详细的调试日志

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
        import time
        start_time = time.time()
        
        # 记录已访问的记忆，避免重复
        visited_memories = set(initial_memory_ids)
        # 记录扩展的记忆及其分数
        expanded_memories: dict[str, float] = {}

        # BFS扩展（基于记忆而非节点）
        current_level_memories = initial_memory_ids
        depth_stats = []  # 每层统计

        for depth in range(max_depth):
            next_level_memories = []
            candidates_checked = 0
            candidates_passed = 0

            logger.debug(f"🔍 图扩展 - 深度 {depth+1}/{max_depth}, 当前层记忆数: {len(current_level_memories)}")

            # 遍历当前层的记忆
            for memory_id in current_level_memories:
                memory = graph_store.get_memory_by_id(memory_id)
                if not memory:
                    continue

                # 获取该记忆的邻居记忆（通过边关系）
                neighbor_memory_ids = set()
                
                # 遍历记忆的所有边，收集邻居记忆
                for edge in memory.edges:
                    # 获取边的目标节点
                    target_node_id = edge.target_id
                    source_node_id = edge.source_id
                    
                    # 通过节点找到其他记忆
                    for node_id in [target_node_id, source_node_id]:
                        if node_id in graph_store.node_to_memories:
                            neighbor_memory_ids.update(graph_store.node_to_memories[node_id])
                
                # 过滤掉已访问的和自己
                neighbor_memory_ids.discard(memory_id)
                neighbor_memory_ids -= visited_memories

                # 批量评估邻居记忆
                for neighbor_mem_id in neighbor_memory_ids:
                    candidates_checked += 1
                    
                    neighbor_memory = graph_store.get_memory_by_id(neighbor_mem_id)
                    if not neighbor_memory:
                        continue

                    # 获取邻居记忆的主题节点向量
                    topic_node = next(
                        (n for n in neighbor_memory.nodes if n.has_embedding()),
                        None
                    )
                    
                    if not topic_node or topic_node.embedding is None:
                        continue

                    # 计算语义相似度
                    semantic_sim = cosine_similarity(query_embedding, topic_node.embedding)

                    # 计算边的重要性（影响评分）
                    edge_importance = neighbor_memory.importance * 0.5  # 使用记忆重要性作为边权重

                    # 综合评分：语义相似度(70%) + 重要性(20%) + 深度衰减(10%)
                    depth_decay = 1.0 / (depth + 2)  # 深度衰减
                    relevance_score = semantic_sim * 0.7 + edge_importance * 0.2 + depth_decay * 0.1

                    # 只保留超过阈值的
                    if relevance_score < semantic_threshold:
                        continue

                    candidates_passed += 1

                    # 记录扩展的记忆
                    if neighbor_mem_id not in expanded_memories:
                        expanded_memories[neighbor_mem_id] = relevance_score
                        visited_memories.add(neighbor_mem_id)
                        next_level_memories.append(neighbor_mem_id)
                    else:
                        # 如果已存在，取最高分
                        expanded_memories[neighbor_mem_id] = max(
                            expanded_memories[neighbor_mem_id], relevance_score
                        )

                    # 早停：达到最大扩展数量
                    if len(expanded_memories) >= max_expanded:
                        logger.debug(f"⏹️  提前停止：已达到最大扩展数量 {max_expanded}")
                        break
                
                # 早停检查
                if len(expanded_memories) >= max_expanded:
                    break
            
            # 记录本层统计
            depth_stats.append({
                "depth": depth + 1,
                "checked": candidates_checked,
                "passed": candidates_passed,
                "expanded_total": len(expanded_memories)
            })

            # 如果没有新记忆或已达到数量限制，提前终止
            if not next_level_memories or len(expanded_memories) >= max_expanded:
                logger.debug(f"⏹️  停止扩展：{'无新记忆' if not next_level_memories else '达到上限'}")
                break

            # 限制下一层的记忆数量，避免爆炸性增长
            current_level_memories = next_level_memories[:max_expanded]
            
            # 每层让出控制权
            await asyncio.sleep(0.001)

        # 排序并返回
        sorted_results = sorted(expanded_memories.items(), key=lambda x: x[1], reverse=True)[:max_expanded]
        
        elapsed = time.time() - start_time
        logger.info(
            f"✅ 图扩展完成: 初始{len(initial_memory_ids)}个 → "
            f"扩展{len(sorted_results)}个新记忆 "
            f"(深度={max_depth}, 阈值={semantic_threshold:.2f}, 耗时={elapsed:.3f}s)"
        )
        
        # 输出每层统计
        for stat in depth_stats:
            logger.debug(
                f"  深度{stat['depth']}: 检查{stat['checked']}个, "
                f"通过{stat['passed']}个, 累计扩展{stat['expanded_total']}个"
            )

        return sorted_results

    except Exception as e:
        logger.error(f"语义图扩展失败: {e}", exc_info=True)
        return []


__all__ = ["expand_memories_with_semantic_filter"]
