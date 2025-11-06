"""
向量存储层：基于 ChromaDB 的语义向量存储
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from src.common.logger import get_logger
from src.memory_graph.models import MemoryNode, NodeType

logger = get_logger(__name__)


class VectorStore:
    """
    向量存储封装类
    
    负责：
    1. 节点的语义向量存储和检索
    2. 基于相似度的向量搜索
    3. 节点去重时的相似节点查找
    """

    def __init__(
        self,
        collection_name: str = "memory_nodes",
        data_dir: Optional[Path] = None,
        embedding_function: Optional[Any] = None,
    ):
        """
        初始化向量存储
        
        Args:
            collection_name: ChromaDB 集合名称
            data_dir: 数据存储目录
            embedding_function: 嵌入函数（如果为None则使用默认）
        """
        self.collection_name = collection_name
        self.data_dir = data_dir or Path("data/memory_graph")
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.client = None
        self.collection = None
        self.embedding_function = embedding_function

        logger.info(f"初始化向量存储: collection={collection_name}, dir={self.data_dir}")

    async def initialize(self) -> None:
        """异步初始化 ChromaDB"""
        try:
            import chromadb
            from chromadb.config import Settings

            # 创建持久化客户端
            self.client = chromadb.PersistentClient(
                path=str(self.data_dir / "chroma"),
                settings=Settings(
                    anonymized_telemetry=False,
                    allow_reset=True,
                ),
            )

            # 获取或创建集合
            self.collection = self.client.get_or_create_collection(
                name=self.collection_name,
                metadata={"description": "Memory graph node embeddings"},
            )

            logger.info(f"ChromaDB 初始化完成，集合包含 {self.collection.count()} 个节点")

        except Exception as e:
            logger.error(f"初始化 ChromaDB 失败: {e}", exc_info=True)
            raise

    async def add_node(self, node: MemoryNode) -> None:
        """
        添加节点到向量存储
        
        Args:
            node: 要添加的节点
        """
        if not self.collection:
            raise RuntimeError("向量存储未初始化")

        if not node.has_embedding():
            logger.warning(f"节点 {node.id} 没有 embedding，跳过添加")
            return

        try:
            # 准备元数据（ChromaDB 只支持 str, int, float, bool）
            metadata = {
                "content": node.content,
                "node_type": node.node_type.value,
                "created_at": node.created_at.isoformat(),
            }
            
            # 处理额外的元数据，将 list 转换为 JSON 字符串
            for key, value in node.metadata.items():
                if isinstance(value, (list, dict)):
                    import orjson
                    metadata[key] = orjson.dumps(value, option=orjson.OPT_NON_STR_KEYS).decode('utf-8')
                elif isinstance(value, (str, int, float, bool)) or value is None:
                    metadata[key] = value
                else:
                    metadata[key] = str(value)
            
            self.collection.add(
                ids=[node.id],
                embeddings=[node.embedding.tolist()],
                metadatas=[metadata],
                documents=[node.content],  # 文本内容用于检索
            )

            logger.debug(f"添加节点到向量存储: {node}")

        except Exception as e:
            logger.error(f"添加节点失败: {e}", exc_info=True)
            raise

    async def add_nodes_batch(self, nodes: List[MemoryNode]) -> None:
        """
        批量添加节点
        
        Args:
            nodes: 节点列表
        """
        if not self.collection:
            raise RuntimeError("向量存储未初始化")

        # 过滤出有 embedding 的节点
        valid_nodes = [n for n in nodes if n.has_embedding()]

        if not valid_nodes:
            logger.warning("批量添加：没有有效的节点（缺少 embedding）")
            return

        try:
            # 准备元数据
            import orjson
            metadatas = []
            for n in valid_nodes:
                metadata = {
                    "content": n.content,
                    "node_type": n.node_type.value,
                    "created_at": n.created_at.isoformat(),
                }
                for key, value in n.metadata.items():
                    if isinstance(value, (list, dict)):
                        metadata[key] = orjson.dumps(value, option=orjson.OPT_NON_STR_KEYS).decode('utf-8')
                    elif isinstance(value, (str, int, float, bool)) or value is None:
                        metadata[key] = value  # type: ignore
                    else:
                        metadata[key] = str(value)
                metadatas.append(metadata)
            
            self.collection.add(
                ids=[n.id for n in valid_nodes],
                embeddings=[n.embedding.tolist() for n in valid_nodes],  # type: ignore
                metadatas=metadatas,
                documents=[n.content for n in valid_nodes],
            )

            logger.info(f"批量添加 {len(valid_nodes)} 个节点到向量存储")

        except Exception as e:
            logger.error(f"批量添加节点失败: {e}", exc_info=True)
            raise

    async def search_similar_nodes(
        self,
        query_embedding: np.ndarray,
        limit: int = 10,
        node_types: Optional[List[NodeType]] = None,
        min_similarity: float = 0.0,
    ) -> List[Tuple[str, float, Dict[str, Any]]]:
        """
        搜索相似节点
        
        Args:
            query_embedding: 查询向量
            limit: 返回结果数量
            node_types: 限制节点类型（可选）
            min_similarity: 最小相似度阈值
            
        Returns:
            List of (node_id, similarity, metadata)
        """
        if not self.collection:
            raise RuntimeError("向量存储未初始化")

        try:
            # 构建 where 条件
            where_filter = None
            if node_types:
                where_filter = {"node_type": {"$in": [nt.value for nt in node_types]}}

            # 执行查询
            results = self.collection.query(
                query_embeddings=[query_embedding.tolist()],
                n_results=limit,
                where=where_filter,
            )

            # 解析结果
            import orjson
            similar_nodes = []
            if results["ids"] and results["ids"][0]:
                for i, node_id in enumerate(results["ids"][0]):
                    # ChromaDB 返回的是距离，需要转换为相似度
                    # 余弦距离: distance = 1 - similarity
                    distance = results["distances"][0][i] if results["distances"] else 0.0  # type: ignore
                    similarity = 1.0 - distance

                    if similarity >= min_similarity:
                        metadata = results["metadatas"][0][i] if results["metadatas"] else {}  # type: ignore
                        
                        # 解析 JSON 字符串回列表/字典
                        for key, value in list(metadata.items()):
                            if isinstance(value, str) and (value.startswith('[') or value.startswith('{')):
                                try:
                                    metadata[key] = orjson.loads(value)
                                except:
                                    pass  # 保持原值
                        
                        similar_nodes.append((node_id, similarity, metadata))

            logger.debug(f"相似节点搜索: 找到 {len(similar_nodes)} 个结果")
            return similar_nodes

        except Exception as e:
            logger.error(f"相似节点搜索失败: {e}", exc_info=True)
            raise

    async def search_with_multiple_queries(
        self,
        query_embeddings: List[np.ndarray],
        query_weights: Optional[List[float]] = None,
        limit: int = 10,
        node_types: Optional[List[NodeType]] = None,
        min_similarity: float = 0.0,
        fusion_strategy: str = "weighted_max",
    ) -> List[Tuple[str, float, Dict[str, Any]]]:
        """
        多查询融合搜索
        
        使用多个查询向量进行搜索，然后融合结果。
        这能解决单一查询向量无法同时关注多个关键概念的问题。
        
        Args:
            query_embeddings: 查询向量列表
            query_weights: 每个查询的权重（可选，默认均等）
            limit: 最终返回结果数量
            node_types: 限制节点类型（可选）
            min_similarity: 最小相似度阈值
            fusion_strategy: 融合策略
                - "weighted_max": 加权最大值（推荐）
                - "weighted_sum": 加权求和
                - "rrf": Reciprocal Rank Fusion
            
        Returns:
            融合后的节点列表 [(node_id, fused_score, metadata), ...]
        """
        if not self.collection:
            raise RuntimeError("向量存储未初始化")

        if not query_embeddings:
            return []

        # 默认权重均等
        if query_weights is None:
            query_weights = [1.0 / len(query_embeddings)] * len(query_embeddings)
        
        # 归一化权重
        total_weight = sum(query_weights)
        if total_weight > 0:
            query_weights = [w / total_weight for w in query_weights]

        try:
            # 1. 对每个查询执行搜索
            all_results: Dict[str, Dict[str, Any]] = {}  # node_id -> {scores, metadata}

            for i, (query_emb, weight) in enumerate(zip(query_embeddings, query_weights)):
                # 搜索更多结果以提高融合质量
                search_limit = limit * 3
                results = await self.search_similar_nodes(
                    query_embedding=query_emb,
                    limit=search_limit,
                    node_types=node_types,
                    min_similarity=min_similarity,
                )

                # 记录每个结果
                for rank, (node_id, similarity, metadata) in enumerate(results):
                    if node_id not in all_results:
                        all_results[node_id] = {
                            "scores": [],
                            "ranks": [],
                            "metadata": metadata,
                        }
                    
                    all_results[node_id]["scores"].append((similarity, weight))
                    all_results[node_id]["ranks"].append((rank, weight))

            # 2. 融合分数
            fused_results = []
            
            for node_id, data in all_results.items():
                scores = data["scores"]
                ranks = data["ranks"]
                metadata = data["metadata"]

                if fusion_strategy == "weighted_max":
                    # 加权最大值 + 出现次数奖励
                    max_weighted_score = max(score * weight for score, weight in scores)
                    appearance_bonus = len(scores) * 0.05  # 出现多次有奖励
                    fused_score = max_weighted_score + appearance_bonus

                elif fusion_strategy == "weighted_sum":
                    # 加权求和（可能导致出现多次的结果分数过高）
                    fused_score = sum(score * weight for score, weight in scores)

                elif fusion_strategy == "rrf":
                    # Reciprocal Rank Fusion
                    # RRF score = sum(weight / (rank + k))
                    k = 60  # RRF 常数
                    fused_score = sum(weight / (rank + k) for rank, weight in ranks)

                else:
                    # 默认使用加权平均
                    fused_score = sum(score * weight for score, weight in scores) / len(scores)

                fused_results.append((node_id, fused_score, metadata))

            # 3. 排序并返回 Top-K
            fused_results.sort(key=lambda x: x[1], reverse=True)
            final_results = fused_results[:limit]

            logger.info(
                f"多查询融合搜索完成: {len(query_embeddings)} 个查询, "
                f"融合后 {len(fused_results)} 个结果, 返回 {len(final_results)} 个"
            )

            return final_results

        except Exception as e:
            logger.error(f"多查询融合搜索失败: {e}", exc_info=True)
            raise

    async def get_node_by_id(self, node_id: str) -> Optional[Dict[str, Any]]:
        """
        根据ID获取节点元数据
        
        Args:
            node_id: 节点ID
            
        Returns:
            节点元数据或 None
        """
        if not self.collection:
            raise RuntimeError("向量存储未初始化")

        try:
            result = self.collection.get(ids=[node_id], include=["metadatas", "embeddings"])

            if result and result["ids"]:
                return {
                    "id": result["ids"][0],
                    "metadata": result["metadatas"][0] if result["metadatas"] else {},
                    "embedding": np.array(result["embeddings"][0]) if result["embeddings"] else None,
                }

            return None

        except Exception as e:
            logger.error(f"获取节点失败: {e}", exc_info=True)
            return None

    async def delete_node(self, node_id: str) -> None:
        """
        删除节点
        
        Args:
            node_id: 节点ID
        """
        if not self.collection:
            raise RuntimeError("向量存储未初始化")

        try:
            self.collection.delete(ids=[node_id])
            logger.debug(f"删除节点: {node_id}")

        except Exception as e:
            logger.error(f"删除节点失败: {e}", exc_info=True)
            raise

    async def update_node_embedding(self, node_id: str, embedding: np.ndarray) -> None:
        """
        更新节点的 embedding
        
        Args:
            node_id: 节点ID
            embedding: 新的向量
        """
        if not self.collection:
            raise RuntimeError("向量存储未初始化")

        try:
            self.collection.update(ids=[node_id], embeddings=[embedding.tolist()])
            logger.debug(f"更新节点 embedding: {node_id}")

        except Exception as e:
            logger.error(f"更新节点 embedding 失败: {e}", exc_info=True)
            raise

    def get_total_count(self) -> int:
        """获取向量存储中的节点总数"""
        if not self.collection:
            return 0
        return self.collection.count()

    async def clear(self) -> None:
        """清空向量存储（危险操作，仅用于测试）"""
        if not self.collection:
            return

        try:
            # 删除并重新创建集合
            self.client.delete_collection(self.collection_name)
            self.collection = self.client.get_or_create_collection(
                name=self.collection_name,
                metadata={"description": "Memory graph node embeddings"},
            )
            logger.warning(f"向量存储已清空: {self.collection_name}")

        except Exception as e:
            logger.error(f"清空向量存储失败: {e}", exc_info=True)
            raise
