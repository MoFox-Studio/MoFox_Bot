"""
图存储层：基于 NetworkX 的图结构管理
"""

from __future__ import annotations

import networkx as nx

from src.common.logger import get_logger
from src.memory_graph.models import Memory, MemoryEdge

logger = get_logger(__name__)


class GraphStore:
    """
    图存储封装类

    负责：
    1. 记忆图的构建和维护
    2. 节点和边的快速查询
    3. 图遍历算法（BFS/DFS）
    4. 邻接关系查询
    """

    def __init__(self):
        """初始化图存储"""
        # 使用有向图（记忆关系通常是有向的）
        self.graph = nx.DiGraph()

        # 索引：记忆ID -> 记忆对象
        self.memory_index: dict[str, Memory] = {}

        # 索引：节点ID -> 所属记忆ID集合
        self.node_to_memories: dict[str, set[str]] = {}

        logger.info("初始化图存储")

    def add_memory(self, memory: Memory) -> None:
        """
        添加记忆到图

        Args:
            memory: 要添加的记忆
        """
        try:
            # 1. 添加所有节点到图
            for node in memory.nodes:
                if not self.graph.has_node(node.id):
                    self.graph.add_node(
                        node.id,
                        content=node.content,
                        node_type=node.node_type.value,
                        created_at=node.created_at.isoformat(),
                        metadata=node.metadata,
                    )

                # 更新节点到记忆的映射
                if node.id not in self.node_to_memories:
                    self.node_to_memories[node.id] = set()
                self.node_to_memories[node.id].add(memory.id)

            # 2. 添加所有边到图
            for edge in memory.edges:
                self.graph.add_edge(
                    edge.source_id,
                    edge.target_id,
                    edge_id=edge.id,
                    relation=edge.relation,
                    edge_type=edge.edge_type.value,
                    importance=edge.importance,
                    metadata=edge.metadata,
                    created_at=edge.created_at.isoformat(),
                )

            # 3. 保存记忆对象
            self.memory_index[memory.id] = memory

            logger.debug(f"添加记忆到图: {memory}")

        except Exception as e:
            logger.error(f"添加记忆失败: {e}", exc_info=True)
            raise

    def get_memory_by_id(self, memory_id: str) -> Memory | None:
        """
        根据ID获取记忆

        Args:
            memory_id: 记忆ID

        Returns:
            记忆对象或 None
        """
        return self.memory_index.get(memory_id)

    def get_all_memories(self) -> list[Memory]:
        """
        获取所有记忆

        Returns:
            所有记忆的列表
        """
        return list(self.memory_index.values())

    def get_memories_by_node(self, node_id: str) -> list[Memory]:
        """
        获取包含指定节点的所有记忆

        Args:
            node_id: 节点ID

        Returns:
            记忆列表
        """
        if node_id not in self.node_to_memories:
            return []

        memory_ids = self.node_to_memories[node_id]
        return [self.memory_index[mid] for mid in memory_ids if mid in self.memory_index]

    def get_edges_from_node(self, node_id: str, relation_types: list[str] | None = None) -> list[dict]:
        """
        获取从指定节点出发的所有边

        Args:
            node_id: 源节点ID
            relation_types: 关系类型过滤（可选）

        Returns:
            边信息列表
        """
        if not self.graph.has_node(node_id):
            return []

        edges = []
        for _, target_id, edge_data in self.graph.out_edges(node_id, data=True):
            # 过滤关系类型
            if relation_types and edge_data.get("relation") not in relation_types:
                continue

            edges.append(
                {
                    "source_id": node_id,
                    "target_id": target_id,
                    "relation": edge_data.get("relation"),
                    "edge_type": edge_data.get("edge_type"),
                    "importance": edge_data.get("importance", 0.5),
                    **edge_data,
                }
            )

        return edges

    def get_neighbors(
        self, node_id: str, direction: str = "out", relation_types: list[str] | None = None
    ) -> list[tuple[str, dict]]:
        """
        获取节点的邻居节点

        Args:
            node_id: 节点ID
            direction: 方向 ("out"=出边, "in"=入边, "both"=双向)
            relation_types: 关系类型过滤

        Returns:
            List of (neighbor_id, edge_data)
        """
        if not self.graph.has_node(node_id):
            return []

        neighbors = []

        # 处理出边
        if direction in ["out", "both"]:
            for _, target_id, edge_data in self.graph.out_edges(node_id, data=True):
                if not relation_types or edge_data.get("relation") in relation_types:
                    neighbors.append((target_id, edge_data))

        # 处理入边
        if direction in ["in", "both"]:
            for source_id, _, edge_data in self.graph.in_edges(node_id, data=True):
                if not relation_types or edge_data.get("relation") in relation_types:
                    neighbors.append((source_id, edge_data))

        return neighbors

    def find_path(self, source_id: str, target_id: str, max_length: int | None = None) -> list[str] | None:
        """
        查找两个节点之间的最短路径

        Args:
            source_id: 源节点ID
            target_id: 目标节点ID
            max_length: 最大路径长度（可选）

        Returns:
            路径节点ID列表，或 None（如果不存在路径）
        """
        if not self.graph.has_node(source_id) or not self.graph.has_node(target_id):
            return None

        try:
            if max_length:
                # 使用 cutoff 限制路径长度
                path = nx.shortest_path(self.graph, source_id, target_id, weight=None)
                if len(path) - 1 <= max_length:  # 边数 = 节点数 - 1
                    return path
                return None
            else:
                return nx.shortest_path(self.graph, source_id, target_id, weight=None)

        except nx.NetworkXNoPath:
            return None
        except Exception as e:
            logger.error(f"查找路径失败: {e}", exc_info=True)
            return None

    def bfs_expand(
        self,
        start_nodes: list[str],
        depth: int = 1,
        relation_types: list[str] | None = None,
    ) -> set[str]:
        """
        从起始节点进行广度优先搜索扩展

        Args:
            start_nodes: 起始节点ID列表
            depth: 扩展深度
            relation_types: 关系类型过滤

        Returns:
            扩展到的所有节点ID集合
        """
        visited = set()
        queue = [(node_id, 0) for node_id in start_nodes if self.graph.has_node(node_id)]

        while queue:
            current_node, current_depth = queue.pop(0)

            if current_node in visited:
                continue
            visited.add(current_node)

            if current_depth >= depth:
                continue

            # 获取邻居并加入队列
            neighbors = self.get_neighbors(current_node, direction="out", relation_types=relation_types)
            for neighbor_id, _ in neighbors:
                if neighbor_id not in visited:
                    queue.append((neighbor_id, current_depth + 1))

        return visited

    def get_subgraph(self, node_ids: list[str]) -> nx.DiGraph:
        """
        获取包含指定节点的子图

        Args:
            node_ids: 节点ID列表

        Returns:
            NetworkX 子图
        """
        return self.graph.subgraph(node_ids).copy()

    def merge_nodes(self, source_id: str, target_id: str) -> None:
        """
        合并两个节点（将source的所有边转移到target，然后删除source）

        Args:
            source_id: 源节点ID（将被删除）
            target_id: 目标节点ID（保留）
        """
        if not self.graph.has_node(source_id) or not self.graph.has_node(target_id):
            logger.warning(f"合并节点失败: 节点不存在 ({source_id}, {target_id})")
            return

        try:
            # 1. 转移入边
            for pred, _, edge_data in self.graph.in_edges(source_id, data=True):
                if pred != target_id:  # 避免自环
                    self.graph.add_edge(pred, target_id, **edge_data)

            # 2. 转移出边
            for _, succ, edge_data in self.graph.out_edges(source_id, data=True):
                if succ != target_id:  # 避免自环
                    self.graph.add_edge(target_id, succ, **edge_data)

            # 3. 更新节点到记忆的映射
            if source_id in self.node_to_memories:
                memory_ids = self.node_to_memories[source_id]
                if target_id not in self.node_to_memories:
                    self.node_to_memories[target_id] = set()
                self.node_to_memories[target_id].update(memory_ids)
                del self.node_to_memories[source_id]

            # 4. 删除源节点
            self.graph.remove_node(source_id)

            logger.info(f"节点合并: {source_id} → {target_id}")

        except Exception as e:
            logger.error(f"合并节点失败: {e}", exc_info=True)
            raise

    def get_node_degree(self, node_id: str) -> tuple[int, int]:
        """
        获取节点的度数

        Args:
            node_id: 节点ID

        Returns:
            (in_degree, out_degree)
        """
        if not self.graph.has_node(node_id):
            return (0, 0)

        return (self.graph.in_degree(node_id), self.graph.out_degree(node_id))

    def get_statistics(self) -> dict[str, int]:
        """获取图的统计信息"""
        return {
            "total_nodes": self.graph.number_of_nodes(),
            "total_edges": self.graph.number_of_edges(),
            "total_memories": len(self.memory_index),
            "connected_components": nx.number_weakly_connected_components(self.graph),
        }

    def to_dict(self) -> dict:
        """
        将图转换为字典（用于持久化）

        Returns:
            图的字典表示
        """
        return {
            "nodes": [
                {"id": node_id, **self.graph.nodes[node_id]} for node_id in self.graph.nodes()
            ],
            "edges": [
                {
                    "source": u,
                    "target": v,
                    **data,
                }
                for u, v, data in self.graph.edges(data=True)
            ],
            "memories": {memory_id: memory.to_dict() for memory_id, memory in self.memory_index.items()},
            "node_to_memories": {node_id: list(mem_ids) for node_id, mem_ids in self.node_to_memories.items()},
        }

    @classmethod
    def from_dict(cls, data: dict) -> GraphStore:
        """
        从字典加载图

        Args:
            data: 图的字典表示

        Returns:
            GraphStore 实例
        """
        store = cls()

        # 1. 加载节点
        for node_data in data.get("nodes", []):
            node_id = node_data.pop("id")
            store.graph.add_node(node_id, **node_data)

        # 2. 加载边
        for edge_data in data.get("edges", []):
            source = edge_data.pop("source")
            target = edge_data.pop("target")
            store.graph.add_edge(source, target, **edge_data)

        # 3. 加载记忆
        for memory_id, memory_dict in data.get("memories", {}).items():
            store.memory_index[memory_id] = Memory.from_dict(memory_dict)

        # 4. 加载节点到记忆的映射
        for node_id, mem_ids in data.get("node_to_memories", {}).items():
            store.node_to_memories[node_id] = set(mem_ids)

        # 5. 同步图中的边到 Memory.edges（保证内存对象和图一致）
        try:
            store._sync_memory_edges_from_graph()
        except Exception:
            logger.exception("同步图边到记忆.edges 失败")

        logger.info(f"从字典加载图: {store.get_statistics()}")
        return store

    def _sync_memory_edges_from_graph(self) -> None:
        """
        将 NetworkX 图中的边重建为 MemoryEdge 并注入到对应的 Memory.edges 列表中。

        目的：当从持久化数据加载时，确保 memory_index 中的 Memory 对象的
        edges 列表反映图中实际存在的边（避免只有图中存在而 memory.edges 为空的不同步情况）。

        规则：对于图中每条边(u, v, data)，会尝试将该边注入到所有包含 u 或 v 的记忆中（避免遗漏跨记忆边）。
        已存在的边（通过 edge.id 检查）将不会重复添加。
        """

        # 构建快速查重索引：memory_id -> set(edge_id)
        existing_edges = {mid: {e.id for e in mem.edges} for mid, mem in self.memory_index.items()}

        for u, v, data in self.graph.edges(data=True):
            # 兼容旧数据：edge_id 可能在 data 中，或叫 id
            edge_id = data.get("edge_id") or data.get("id") or ""

            edge_dict = {
                "id": edge_id or "",
                "source_id": u,
                "target_id": v,
                "relation": data.get("relation", ""),
                "edge_type": data.get("edge_type", data.get("edge_type", "")),
                "importance": data.get("importance", 0.5),
                "metadata": data.get("metadata", {}),
                "created_at": data.get("created_at", "1970-01-01T00:00:00"),
            }

            # 找到相关记忆（包含源或目标节点）
            related_memory_ids = set()
            if u in self.node_to_memories:
                related_memory_ids.update(self.node_to_memories[u])
            if v in self.node_to_memories:
                related_memory_ids.update(self.node_to_memories[v])

            for mid in related_memory_ids:
                mem = self.memory_index.get(mid)
                if mem is None:
                    continue

                # 检查是否已存在
                if edge_dict["id"] and edge_dict["id"] in existing_edges.get(mid, set()):
                    continue

                try:
                    # 使用 MemoryEdge.from_dict 构建对象
                    mem_edge = MemoryEdge.from_dict(edge_dict)
                except Exception:
                    # 兼容性：直接构造对象
                    mem_edge = MemoryEdge(
                        id=edge_dict["id"] or "",
                        source_id=edge_dict["source_id"],
                        target_id=edge_dict["target_id"],
                        relation=edge_dict["relation"],
                        edge_type=edge_dict["edge_type"],
                        importance=edge_dict.get("importance", 0.5),
                        metadata=edge_dict.get("metadata", {}),
                    )

                mem.edges.append(mem_edge)
                existing_edges.setdefault(mid, set()).add(mem_edge.id)

        logger.info("已将图中的边同步到 Memory.edges（保证 graph 与 memory 对象一致）")

    def remove_memory(self, memory_id: str, cleanup_orphans: bool = True) -> bool:
        """
        从图中删除指定记忆

        Args:
            memory_id: 要删除的记忆ID
            cleanup_orphans: 是否立即清理孤立节点（默认True，批量删除时设为False）

        Returns:
            是否删除成功
        """
        try:
            # 1. 检查记忆是否存在
            if memory_id not in self.memory_index:
                logger.warning(f"记忆不存在，无法删除: {memory_id}")
                return False

            memory = self.memory_index[memory_id]

            # 2. 从节点映射中移除此记忆
            for node in memory.nodes:
                if node.id in self.node_to_memories:
                    self.node_to_memories[node.id].discard(memory_id)
                    
                    # 可选：立即清理孤立节点
                    if cleanup_orphans:
                        # 如果该节点不再属于任何记忆，从图中移除节点
                        if not self.node_to_memories[node.id]:
                            if self.graph.has_node(node.id):
                                self.graph.remove_node(node.id)
                            del self.node_to_memories[node.id]

            # 3. 从记忆索引中移除
            del self.memory_index[memory_id]

            logger.debug(f"成功删除记忆: {memory_id}")
            return True

        except Exception as e:
            logger.error(f"删除记忆失败 {memory_id}: {e}", exc_info=True)
            return False

    def clear(self) -> None:
        """清空图（危险操作，仅用于测试）"""
        self.graph.clear()
        self.memory_index.clear()
        self.node_to_memories.clear()
        logger.warning("图存储已清空")
