"""
LLM 工具接口：定义记忆系统的工具 schema 和执行逻辑
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.common.logger import get_logger
from src.memory_graph.core.builder import MemoryBuilder
from src.memory_graph.core.extractor import MemoryExtractor
from src.memory_graph.models import Memory, MemoryStatus
from src.memory_graph.storage.graph_store import GraphStore
from src.memory_graph.storage.persistence import PersistenceManager
from src.memory_graph.storage.vector_store import VectorStore
from src.memory_graph.utils.embeddings import EmbeddingGenerator

logger = get_logger(__name__)


class MemoryTools:
    """
    记忆系统工具集
    
    提供给 LLM 使用的工具接口：
    1. create_memory: 创建新记忆
    2. link_memories: 关联两个记忆
    3. search_memories: 搜索记忆
    """

    def __init__(
        self,
        vector_store: VectorStore,
        graph_store: GraphStore,
        persistence_manager: PersistenceManager,
        embedding_generator: Optional[EmbeddingGenerator] = None,
    ):
        """
        初始化工具集
        
        Args:
            vector_store: 向量存储
            graph_store: 图存储
            persistence_manager: 持久化管理器
            embedding_generator: 嵌入生成器（可选）
        """
        self.vector_store = vector_store
        self.graph_store = graph_store
        self.persistence_manager = persistence_manager
        self._initialized = False

        # 初始化组件
        self.extractor = MemoryExtractor()
        self.builder = MemoryBuilder(
            vector_store=vector_store,
            graph_store=graph_store,
            embedding_generator=embedding_generator,
        )

    async def _ensure_initialized(self):
        """确保向量存储已初始化"""
        if not self._initialized:
            await self.vector_store.initialize()
            self._initialized = True

    @staticmethod
    def get_create_memory_schema() -> Dict[str, Any]:
        """
        获取 create_memory 工具的 JSON schema
        
        Returns:
            工具 schema 定义
        """
        return {
            "name": "create_memory",
            "description": "创建一个新的记忆。记忆由主体、类型、主题、客体（可选）和属性组成。",
            "parameters": {
                "type": "object",
                "properties": {
                    "subject": {
                        "type": "string",
                        "description": "记忆的主体，通常是'我'、'用户'或具体的人名",
                    },
                    "memory_type": {
                        "type": "string",
                        "enum": ["事件", "事实", "关系", "观点"],
                        "description": "记忆类型：事件（时间绑定的动作）、事实（稳定状态）、关系（人际关系）、观点（主观评价）",
                    },
                    "topic": {
                        "type": "string",
                        "description": "记忆的主题，即发生的事情或状态",
                    },
                    "object": {
                        "type": "string",
                        "description": "记忆的客体，即主题作用的对象（可选）",
                    },
                    "attributes": {
                        "type": "object",
                        "description": "记忆的属性，如时间、地点、原因、方式等",
                        "properties": {
                            "时间": {
                                "type": "string",
                                "description": "时间表达式，如'今天'、'昨天'、'3天前'、'2025-11-05'",
                            },
                            "地点": {"type": "string", "description": "地点"},
                            "原因": {"type": "string", "description": "原因"},
                            "方式": {"type": "string", "description": "方式"},
                        },
                        "additionalProperties": True,
                    },
                    "importance": {
                        "type": "number",
                        "minimum": 0.0,
                        "maximum": 1.0,
                        "description": "记忆的重要性，0-1之间的浮点数，默认0.5",
                    },
                },
                "required": ["subject", "memory_type", "topic"],
            },
        }

    @staticmethod
    def get_link_memories_schema() -> Dict[str, Any]:
        """
        获取 link_memories 工具的 JSON schema
        
        Returns:
            工具 schema 定义
        """
        return {
            "name": "link_memories",
            "description": "关联两个已存在的记忆，建立因果或引用关系。",
            "parameters": {
                "type": "object",
                "properties": {
                    "source_memory_description": {
                        "type": "string",
                        "description": "源记忆的描述，用于查找对应的记忆",
                    },
                    "target_memory_description": {
                        "type": "string",
                        "description": "目标记忆的描述，用于查找对应的记忆",
                    },
                    "relation_type": {
                        "type": "string",
                        "description": "关系类型，如'导致'、'引起'、'因为'、'所以'、'引用'、'基于'等",
                    },
                    "importance": {
                        "type": "number",
                        "minimum": 0.0,
                        "maximum": 1.0,
                        "description": "关系的重要性，0-1之间的浮点数，默认0.6",
                    },
                },
                "required": [
                    "source_memory_description",
                    "target_memory_description",
                    "relation_type",
                ],
            },
        }

    @staticmethod
    def get_search_memories_schema() -> Dict[str, Any]:
        """
        获取 search_memories 工具的 JSON schema
        
        Returns:
            工具 schema 定义
        """
        return {
            "name": "search_memories",
            "description": "搜索相关的记忆。支持语义搜索、图遍历和时间过滤。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索查询，描述要查找的记忆内容",
                    },
                    "memory_types": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": ["事件", "事实", "关系", "观点"],
                        },
                        "description": "要搜索的记忆类型，可多选",
                    },
                    "time_range": {
                        "type": "object",
                        "properties": {
                            "start": {
                                "type": "string",
                                "description": "开始时间，如'3天前'、'2025-11-01'",
                            },
                            "end": {
                                "type": "string",
                                "description": "结束时间，如'今天'、'2025-11-05'",
                            },
                        },
                        "description": "时间范围过滤（可选）",
                    },
                    "top_k": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 50,
                        "description": "返回结果数量，默认10",
                    },
                    "expand_depth": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 3,
                        "description": "图遍历扩展深度，0表示不扩展，默认1",
                    },
                },
                "required": ["query"],
            },
        }

    async def create_memory(self, **params) -> Dict[str, Any]:
        """
        执行 create_memory 工具
        
        Args:
            **params: 工具参数
            
        Returns:
            执行结果
        """
        try:
            logger.info(f"创建记忆: {params.get('subject')} - {params.get('topic')}")

            # 0. 确保初始化
            await self._ensure_initialized()

            # 1. 提取参数
            extracted = self.extractor.extract_from_tool_params(params)

            # 2. 构建记忆
            memory = await self.builder.build_memory(extracted)

            # 3. 添加到存储（暂存状态）
            await self._add_memory_to_stores(memory)

            # 4. 保存到磁盘
            await self.persistence_manager.save_graph_store(self.graph_store)

            logger.info(f"记忆创建成功: {memory.id}")

            return {
                "success": True,
                "memory_id": memory.id,
                "message": f"记忆已创建: {extracted['subject']} - {extracted['topic']}",
                "nodes_count": len(memory.nodes),
                "edges_count": len(memory.edges),
            }

        except Exception as e:
            logger.error(f"记忆创建失败: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "message": "记忆创建失败",
            }

    async def link_memories(self, **params) -> Dict[str, Any]:
        """
        执行 link_memories 工具
        
        Args:
            **params: 工具参数
            
        Returns:
            执行结果
        """
        try:
            logger.info(
                f"关联记忆: {params.get('source_memory_description')} -> "
                f"{params.get('target_memory_description')}"
            )

            # 1. 提取参数
            extracted = self.extractor.extract_link_params(params)

            # 2. 查找源记忆和目标记忆
            source_memory = await self._find_memory_by_description(
                extracted["source_description"]
            )
            target_memory = await self._find_memory_by_description(
                extracted["target_description"]
            )

            if not source_memory:
                return {
                    "success": False,
                    "error": "找不到源记忆",
                    "message": f"未找到匹配的源记忆: {extracted['source_description']}",
                }

            if not target_memory:
                return {
                    "success": False,
                    "error": "找不到目标记忆",
                    "message": f"未找到匹配的目标记忆: {extracted['target_description']}",
                }

            # 3. 创建关联边
            edge = await self.builder.link_memories(
                source_memory=source_memory,
                target_memory=target_memory,
                relation_type=extracted["relation_type"],
                importance=extracted["importance"],
            )

            # 4. 添加边到图存储
            self.graph_store.graph.add_edge(
                edge.source_id,
                edge.target_id,
                relation=edge.relation,
                edge_type=edge.edge_type.value,
                importance=edge.importance,
                **edge.metadata
            )

            # 5. 保存
            await self.persistence_manager.save_graph_store(self.graph_store)

            logger.info(f"记忆关联成功: {source_memory.id} -> {target_memory.id}")

            return {
                "success": True,
                "message": f"记忆已关联: {extracted['relation_type']}",
                "source_memory_id": source_memory.id,
                "target_memory_id": target_memory.id,
                "relation_type": extracted["relation_type"],
            }

        except Exception as e:
            logger.error(f"记忆关联失败: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "message": "记忆关联失败",
            }

    async def search_memories(self, **params) -> Dict[str, Any]:
        """
        执行 search_memories 工具
        
        Args:
            **params: 工具参数
            
        Returns:
            搜索结果
        """
        try:
            query = params.get("query", "")
            top_k = params.get("top_k", 10)
            expand_depth = params.get("expand_depth", 1)

            logger.info(f"搜索记忆: {query} (top_k={top_k}, expand_depth={expand_depth})")

            # 0. 确保初始化
            await self._ensure_initialized()

            # 1. 生成查询嵌入
            if self.builder.embedding_generator:
                query_embedding = await self.builder.embedding_generator.generate(query)
            else:
                logger.warning("未配置嵌入生成器，使用随机向量")
                import numpy as np
                query_embedding = np.random.rand(384).astype(np.float32)

            # 2. 向量搜索
            node_types_filter = None
            if "memory_types" in params:
                # 添加类型过滤
                pass

            similar_nodes = await self.vector_store.search_similar_nodes(
                query_embedding=query_embedding,
                limit=top_k * 2,  # 多取一些，后续过滤
                node_types=node_types_filter,
            )

            # 3. 提取记忆ID
            memory_ids = set()
            for node_id, similarity, metadata in similar_nodes:
                if "memory_ids" in metadata:
                    ids = metadata["memory_ids"]
                    # 确保是列表
                    if isinstance(ids, str):
                        import json
                        try:
                            ids = json.loads(ids)
                        except:
                            ids = [ids]
                    if isinstance(ids, list):
                        memory_ids.update(ids)

            # 4. 获取完整记忆
            memories = []
            for memory_id in list(memory_ids)[:top_k]:
                memory = self.graph_store.get_memory_by_id(memory_id)
                if memory:
                    memories.append(memory)

            # 5. 格式化结果
            results = []
            for memory in memories:
                result = {
                    "memory_id": memory.id,
                    "importance": memory.importance,
                    "created_at": memory.created_at.isoformat(),
                    "summary": self._summarize_memory(memory),
                }
                results.append(result)

            logger.info(f"搜索完成: 找到 {len(results)} 条记忆")

            return {
                "success": True,
                "results": results,
                "total": len(results),
                "query": query,
            }

        except Exception as e:
            logger.error(f"记忆搜索失败: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "message": "记忆搜索失败",
                "results": [],
            }

    async def _add_memory_to_stores(self, memory: Memory):
        """将记忆添加到存储"""
        # 1. 添加到图存储
        self.graph_store.add_memory(memory)

        # 2. 添加有嵌入的节点到向量存储
        for node in memory.nodes:
            if node.embedding is not None:
                await self.vector_store.add_node(node)

    async def _find_memory_by_description(self, description: str) -> Optional[Memory]:
        """
        通过描述查找记忆
        
        Args:
            description: 记忆描述
            
        Returns:
            找到的记忆，如果没有则返回 None
        """
        # 使用语义搜索查找最相关的记忆
        if self.builder.embedding_generator:
            query_embedding = await self.builder.embedding_generator.generate(description)
        else:
            import numpy as np
            query_embedding = np.random.rand(384).astype(np.float32)

        # 搜索相似节点
        similar_nodes = await self.vector_store.search_similar_nodes(
            query_embedding=query_embedding,
            limit=5,
        )

        if not similar_nodes:
            return None

        # 获取最相似节点关联的记忆
        node_id, similarity, metadata = similar_nodes[0]
        
        if "memory_ids" not in metadata or not metadata["memory_ids"]:
            return None
        
        ids = metadata["memory_ids"]
        
        # 确保是列表
        if isinstance(ids, str):
            import json
            try:
                ids = json.loads(ids)
            except Exception as e:
                logger.warning(f"JSON 解析失败: {e}")
                ids = [ids]
        
        if isinstance(ids, list) and ids:
            memory_id = ids[0]
            return self.graph_store.get_memory_by_id(memory_id)
        
        return None

    def _summarize_memory(self, memory: Memory) -> str:
        """生成记忆摘要"""
        if not memory.metadata:
            return "未知记忆"

        subject = memory.metadata.get("subject", "")
        topic = memory.metadata.get("topic", "")
        memory_type = memory.metadata.get("memory_type", "")

        return f"{subject} - {memory_type}: {topic}"

    @staticmethod
    def get_all_tool_schemas() -> List[Dict[str, Any]]:
        """
        获取所有工具的 schema
        
        Returns:
            工具 schema 列表
        """
        return [
            MemoryTools.get_create_memory_schema(),
            MemoryTools.get_link_memories_schema(),
            MemoryTools.get_search_memories_schema(),
        ]
