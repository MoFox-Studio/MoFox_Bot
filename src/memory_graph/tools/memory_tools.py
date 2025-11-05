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
            "description": """创建一个新的记忆节点。

⚠️ 记忆创建原则（必须遵守）：
1. **价值判断**：只创建具有长期价值的关键信息，避免记录日常闲聊、礼貌用语、重复信息
2. **细粒度原则**：每条记忆只包含一个明确的事实/事件/观点，避免泛化
3. **原子性**：如果一句话包含多个重要信息点，拆分成多条独立记忆
4. **具体性**：记录具体的人、事、物、时间、地点，避免模糊描述

❌ 不应创建记忆的情况：
- 普通问候、感谢、确认等礼貌性对话
- 已存在的重复信息
- 临时性、一次性的琐碎信息
- 纯粹的功能操作指令（如"帮我查一下"）
- 缺乏上下文的碎片化信息

✅ 应该创建记忆的情况：
- 用户的个人信息（姓名、职业、兴趣、联系方式等）
- 重要事件（项目进展、重大决定、关键行动等）
- 长期偏好/观点（喜好、价值观、习惯等）
- 人际关系变化（新朋友、合作关系等）
- 具体计划/目标（明确的待办事项、长期目标等）

📝 拆分示例：
- ❌ "用户喜欢编程，最近在学Python和机器学习" → 过于泛化
- ✅ 拆分为3条：
  1. "用户喜欢编程"（观点）
  2. "用户正在学习Python"（事件）
  3. "用户正在学习机器学习"（事件）

记忆结构：主体 + 类型 + 主题 + 客体（可选）+ 属性""",
            "parameters": {
                "type": "object",
                "properties": {
                    "subject": {
                        "type": "string",
                        "description": "记忆的主体，通常是'用户'或具体的人名（避免使用'我'）",
                    },
                    "memory_type": {
                        "type": "string",
                        "enum": ["事件", "事实", "关系", "观点"],
                        "description": "记忆类型：\n- 事件：时间绑定的具体动作（如'完成项目'、'学习课程'）\n- 事实：稳定的客观状态（如'职业是工程师'、'住在北京'）\n- 关系：人际关系（如'认识了朋友'、'同事关系'）\n- 观点：主观评价/偏好（如'喜欢Python'、'认为AI很重要'）",
                    },
                    "topic": {
                        "type": "string",
                        "description": "记忆的核心主题，必须具体且明确（如'学习PyTorch框架'而非'学习编程'）",
                    },
                    "object": {
                        "type": "string",
                        "description": "记忆的客体/对象，作为主题的补充说明（如主题是'学习'，客体可以是'PyTorch框架'）",
                    },
                    "attributes": {
                        "type": "object",
                        "description": "记忆的具体属性（尽量填写以增加记忆的信息密度）",
                        "properties": {
                            "时间": {
                                "type": "string",
                                "description": "具体时间表达式，如'2025-11-05'、'今天下午'、'最近一周'、'3天前'",
                            },
                            "地点": {
                                "type": "string", 
                                "description": "具体地点（如果相关）"
                            },
                            "原因": {
                                "type": "string", 
                                "description": "事件发生的原因或动机（如果明确）"
                            },
                            "方式": {
                                "type": "string", 
                                "description": "完成的方式或途径（如果相关）"
                            },
                            "结果": {
                                "type": "string",
                                "description": "事件的结果或影响（如果已知）"
                            },
                            "状态": {
                                "type": "string",
                                "description": "当前状态（如'进行中'、'已完成'、'计划中'）"
                            },
                        },
                        "additionalProperties": True,
                    },
                    "importance": {
                        "type": "number",
                        "minimum": 0.0,
                        "maximum": 1.0,
                        "description": "记忆的重要性评分（0.0-1.0）：\n- 0.3-0.4: 次要信息\n- 0.5-0.6: 一般信息\n- 0.7-0.8: 重要信息（用户明确表达的偏好、重要事件）\n- 0.9-1.0: 关键信息（核心个人信息、重大决定、强烈偏好）\n默认0.5",
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
            "description": """手动关联两个已存在的记忆。

⚠️ 使用建议：
- 系统会自动发现记忆间的关联关系，通常不需要手动调用此工具
- 仅在以下情况使用：
  1. 用户明确指出两个记忆之间的关系
  2. 发现明显的因果关系但系统未自动关联
  3. 需要建立特殊的引用关系

关系类型说明：
- 导致：A事件/行为导致B事件/结果（因果关系）
- 引用：A记忆引用/基于B记忆（知识关联）
- 相似：A和B描述相似的内容（主题相似）
- 相反：A和B表达相反的观点（对比关系）
- 关联：A和B存在一般性关联（其他关系）""",
            "parameters": {
                "type": "object",
                "properties": {
                    "source_memory_description": {
                        "type": "string",
                        "description": "源记忆的关键描述（用于搜索定位，需要足够具体）",
                    },
                    "target_memory_description": {
                        "type": "string",
                        "description": "目标记忆的关键描述（用于搜索定位，需要足够具体）",
                    },
                    "relation_type": {
                        "type": "string",
                        "enum": ["导致", "引用", "相似", "相反", "关联"],
                        "description": "关系类型（从上述5种类型中选择最合适的）",
                    },
                    "importance": {
                        "type": "number",
                        "minimum": 0.0,
                        "maximum": 1.0,
                        "description": "关系的重要性（0.0-1.0）：\n- 0.5-0.6: 一般关联\n- 0.7-0.8: 重要关联\n- 0.9-1.0: 关键关联\n默认0.6",
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
            "description": """搜索相关的记忆，用于回忆和查找历史信息。

使用场景：
- 用户询问之前的对话内容
- 需要回忆用户的个人信息、偏好、经历
- 查找相关的历史事件或观点
- 基于上下文补充信息

搜索特性：
- 语义搜索：基于内容相似度匹配
- 图遍历：自动扩展相关联的记忆
- 时间过滤：按时间范围筛选
- 类型过滤：按记忆类型筛选""",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索查询（用自然语言描述要查找的内容，如'用户的职业'、'最近的项目'、'Python相关的记忆'）",
                    },
                    "memory_types": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": ["事件", "事实", "关系", "观点"],
                        },
                        "description": "记忆类型过滤（可选，留空表示搜索所有类型）",
                    },
                    "time_range": {
                        "type": "object",
                        "properties": {
                            "start": {
                                "type": "string",
                                "description": "开始时间（如'3天前'、'上周'、'2025-11-01'）",
                            },
                            "end": {
                                "type": "string",
                                "description": "结束时间（如'今天'、'现在'、'2025-11-05'）",
                            },
                        },
                        "description": "时间范围（可选，用于查找特定时间段的记忆）",
                    },
                    "top_k": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 50,
                        "description": "返回结果数量（1-50，默认10）。根据需求调整：\n- 快速查找：3-5条\n- 一般搜索：10条\n- 全面了解：20-30条",
                    },
                    "expand_depth": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 3,
                        "description": "图扩展深度（0-3，默认1）：\n- 0: 仅返回直接匹配的记忆\n- 1: 包含一度相关的记忆（推荐）\n- 2-3: 包含更多间接相关的记忆（用于深度探索）",
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
