"""
LLM 工具接口：定义记忆系统的工具 schema 和执行逻辑
"""

from __future__ import annotations

from typing import Any

from src.common.logger import get_logger
from src.memory_graph.core.builder import MemoryBuilder
from src.memory_graph.core.extractor import MemoryExtractor
from src.memory_graph.models import Memory
from src.memory_graph.storage.graph_store import GraphStore
from src.memory_graph.storage.persistence import PersistenceManager
from src.memory_graph.storage.vector_store import VectorStore
from src.memory_graph.utils.embeddings import EmbeddingGenerator
from src.memory_graph.utils.graph_expansion import expand_memories_with_semantic_filter

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
        embedding_generator: EmbeddingGenerator | None = None,
        max_expand_depth: int = 1,
        expand_semantic_threshold: float = 0.3,
        search_top_k: int = 10,
        # 新增：搜索权重配置
        search_vector_weight: float = 0.65,
        search_importance_weight: float = 0.25,
        search_recency_weight: float = 0.10,
        # 新增：阈值过滤配置
        search_min_importance: float = 0.3,
        search_similarity_threshold: float = 0.5,
    ):
        """
        初始化工具集

        Args:
            vector_store: 向量存储
            graph_store: 图存储
            persistence_manager: 持久化管理器
            embedding_generator: 嵌入生成器（可选）
            max_expand_depth: 图扩展深度的默认值（从配置读取）
            expand_semantic_threshold: 图扩展时语义相似度阈值（从配置读取）
            search_top_k: 默认检索返回数量（从配置读取）
            search_vector_weight: 向量相似度权重（从配置读取）
            search_importance_weight: 重要性权重（从配置读取）
            search_recency_weight: 时效性权重（从配置读取）
            search_min_importance: 最小重要性阈值（从配置读取）
            search_similarity_threshold: 向量相似度阈值（从配置读取）
        """
        self.vector_store = vector_store
        self.graph_store = graph_store
        self.persistence_manager = persistence_manager
        self._initialized = False
        self.max_expand_depth = max_expand_depth
        self.expand_semantic_threshold = expand_semantic_threshold
        self.search_top_k = search_top_k
        
        # 保存权重配置
        self.base_vector_weight = search_vector_weight
        self.base_importance_weight = search_importance_weight
        self.base_recency_weight = search_recency_weight
        
        # 保存阈值过滤配置
        self.search_min_importance = search_min_importance
        self.search_similarity_threshold = search_similarity_threshold

        logger.info(
            f"MemoryTools 初始化: max_expand_depth={max_expand_depth}, "
            f"expand_semantic_threshold={expand_semantic_threshold}, "
            f"search_top_k={search_top_k}, "
            f"权重配置: vector={search_vector_weight}, importance={search_importance_weight}, recency={search_recency_weight}, "
            f"阈值过滤: min_importance={search_min_importance}, similarity_threshold={search_similarity_threshold}"
        )

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
    def get_create_memory_schema() -> dict[str, Any]:
        """
        获取 create_memory 工具的 JSON schema

        Returns:
            工具 schema 定义
        """
        return {
            "name": "create_memory",
            "description": """创建一个新的记忆节点，记录对话中有价值的信息。

🎯 **核心原则**：主动记录、积极构建、丰富细节

✅ **优先创建记忆的场景**（鼓励记录）：
1. **个人信息**：姓名、昵称、年龄、职业、身份、所在地、联系方式等
2. **兴趣爱好**：喜欢/不喜欢的事物、娱乐偏好、运动爱好、饮食口味等
3. **生活状态**：工作学习状态、生活习惯、作息时间、日常安排等
4. **经历事件**：正在做的事、完成的任务、参与的活动、遇到的问题等
5. **观点态度**：对事物的看法、价值观、情绪表达、评价意见等
6. **计划目标**：未来打算、学习计划、工作目标、待办事项等
7. **人际关系**：提到的朋友、家人、同事、认识的人等
8. **技能知识**：掌握的技能、学习的知识、专业领域、使用的工具等
9. **物品资源**：拥有的物品、使用的设备、喜欢的品牌等
10. **时间地点**：重要时间节点、常去的地点、活动场所等

⚠️ **暂不创建的情况**（仅限以下）：
- 纯粹的招呼语（单纯的"你好"、"再见"）
- 完全无意义的语气词（单纯的"哦"、"嗯"）
- 明确的系统指令（如"切换模式"、"重启"）

� **记忆拆分建议**：
- 一句话包含多个信息点 → 拆成多条记忆（更利于后续检索）
- 例如："我最近在学Python和机器学习，想找工作"
  → 拆成3条：
  1. "用户正在学习Python"（事件）
  2. "用户正在学习机器学习"（事件）
  3. "用户想找工作"（事件/目标）

📌 **记忆质量建议**：
- 记录时尽量补充时间（"今天"、"最近"、"昨天"等）
- 包含具体细节（越具体越好）
- 主体明确（优先使用"用户"或具体人名，避免"我"）

记忆结构：主体 + 类型 + 主题 + 客体（可选）+ 属性（越详细越好）""",
            "parameters": {
                "type": "object",
                "properties": {
                    "subject": {
                        "type": "string",
                        "description": "记忆的主体（谁的信息）：\n- 对话中的用户统一使用'用户'\n- 提到的具体人物使用其名字（如'小明'、'张三'）\n- 避免使用'我'、'他'等代词",
                    },
                    "memory_type": {
                        "type": "string",
                        "enum": ["事件", "事实", "关系", "观点"],
                        "description": "选择最合适的记忆类型：\n\n【事件】时间相关的动作或发生的事（用'正在'、'完成了'、'参加'等动词）\n  例：正在学习Python、完成了项目、参加会议、去旅行\n\n【事实】相对稳定的客观信息（用'是'、'有'、'在'等描述状态）\n  例：职业是工程师、住在北京、有一只猫、会说英语\n\n【观点】主观看法、喜好、态度（用'喜欢'、'认为'、'觉得'等）\n  例：喜欢Python、认为AI很重要、觉得累、讨厌加班\n\n【关系】人与人之间的关系\n  例：认识了朋友、是同事、家人关系",
                    },
                    "topic": {
                        "type": "string",
                        "description": "记忆的核心内容（做什么/是什么/关于什么）：\n- 尽量具体明确（'学习Python编程' 优于 '学习'）\n- 包含关键动词或核心概念\n- 可以包含时间状态（'正在学习'、'已完成'、'计划做'）",
                    },
                    "object": {
                        "type": "string",
                        "description": "可选：记忆涉及的对象或目标：\n- 事件的对象（学习的是什么、购买的是什么）\n- 观点的对象（喜欢的是什么、讨厌的是什么）\n- 可以留空（如果topic已经足够完整）",
                    },
                    "attributes": {
                        "type": "object",
                        "description": "记忆的详细属性（建议尽量填写，越详细越好）：",
                        "properties": {
                            "时间": {
                                "type": "string",
                                "description": "时间信息（强烈建议填写）：\n- 具体日期：'2025-11-05'、'2025年11月'\n- 相对时间：'今天'、'昨天'、'上周'、'最近'、'3天前'\n- 时间段：'今天下午'、'上个月'、'这学期'",
                            },
                            "地点": {
                                "type": "string",
                                "description": "地点信息（如涉及）：\n- 具体地址、城市名、国家\n- 场所类型：'在家'、'公司'、'学校'、'咖啡店'"
                            },
                            "原因": {
                                "type": "string",
                                "description": "为什么这样做/这样想（如明确提到）"
                            },
                            "方式": {
                                "type": "string",
                                "description": "怎么做的/通过什么方式（如明确提到）"
                            },
                            "结果": {
                                "type": "string",
                                "description": "结果如何/产生什么影响（如明确提到）"
                            },
                            "状态": {
                                "type": "string",
                                "description": "当前进展：'进行中'、'已完成'、'计划中'、'暂停'等"
                            },
                            "程度": {
                                "type": "string",
                                "description": "程度描述（如'非常'、'比较'、'有点'、'不太'）"
                            },
                        },
                        "additionalProperties": True,
                    },
                    "importance": {
                        "type": "number",
                        "minimum": 0.0,
                        "maximum": 1.0,
                        "description": "重要性评分（默认0.5，日常对话建议0.5-0.7）：\n\n0.3-0.4: 次要细节（偶然提及的琐事）\n0.5-0.6: 日常信息（一般性的分享、普通爱好）← 推荐默认值\n0.7-0.8: 重要信息（明确的偏好、重要计划、核心爱好）\n0.9-1.0: 关键信息（身份信息、重大决定、强烈情感）\n\n💡 建议：日常对话中大部分记忆使用0.5-0.6，除非用户特别强调",
                    },
                },
                "required": ["subject", "memory_type", "topic"],
            },
        }

    @staticmethod
    def get_link_memories_schema() -> dict[str, Any]:
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
    def get_search_memories_schema() -> dict[str, Any]:
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
                        "description": "返回结果数量（1-50，不指定则使用系统配置）。根据需求调整：\n- 快速查找：3-5条\n- 一般搜索：10-15条\n- 全面了解：20-30条\n- 深度探索：40-50条\n建议：除非有特殊需求，否则不指定此参数，让系统自动决定。",
                    },
                    "expand_depth": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 3,
                        "description": "图扩展深度（0-3，不指定则使用系统配置，通常为2）：\n- 0: 仅返回直接匹配的记忆\n- 1: 包含一度相关的记忆\n- 2: 包含二度相关的记忆（推荐）\n- 3: 包含三度相关的记忆（深度探索）\n建议：通常不需要指定，系统会自动选择合适的深度。",
                    },
                    "prefer_node_types": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": ["ATTRIBUTE", "REFERENCE", "ENTITY", "EVENT", "RELATION"],
                        },
                        "description": "优先召回的节点类型（可选）：\n- ATTRIBUTE: 属性信息（如配置、参数）\n- REFERENCE: 引用信息（如文档地址、链接）\n- ENTITY: 实体信息（如人物、组织）\n- EVENT: 事件信息（如活动、对话）\n- RELATION: 关系信息（如人际关系）",
                    },
                },
                "required": ["query"],
            },
        }

    async def create_memory(self, **params) -> dict[str, Any]:
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

    async def link_memories(self, **params) -> dict[str, Any]:
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

    async def search_memories(self, **params) -> dict[str, Any]:
        """
        执行 search_memories 工具

        使用多策略检索优化：
        1. 查询分解（识别主要实体和概念）
        2. 多查询并行检索
        3. 结果融合和重排

        Args:
            **params: 工具参数
                - query: 查询字符串
                - top_k: 返回结果数（默认10）
                - expand_depth: 扩展深度（默认使用配置）
                - use_multi_query: 是否使用多查询策略（默认True）
                - prefer_node_types: 优先召回的节点类型列表（可选）
                - context: 查询上下文（可选）

        Returns:
            搜索结果
        """
        try:
            query = params.get("query", "")
            top_k = params.get("top_k", self.search_top_k)  # 使用配置的默认值
            expand_depth = params.get("expand_depth", self.max_expand_depth)
            use_multi_query = params.get("use_multi_query", True)
            prefer_node_types = params.get("prefer_node_types", [])  # 🆕 优先节点类型
            context = params.get("context", None)

            logger.info(
                f"搜索记忆: {query} (top_k={top_k}, expand_depth={expand_depth}, "
                f"multi_query={use_multi_query}, prefer_types={prefer_node_types})"
            )

            # 0. 确保初始化
            await self._ensure_initialized()

            # 1. 根据策略选择检索方式
            llm_prefer_types = []  # LLM识别的偏好节点类型
            
            if use_multi_query:
                # 多查询策略（返回节点列表 + 偏好类型）
                similar_nodes, llm_prefer_types = await self._multi_query_search(query, top_k, context)
            else:
                # 传统单查询策略
                similar_nodes = await self._single_query_search(query, top_k)
            
            # 合并用户指定的偏好类型和LLM识别的偏好类型
            all_prefer_types = list(set(prefer_node_types + llm_prefer_types))
            if all_prefer_types:
                logger.info(f"最终偏好节点类型: {all_prefer_types} (用户指定: {prefer_node_types}, LLM识别: {llm_prefer_types})")
                # 更新prefer_node_types用于后续评分
                prefer_node_types = all_prefer_types

            # 2. 提取初始记忆ID（来自向量搜索）
            initial_memory_ids = set()
            memory_scores = {}  # 记录每个记忆的初始分数

            for node_id, similarity, metadata in similar_nodes:
                if "memory_ids" in metadata:
                    ids = metadata["memory_ids"]
                    # 确保是列表
                    if isinstance(ids, str):
                        import orjson
                        try:
                            ids = orjson.loads(ids)
                        except Exception:
                            ids = [ids]
                    if isinstance(ids, list):
                        for mem_id in ids:
                            initial_memory_ids.add(mem_id)
                            # 记录最高分数
                            if mem_id not in memory_scores or similarity > memory_scores[mem_id]:
                                memory_scores[mem_id] = similarity
            
            # 🔥 详细日志：检查初始召回情况
            logger.info(
                f"初始向量搜索: 返回{len(similar_nodes)}个节点 → "
                f"提取{len(initial_memory_ids)}条记忆"
            )
            if len(initial_memory_ids) == 0:
                logger.warning(
                    f"⚠️ 向量搜索未找到任何记忆！"
                    f"可能原因：1) 嵌入模型理解问题 2) 记忆节点未建立索引 3) 查询表达与存储内容差异过大"
                )
                # 输出相似节点的详细信息用于调试
                if similar_nodes:
                    logger.debug(f"向量搜索返回的节点元数据样例: {similar_nodes[0][2] if len(similar_nodes) > 0 else 'None'}")
            elif len(initial_memory_ids) < 3:
                logger.warning(f"⚠️ 初始召回记忆数量较少({len(initial_memory_ids)}条)，可能影响结果质量")

            # 3. 图扩展（如果启用且有expand_depth）
            expanded_memory_scores = {}
            if expand_depth > 0 and initial_memory_ids:
                logger.info(f"开始图扩展: 初始记忆{len(initial_memory_ids)}个, 深度={expand_depth}")

                # 获取查询的embedding用于语义过滤
                if self.builder.embedding_generator:
                    try:
                        query_embedding = await self.builder.embedding_generator.generate(query)

                        # 只有在嵌入生成成功时才进行语义扩展
                        if query_embedding is not None:
                            # 使用共享的图扩展工具函数
                            expanded_results = await expand_memories_with_semantic_filter(
                                graph_store=self.graph_store,
                                vector_store=self.vector_store,
                                initial_memory_ids=list(initial_memory_ids),
                                query_embedding=query_embedding,
                                max_depth=expand_depth,
                                semantic_threshold=self.expand_semantic_threshold,  # 使用配置的阈值
                                max_expanded=top_k * 2
                            )

                            # 合并扩展结果
                            expanded_memory_scores.update(dict(expanded_results))

                            logger.info(f"图扩展完成: 新增{len(expanded_memory_scores)}个相关记忆")

                    except Exception as e:
                        logger.warning(f"图扩展失败: {e}")

            # 4. 合并初始记忆和扩展记忆
            all_memory_ids = set(initial_memory_ids) | set(expanded_memory_scores.keys())

            # 计算最终分数：初始记忆保持原分数，扩展记忆使用扩展分数
            final_scores = {}
            for mem_id in all_memory_ids:
                if mem_id in memory_scores:
                    # 初始记忆：使用向量相似度分数
                    final_scores[mem_id] = memory_scores[mem_id]
                elif mem_id in expanded_memory_scores:
                    # 扩展记忆：使用图扩展分数（稍微降权）
                    final_scores[mem_id] = expanded_memory_scores[mem_id] * 0.8

            # 按分数排序（先粗排，稍后会用详细评分重新排序）
            sorted_memory_ids = sorted(
                final_scores.keys(),
                key=lambda x: final_scores[x],
                reverse=True
            )  # 🔥 不再提前截断，让所有候选参与详细评分
            
            # 🔍 统计初始记忆的相似度分布（用于诊断）
            if memory_scores:
                similarities = list(memory_scores.values())
                logger.info(
                    f"📊 向量相似度分布: 最高={max(similarities):.3f}, "
                    f"最低={min(similarities):.3f}, "
                    f"平均={sum(similarities)/len(similarities):.3f}, "
                    f">0.3: {len([s for s in similarities if s > 0.3])}/{len(similarities)}, "
                    f">0.2: {len([s for s in similarities if s > 0.2])}/{len(similarities)}"
                )

            # 5. 获取完整记忆并进行最终排序（优化后的动态权重系统）
            memories_with_scores = []
            filter_stats = {"importance": 0, "similarity": 0, "total_checked": 0}  # 过滤统计
            
            for memory_id in sorted_memory_ids:  # 遍历所有候选
                memory = self.graph_store.get_memory_by_id(memory_id)
                if memory:
                    filter_stats["total_checked"] += 1
                    # 基础分数
                    similarity_score = final_scores[memory_id]
                    importance_score = memory.importance
                    
                    # 🆕 区分记忆来源（用于过滤）
                    is_initial_memory = memory_id in memory_scores  # 是否来自初始向量搜索
                    true_similarity = memory_scores.get(memory_id, 0.0) if is_initial_memory else None

                    # 计算时效性分数（最近的记忆得分更高）
                    from datetime import datetime, timezone
                    now = datetime.now(timezone.utc)
                    # 确保 memory.created_at 有时区信息
                    if memory.created_at.tzinfo is None:
                        memory_time = memory.created_at.replace(tzinfo=timezone.utc)
                    else:
                        memory_time = memory.created_at
                    age_days = (now - memory_time).total_seconds() / 86400
                    recency_score = 1.0 / (1.0 + age_days / 30)  # 30天半衰期

                    # 获取激活度分数
                    activation_info = memory.metadata.get("activation", {})
                    activation_score = activation_info.get("level", memory.activation)
                    if activation_score == 0.0 and memory.activation > 0.0:
                        activation_score = memory.activation

                    # 🆕 动态权重计算：使用配置的基础权重 + 根据记忆类型微调
                    memory_type = memory.memory_type.value if hasattr(memory.memory_type, 'value') else str(memory.memory_type)
                    
                    # 检测记忆的主要节点类型
                    node_types_count = {}
                    for node in memory.nodes:
                        nt = node.node_type.value if hasattr(node.node_type, 'value') else str(node.node_type)
                        node_types_count[nt] = node_types_count.get(nt, 0) + 1
                    
                    dominant_node_type = max(node_types_count.items(), key=lambda x: x[1])[0] if node_types_count else "unknown"
                    
                    # 根据记忆类型和节点类型计算调整系数（在配置权重基础上微调）
                    if dominant_node_type in ["ATTRIBUTE", "REFERENCE"] or memory_type == "FACT":
                        # 事实性记忆：提升相似度权重，降低时效性权重
                        type_adjustments = {
                            "similarity": 1.08,    # 相似度提升 8%
                            "importance": 1.0,     # 重要性保持
                            "recency": 0.5,        # 时效性降低 50%（事实不随时间失效）
                        }
                    elif memory_type in ["CONVERSATION", "EPISODIC"] or dominant_node_type == "EVENT":
                        # 对话/事件记忆：提升时效性权重
                        type_adjustments = {
                            "similarity": 0.85,    # 相似度降低 15%
                            "importance": 0.8,     # 重要性降低 20%
                            "recency": 2.5,        # 时效性提升 150%
                        }
                    elif dominant_node_type == "ENTITY" or memory_type == "SEMANTIC":
                        # 实体/语义记忆：平衡调整
                        type_adjustments = {
                            "similarity": 0.92,    # 相似度微降 8%
                            "importance": 1.2,     # 重要性提升 20%
                            "recency": 1.0,        # 时效性保持
                        }
                    else:
                        # 默认不调整
                        type_adjustments = {
                            "similarity": 1.0,
                            "importance": 1.0,
                            "recency": 1.0,
                        }
                    
                    # 应用调整后的权重（基于配置的基础权重）
                    weights = {
                        "similarity": self.base_vector_weight * type_adjustments["similarity"],
                        "importance": self.base_importance_weight * type_adjustments["importance"],
                        "recency": self.base_recency_weight * type_adjustments["recency"],
                    }
                    
                    # 归一化权重（确保总和为1.0）
                    total_weight = sum(weights.values())
                    if total_weight > 0:
                        weights = {k: v / total_weight for k, v in weights.items()}
                    
                    # 综合分数计算（🔥 移除激活度影响）
                    final_score = (
                        similarity_score * weights["similarity"] +
                        importance_score * weights["importance"] +
                        recency_score * weights["recency"]
                    )
                    
                    # 🆕 阈值过滤策略：
                    # 1. 重要性过滤：应用于所有记忆（过滤极低质量）
                    if memory.importance < self.search_min_importance:
                        filter_stats["importance"] += 1
                        logger.debug(f"❌ 过滤 {memory.id[:8]}: 重要性 {memory.importance:.2f} < 阈值 {self.search_min_importance}")
                        continue
                    
                    # 2. 相似度过滤：不再对初始向量搜索结果过滤（信任向量搜索的排序）
                    # 理由：向量搜索已经按相似度排序，返回的都是最相关结果
                    # 如果再用阈值过滤，会导致"最相关的也不够相关"的矛盾
                    # 
                    # 注意：如果未来需要对扩展记忆过滤，可以在这里添加逻辑
                    # if not is_initial_memory and some_score < threshold:
                    #     continue
                    
                    # 记录通过过滤的记忆（用于调试）
                    if is_initial_memory:
                        logger.debug(
                            f"✅ 保留 {memory.id[:8]} [初始]: 相似度={true_similarity:.3f}, "
                            f"重要性={memory.importance:.2f}, 综合分数={final_score:.4f}"
                        )
                    else:
                        logger.debug(
                            f"✅ 保留 {memory.id[:8]} [扩展]: 重要性={memory.importance:.2f}, "
                            f"综合分数={final_score:.4f}"
                        )
                    
                    # 🆕 节点类型加权：对REFERENCE/ATTRIBUTE节点额外加分（促进事实性信息召回）
                    if "REFERENCE" in node_types_count or "ATTRIBUTE" in node_types_count:
                        final_score *= 1.1  # 10% 加成
                    
                    # 🆕 用户指定的优先节点类型额外加权
                    if prefer_node_types:
                        for prefer_type in prefer_node_types:
                            if prefer_type in node_types_count:
                                final_score *= 1.15  # 15% 额外加成
                                logger.debug(f"记忆 {memory.id[:8]} 包含优先节点类型 {prefer_type}，加权后分数: {final_score:.4f}")
                                break
                    
                    memories_with_scores.append((memory, final_score, dominant_node_type))

            # 按综合分数排序
            memories_with_scores.sort(key=lambda x: x[1], reverse=True)
            memories = [mem for mem, _, _ in memories_with_scores[:top_k]]

            # 统计过滤情况
            total_candidates = len(all_memory_ids)
            filtered_count = total_candidates - len(memories_with_scores)
            
            # 6. 格式化结果（包含调试信息）
            results = []
            for memory, score, node_type in memories_with_scores[:top_k]:
                result = {
                    "memory_id": memory.id,
                    "importance": memory.importance,
                    "created_at": memory.created_at.isoformat(),
                    "summary": self._summarize_memory(memory),
                    "score": round(score, 4),  # 🆕 暴露最终分数，便于调试
                    "dominant_node_type": node_type,  # 🆕 暴露节点类型
                }
                results.append(result)

            logger.info(
                f"搜索完成: 初始{len(initial_memory_ids)}个 → "
                f"扩展{len(expanded_memory_scores)}个 → "
                f"候选{total_candidates}个 → "
                f"过滤{filtered_count}个 (重要性过滤) → "
                f"最终返回{len(results)}条记忆"
            )
            
            # 如果过滤率过高，发出警告
            if total_candidates > 0:
                filter_rate = filtered_count / total_candidates
                if filter_rate > 0.5:  # 降低警告阈值到50%
                    logger.warning(
                        f"⚠️ 过滤率较高 ({filter_rate*100:.1f}%)！"
                        f"原因：{filter_stats['importance']}个记忆重要性 < {self.search_min_importance}。"
                        f"建议：1) 降低 min_importance 阈值，或 2) 检查记忆质量评分"
                    )

            return {
                "success": True,
                "results": results,
                "total": len(results),
                "query": query,
                "strategy": "multi_query" if use_multi_query else "single_query",
                "expanded_count": len(expanded_memory_scores),
                "expand_depth": expand_depth,
            }

        except Exception as e:
            logger.error(f"记忆搜索失败: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "message": "记忆搜索失败",
                "results": [],
            }

    async def _generate_multi_queries_simple(
        self, query: str, context: dict[str, Any] | None = None
    ) -> tuple[list[tuple[str, float]], list[str]]:
        """
        简化版多查询生成（直接在 Tools 层实现，避免循环依赖）

        让小模型直接生成3-5个不同角度的查询语句，并识别偏好的节点类型。
        
        Returns:
            (查询列表, 偏好节点类型列表)
        """
        try:
            from src.config.config import model_config
            from src.llm_models.utils_model import LLMRequest

            llm = LLMRequest(
                model_set=model_config.model_task_config.utils_small,
                request_type="memory.multi_query"
            )

            # 获取上下文信息
            participants = context.get("participants", []) if context else []
            chat_history = context.get("chat_history", "") if context else ""
            sender = context.get("sender", "") if context else ""

            # 处理聊天历史，提取最近5条左右的对话
            recent_chat = ""
            if chat_history:
                lines = chat_history.strip().split("\n")
                # 取最近5条消息
                recent_lines = lines[-5:] if len(lines) > 5 else lines
                recent_chat = "\n".join(recent_lines)

            prompt = f"""基于聊天上下文为查询生成3-5个不同角度的搜索语句，并识别查询意图对应的记忆类型（JSON格式）。

**当前查询：** {query}
**发送者：** {sender if sender else '未知'}
**参与者：** {', '.join(participants) if participants else '无'}
**当前时间：** {__import__('datetime').datetime.now().__str__()}

**最近聊天记录（最近5条）：**
{recent_chat if recent_chat else '无聊天历史'}

---

## 第一步：分析查询意图与记忆类型

### 记忆类型识别表（按优先级判断）

| 查询特征 | 偏好节点类型 | 示例 |
|---------|-------------|------|
| 🔗 **查找链接/地址/URL/网址/文档位置** | `REFERENCE` | "xxx的文档地址"、"那个网站链接" |
| ⚙️ **查询配置/参数/设置/属性值** | `ATTRIBUTE` | "Python版本是多少"、"数据库配置" |
| 👤 **询问人物/组织/实体身份** | `ENTITY` | "拾风是谁"、"MoFox团队成员" |
| 🔄 **询问关系/人际/交互** | `RELATION` | "我和机器人的关系"、"谁认识谁" |
| 📅 **回忆事件/对话/活动** | `EVENT` | "上次聊了什么"、"昨天的会议" |
| 💡 **查询概念/定义/知识** | 无特定偏好 | "什么是记忆图谱" |

### 判断规则
- 如果查询包含"地址"、"链接"、"URL"、"网址"、"文档"等关键词 → `REFERENCE`
- 如果查询包含"配置"、"参数"、"设置"、"版本"、"属性"等关键词 → `ATTRIBUTE`
- 如果查询询问"是谁"、"什么人"、"团队"、"组织"等 → `ENTITY`
- 如果查询询问"关系"、"朋友"、"认识"等 → `RELATION`
- 如果查询回忆"上次"、"之前"、"讨论过"、"聊过"等 → `EVENT`
- 如果无明确特征 → 不指定类型（空列表）

---

## 第二步：生成多角度查询

### 分析原则
1. **上下文理解**：根据聊天历史理解查询的真实意图
2. **指代消解**：识别并代换"他"、"她"、"它"、"那个"等指代词为具体实体名
3. **话题关联**：结合最近讨论的话题生成更精准的查询
4. **查询分解**：对复杂查询分解为多个子查询
5. **实体提取**：显式提取查询中的关键实体（人名、项目名、组织名等）

### 生成策略（按顺序）
1. **完整查询**（权重1.0）：结合上下文的完整查询，包含指代消解后的实体名
2. **关键实体查询**（权重0.9）：只包含核心实体，去除修饰词（如"xxx的"→"xxx"）
3. **同义表达查询**（权重0.8）：用不同表达方式重述查询意图
4. **话题扩展查询**（权重0.7）：基于最近聊天话题的相关查询
5. **时间范围查询**（权重0.6，如适用）：如果涉及时间，生成具体时间范围

---

## 输出格式（严格JSON）

```json
{{
  "prefer_node_types": ["REFERENCE", "ATTRIBUTE"],
  "queries": [
    {{"text": "完整查询（已消解指代）", "weight": 1.0}},
    {{"text": "核心实体查询", "weight": 0.9}},
    {{"text": "同义表达查询", "weight": 0.8}}
  ]
}}
```

**字段说明**：
- `prefer_node_types`: 偏好的节点类型数组，可选值：`REFERENCE`、`ATTRIBUTE`、`ENTITY`、`RELATION`、`EVENT`，如无明确特征则为空数组`[]`
- `queries`: 查询数组，每个查询包含`text`（查询文本）和`weight`（权重0.5-1.0）

---

## 示例

### 示例1：查询文档地址
**输入**：
- 查询："你知道MoFox-Bot的文档地址吗？"
- 聊天历史：无

**输出**：
```json
{{
  "prefer_node_types": ["REFERENCE"],
  "queries": [
    {{"text": "MoFox-Bot文档地址", "weight": 1.0}},
    {{"text": "MoFox-Bot", "weight": 0.9}},
    {{"text": "MoFox-Bot官方文档URL", "weight": 0.8}}
  ]
}}
```

### 示例2：查询人物关系
**输入**：
- 查询："拾风是谁？"
- 聊天历史：提到过"拾风和杰瑞喵"

**输出**：
```json
{{
  "prefer_node_types": ["ENTITY", "RELATION"],
  "queries": [
    {{"text": "拾风身份信息", "weight": 1.0}},
    {{"text": "拾风", "weight": 0.9}},
    {{"text": "拾风和杰瑞喵的关系", "weight": 0.8}}
  ]
}}
```

### 示例3：查询配置参数
**输入**：
- 查询："Python版本是多少？"
- 聊天历史：讨论过"项目环境配置"

**输出**：
```json
{{
  "prefer_node_types": ["ATTRIBUTE"],
  "queries": [
    {{"text": "Python版本号", "weight": 1.0}},
    {{"text": "Python配置", "weight": 0.9}},
    {{"text": "项目Python环境版本", "weight": 0.8}}
  ]
}}
```

### 示例4：回忆对话（无明确类型）
**输入**：
- 查询："我们上次聊了什么？"
- 聊天历史：最近讨论"记忆系统优化"

**输出**：
```json
{{
  "prefer_node_types": ["EVENT"],
  "queries": [
    {{"text": "最近对话内容", "weight": 1.0}},
    {{"text": "记忆系统优化讨论", "weight": 0.9}},
    {{"text": "上次聊天记录", "weight": 0.8}}
  ]
}}
```

---

**现在请根据上述规则生成输出（仅输出JSON，不要其他内容）：**
"""

            response, _ = await llm.generate_response_async(prompt, temperature=0.3, max_tokens=300)

            import re
            import orjson
            
            # 清理Markdown代码块
            response = re.sub(r"```json\s*", "", response)
            response = re.sub(r"```\s*$", "", response).strip()

            # 解析JSON
            data = orjson.loads(response)
            
            # 提取查询列表
            queries = data.get("queries", [])
            result_queries = [(item.get("text", "").strip(), float(item.get("weight", 0.5)))
                             for item in queries if item.get("text", "").strip()]
            
            # 提取偏好节点类型
            prefer_node_types = data.get("prefer_node_types", [])
            # 确保类型正确且有效
            valid_types = {"REFERENCE", "ATTRIBUTE", "ENTITY", "RELATION", "EVENT"}
            prefer_node_types = [t for t in prefer_node_types if t in valid_types]

            if result_queries:
                logger.info(
                    f"生成查询: {[q for q, _ in result_queries]} "
                    f"(偏好类型: {prefer_node_types if prefer_node_types else '无'})"
                )
                return result_queries, prefer_node_types

        except Exception as e:
            logger.warning(f"多查询生成失败: {e}")

        # 降级：返回原始查询和空的节点类型列表
        return [(query, 1.0)], []

    async def _single_query_search(
        self, query: str, top_k: int
    ) -> list[tuple[str, float, dict[str, Any]]]:
        """
        传统的单查询搜索

        Args:
            query: 查询字符串
            top_k: 返回结果数

        Returns:
            相似节点列表 [(node_id, similarity, metadata), ...]
        """
        # 生成查询嵌入
        query_embedding = None
        if self.builder.embedding_generator:
            query_embedding = await self.builder.embedding_generator.generate(query)

        # 如果嵌入生成失败，无法进行向量搜索
        if query_embedding is None:
            logger.warning("嵌入生成失败，跳过节点搜索")
            return []

        # 向量搜索（增加返回数量以提高召回率）
        similar_nodes = await self.vector_store.search_similar_nodes(
            query_embedding=query_embedding,
            limit=top_k * 5,  # 🔥 从2倍提升到5倍，提高初始召回率
            min_similarity=0.0,  # 不在这里过滤，交给后续评分
        )
        
        logger.debug(f"单查询向量搜索: 查询='{query}', 返回节点数={len(similar_nodes)}")
        if similar_nodes:
            logger.debug(f"Top 3相似度: {[f'{sim:.3f}' for _, sim, _ in similar_nodes[:3]]}")

        return similar_nodes

    async def _multi_query_search(
        self, query: str, top_k: int, context: dict[str, Any] | None = None
    ) -> tuple[list[tuple[str, float, dict[str, Any]]], list[str]]:
        """
        多查询策略搜索（简化版 + 节点类型识别）

        直接使用小模型生成多个查询，并识别查询意图对应的偏好节点类型。

        步骤：
        1. 让小模型生成3-5个不同角度的查询 + 识别偏好节点类型
        2. 为每个查询生成嵌入
        3. 并行搜索并融合结果

        Args:
            query: 查询字符串
            top_k: 返回结果数
            context: 查询上下文

        Returns:
            (融合后的相似节点列表, 偏好节点类型列表)
        """
        try:
            # 1. 使用小模型生成多个查询 + 节点类型识别
            multi_queries, prefer_node_types = await self._generate_multi_queries_simple(query, context)

            logger.debug(f"生成 {len(multi_queries)} 个查询: {multi_queries}, 偏好类型: {prefer_node_types}")

            # 2. 生成所有查询的嵌入
            if not self.builder.embedding_generator:
                logger.warning("未配置嵌入生成器，回退到单查询模式")
                single_results = await self._single_query_search(query, top_k)
                return single_results, prefer_node_types

            query_embeddings = []
            query_weights = []

            for sub_query, weight in multi_queries:
                embedding = await self.builder.embedding_generator.generate(sub_query)
                if embedding is not None:
                    query_embeddings.append(embedding)
                    query_weights.append(weight)

            # 如果所有嵌入都生成失败，回退到单查询模式
            if not query_embeddings:
                logger.warning("所有查询嵌入生成失败，回退到单查询模式")
                single_results = await self._single_query_search(query, top_k)
                return single_results, prefer_node_types

            # 3. 多查询融合搜索
            similar_nodes = await self.vector_store.search_with_multiple_queries(
                query_embeddings=query_embeddings,
                query_weights=query_weights,
                limit=top_k * 5,  # 🔥 从2倍提升到5倍，提高初始召回率
                fusion_strategy="weighted_max",
            )

            logger.info(f"多查询检索完成: {len(similar_nodes)} 个节点 (偏好类型: {prefer_node_types})")
            if similar_nodes:
                logger.debug(f"Top 5融合相似度: {[f'{sim:.3f}' for _, sim, _ in similar_nodes[:5]]}")

            return similar_nodes, prefer_node_types

        except Exception as e:
            logger.warning(f"多查询搜索失败，回退到单查询模式: {e}", exc_info=True)
            single_results = await self._single_query_search(query, top_k)
            return single_results, []

    async def _add_memory_to_stores(self, memory: Memory):
        """将记忆添加到存储"""
        # 1. 添加到图存储
        self.graph_store.add_memory(memory)

        # 2. 添加有嵌入的节点到向量存储
        for node in memory.nodes:
            if node.embedding is not None:
                await self.vector_store.add_node(node)

    async def _find_memory_by_description(self, description: str) -> Memory | None:
        """
        通过描述查找记忆

        Args:
            description: 记忆描述

        Returns:
            找到的记忆，如果没有则返回 None
        """
        # 使用语义搜索查找最相关的记忆
        query_embedding = None
        if self.builder.embedding_generator:
            query_embedding = await self.builder.embedding_generator.generate(description)

        # 如果嵌入生成失败，无法进行语义搜索
        if query_embedding is None:
            logger.debug("嵌入生成失败，跳过描述搜索")
            return None

        # 搜索相似节点
        similar_nodes = await self.vector_store.search_similar_nodes(
            query_embedding=query_embedding,
            limit=5,
        )

        if not similar_nodes:
            return None

        # 获取最相似节点关联的记忆
        _node_id, _similarity, metadata = similar_nodes[0]

        if "memory_ids" not in metadata or not metadata["memory_ids"]:
            return None

        ids = metadata["memory_ids"]

        # 确保是列表
        if isinstance(ids, str):
            import orjson
            try:
                ids = orjson.loads(ids)
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
    def get_all_tool_schemas() -> list[dict[str, Any]]:
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
