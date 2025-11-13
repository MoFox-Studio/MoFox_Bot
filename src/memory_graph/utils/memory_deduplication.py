"""
记忆去重与聚合工具

用于在检索结果中识别并合并相似的记忆，提高结果质量
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.common.logger import get_logger
from src.memory_graph.utils.similarity import cosine_similarity

if TYPE_CHECKING:
    from src.memory_graph.models import Memory

logger = get_logger(__name__)


async def deduplicate_memories_by_similarity(
    memories: list[tuple[Any, float, Any]],  # [(Memory, score, extra_data), ...]
    similarity_threshold: float = 0.85,
    keep_top_n: int | None = None,
) -> list[tuple[Any, float, Any]]:
    """
    基于相似度对记忆进行去重聚合
    
    策略：
    1. 计算所有记忆对之间的相似度
    2. 当相似度 > threshold 时，合并为一条记忆
    3. 保留分数更高的记忆，丢弃分数较低的
    4. 合并后的记忆分数为原始分数的加权平均
    
    Args:
        memories: 记忆列表 [(Memory, score, extra_data), ...]
        similarity_threshold: 相似度阈值（0.85 表示 85% 相似即视为重复）
        keep_top_n: 去重后保留的最大数量（None 表示不限制）
        
    Returns:
        去重后的记忆列表 [(Memory, adjusted_score, extra_data), ...]
    """
    if len(memories) <= 1:
        return memories
    
    logger.info(f"开始记忆去重: {len(memories)} 条记忆 (阈值={similarity_threshold})")
    
    # 准备数据结构
    memory_embeddings = []
    for memory, score, extra in memories:
        # 获取记忆的向量表示
        embedding = await _get_memory_embedding(memory)
        memory_embeddings.append((memory, score, extra, embedding))
    
    # 构建相似度矩阵并找出重复组
    duplicate_groups = _find_duplicate_groups(memory_embeddings, similarity_threshold)
    
    # 合并每个重复组
    deduplicated = []
    processed_indices = set()
    
    for group_indices in duplicate_groups:
        if any(i in processed_indices for i in group_indices):
            continue  # 已经处理过
        
        # 标记为已处理
        processed_indices.update(group_indices)
        
        # 合并组内记忆
        group_memories = [memory_embeddings[i] for i in group_indices]
        merged_memory = _merge_memory_group(group_memories)
        deduplicated.append(merged_memory)
    
    # 添加未被合并的记忆
    for i, (memory, score, extra, _) in enumerate(memory_embeddings):
        if i not in processed_indices:
            deduplicated.append((memory, score, extra))
    
    # 按分数排序
    deduplicated.sort(key=lambda x: x[1], reverse=True)
    
    # 限制数量
    if keep_top_n is not None:
        deduplicated = deduplicated[:keep_top_n]
    
    logger.info(
        f"去重完成: {len(memories)} → {len(deduplicated)} 条记忆 "
        f"(合并了 {len(memories) - len(deduplicated)} 条重复)"
    )
    
    return deduplicated


async def _get_memory_embedding(memory: Any) -> list[float] | None:
    """
    获取记忆的向量表示
    
    策略：
    1. 如果记忆有节点，使用第一个节点的 ID 查询向量存储
    2. 返回节点的 embedding
    3. 如果无法获取，返回 None
    """
    # 尝试从节点获取 embedding
    if hasattr(memory, "nodes") and memory.nodes:
        # nodes 是 MemoryNode 对象列表
        first_node = memory.nodes[0]
        node_id = getattr(first_node, "id", None)
        
        if node_id:
            # 直接从 embedding 属性获取（如果存在）
            if hasattr(first_node, "embedding") and first_node.embedding is not None:
                embedding = first_node.embedding
                # 转换为列表
                if hasattr(embedding, "tolist"):
                    return embedding.tolist()
                elif isinstance(embedding, list):
                    return embedding
    
    # 无法获取 embedding
    return None


def _find_duplicate_groups(
    memory_embeddings: list[tuple[Any, float, Any, list[float] | None]],
    threshold: float
) -> list[list[int]]:
    """
    找出相似度超过阈值的记忆组
    
    Returns:
        List of groups, each group is a list of indices
        例如: [[0, 3, 7], [1, 4], [2, 5, 6]] 表示 3 个重复组
    """
    n = len(memory_embeddings)
    similarity_matrix = [[0.0] * n for _ in range(n)]
    
    # 计算相似度矩阵
    for i in range(n):
        for j in range(i + 1, n):
            embedding_i = memory_embeddings[i][3]
            embedding_j = memory_embeddings[j][3]
            
            # 跳过 None 或零向量
            if (embedding_i is None or embedding_j is None or
                all(x == 0.0 for x in embedding_i) or all(x == 0.0 for x in embedding_j)):
                similarity = 0.0
            else:
                # cosine_similarity 会自动转换为 numpy 数组
                similarity = float(cosine_similarity(embedding_i, embedding_j))  # type: ignore
            
            similarity_matrix[i][j] = similarity
            similarity_matrix[j][i] = similarity
    
    # 使用并查集找出连通分量
    parent = list(range(n))
    
    def find(x):
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]
    
    def union(x, y):
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py
    
    # 合并相似的记忆
    for i in range(n):
        for j in range(i + 1, n):
            if similarity_matrix[i][j] >= threshold:
                union(i, j)
    
    # 构建组
    groups_dict: dict[int, list[int]] = {}
    for i in range(n):
        root = find(i)
        if root not in groups_dict:
            groups_dict[root] = []
        groups_dict[root].append(i)
    
    # 只返回大小 > 1 的组（真正的重复组）
    duplicate_groups = [group for group in groups_dict.values() if len(group) > 1]
    
    return duplicate_groups


def _merge_memory_group(
    group: list[tuple[Any, float, Any, list[float] | None]]
) -> tuple[Any, float, Any]:
    """
    合并一组相似的记忆
    
    策略：
    1. 保留分数最高的记忆作为代表
    2. 合并后的分数 = 所有记忆分数的加权平均（权重随排名递减）
    3. 在 extra_data 中记录合并信息
    """
    # 按分数排序
    sorted_group = sorted(group, key=lambda x: x[1], reverse=True)
    
    # 保留分数最高的记忆
    best_memory, best_score, best_extra, _ = sorted_group[0]
    
    # 计算合并后的分数（加权平均，权重递减）
    total_weight = 0.0
    weighted_sum = 0.0
    for i, (_, score, _, _) in enumerate(sorted_group):
        weight = 1.0 / (i + 1)  # 第1名权重1.0，第2名0.5，第3名0.33...
        weighted_sum += score * weight
        total_weight += weight
    
    merged_score = weighted_sum / total_weight if total_weight > 0 else best_score
    
    # 增强 extra_data
    merged_extra = best_extra if isinstance(best_extra, dict) else {}
    merged_extra["merged_count"] = len(sorted_group)
    merged_extra["original_scores"] = [score for _, score, _, _ in sorted_group]
    
    logger.debug(
        f"合并 {len(sorted_group)} 条相似记忆: "
        f"分数 {best_score:.3f} → {merged_score:.3f}"
    )
    
    return (best_memory, merged_score, merged_extra)
