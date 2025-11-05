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
            self.collection.add(
                ids=[node.id],
                embeddings=[node.embedding.tolist()],
                metadatas=[
                    {
                        "content": node.content,
                        "node_type": node.node_type.value,
                        "created_at": node.created_at.isoformat(),
                        **node.metadata,
                    }
                ],
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
            self.collection.add(
                ids=[n.id for n in valid_nodes],
                embeddings=[n.embedding.tolist() for n in valid_nodes],
                metadatas=[
                    {
                        "content": n.content,
                        "node_type": n.node_type.value,
                        "created_at": n.created_at.isoformat(),
                        **n.metadata,
                    }
                    for n in valid_nodes
                ],
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
            similar_nodes = []
            if results["ids"] and results["ids"][0]:
                for i, node_id in enumerate(results["ids"][0]):
                    # ChromaDB 返回的是距离，需要转换为相似度
                    # 余弦距离: distance = 1 - similarity
                    distance = results["distances"][0][i]
                    similarity = 1.0 - distance

                    if similarity >= min_similarity:
                        metadata = results["metadatas"][0][i] if results["metadatas"] else {}
                        similar_nodes.append((node_id, similarity, metadata))

            logger.debug(f"相似节点搜索: 找到 {len(similar_nodes)} 个结果")
            return similar_nodes

        except Exception as e:
            logger.error(f"相似节点搜索失败: {e}", exc_info=True)
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

            if result["ids"]:
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
