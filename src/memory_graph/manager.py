"""
记忆管理器 - Phase 3

统一的记忆系统管理接口，整合所有组件：
- 记忆创建、检索、更新、删除
- 记忆生命周期管理（激活、遗忘）
- 记忆整合与维护
- 多策略检索优化
"""

import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from src.config.config import global_config
from src.config.official_configs import MemoryConfig
from src.memory_graph.core.builder import MemoryBuilder
from src.memory_graph.core.extractor import MemoryExtractor
from src.memory_graph.models import Memory, MemoryEdge, MemoryNode, MemoryType, NodeType, EdgeType
from src.memory_graph.storage.graph_store import GraphStore
from src.memory_graph.storage.persistence import PersistenceManager
from src.memory_graph.storage.vector_store import VectorStore
from src.memory_graph.tools.memory_tools import MemoryTools
from src.memory_graph.utils.embeddings import EmbeddingGenerator
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

logger = logging.getLogger(__name__)


class MemoryManager:
    """
    记忆管理器
    
    核心管理类，提供记忆系统的统一接口：
    - 记忆 CRUD 操作
    - 记忆生命周期管理
    - 智能检索与推荐
    - 记忆维护与优化
    """

    def __init__(
        self,
        data_dir: Optional[Path] = None,
    ):
        """
        初始化记忆管理器
        
        Args:
            data_dir: 数据目录（可选，默认从global_config读取）
        """
        # 直接使用 global_config.memory
        if not global_config.memory or not getattr(global_config.memory, 'enable', False):
            raise ValueError("记忆系统未启用，请在配置文件中启用 [memory] enable = true")
        
        self.config: MemoryConfig = global_config.memory
        self.data_dir = data_dir or Path(getattr(self.config, 'data_dir', 'data/memory_graph'))
        
        # 存储组件
        self.vector_store: Optional[VectorStore] = None
        self.graph_store: Optional[GraphStore] = None
        self.persistence: Optional[PersistenceManager] = None
        
        # 核心组件
        self.embedding_generator: Optional[EmbeddingGenerator] = None
        self.extractor: Optional[MemoryExtractor] = None
        self.builder: Optional[MemoryBuilder] = None
        self.tools: Optional[MemoryTools] = None
        
        # 状态
        self._initialized = False
        self._last_maintenance = datetime.now()
        self._maintenance_task: Optional[asyncio.Task] = None
        self._maintenance_interval_hours = getattr(self.config, 'consolidation_interval_hours', 1.0)
        self._maintenance_schedule_id: Optional[str] = None  # 调度任务ID
        
        logger.info(f"记忆管理器已创建 (data_dir={self.data_dir}, enable={getattr(self.config, 'enable', False)})")

    async def initialize(self) -> None:
        """
        初始化所有组件
        
        按照依赖顺序初始化：
        1. 存储层（向量存储、图存储、持久化）
        2. 工具层（嵌入生成器、提取器）
        3. 管理层（构建器、工具接口）
        """
        if self._initialized:
            logger.warning("记忆管理器已经初始化")
            return

        try:
            logger.info("开始初始化记忆管理器...")
            
            # 1. 初始化存储层
            self.data_dir.mkdir(parents=True, exist_ok=True)
            
            # 获取存储配置
            storage_config = getattr(self.config, 'storage', None)
            vector_collection_name = getattr(storage_config, 'vector_collection_name', 'memory_graph') if storage_config else 'memory_graph'
            
            self.vector_store = VectorStore(
                collection_name=vector_collection_name,
                data_dir=self.data_dir,
            )
            await self.vector_store.initialize()
            
            self.persistence = PersistenceManager(data_dir=self.data_dir)
            
            # 尝试加载现有图数据
            self.graph_store = await self.persistence.load_graph_store()
            if not self.graph_store:
                logger.info("未找到现有图数据，创建新的图存储")
                self.graph_store = GraphStore()
            else:
                stats = self.graph_store.get_statistics()
                logger.info(
                    f"加载图数据: {stats['total_memories']} 条记忆, "
                    f"{stats['total_nodes']} 个节点, {stats['total_edges']} 条边"
                )
            
            # 2. 初始化工具层
            self.embedding_generator = EmbeddingGenerator()
            # EmbeddingGenerator 使用延迟初始化，在第一次调用时自动初始化
            
            self.extractor = MemoryExtractor()
            
            # 3. 初始化管理层
            self.builder = MemoryBuilder(
                vector_store=self.vector_store,
                graph_store=self.graph_store,
                embedding_generator=self.embedding_generator,
            )
            
            # 检查配置值
            expand_depth = self.config.search_max_expand_depth
            expand_semantic_threshold = self.config.search_expand_semantic_threshold
            logger.info(f"📊 配置检查: search_max_expand_depth={expand_depth}, search_expand_semantic_threshold={expand_semantic_threshold}")

            self.tools = MemoryTools(
                vector_store=self.vector_store,
                graph_store=self.graph_store,
                persistence_manager=self.persistence,
                embedding_generator=self.embedding_generator,
                max_expand_depth=expand_depth,  # 从配置读取图扩展深度
                expand_semantic_threshold=expand_semantic_threshold,  # 从配置读取图扩展语义阈值
            )
            
            self._initialized = True
            logger.info("✅ 记忆管理器初始化完成")
            
            # 启动后台维护调度任务
            await self.start_maintenance_scheduler()
            
        except Exception as e:
            logger.error(f"记忆管理器初始化失败: {e}", exc_info=True)
            raise

    async def shutdown(self) -> None:
        """
        关闭记忆管理器
        
        执行清理操作：
        - 停止维护调度任务
        - 保存所有数据
        - 关闭存储组件
        """
        if not self._initialized:
            logger.warning("记忆管理器未初始化，无需关闭")
            return

        try:
            logger.info("正在关闭记忆管理器...")
            
            # 1. 停止调度任务
            await self.stop_maintenance_scheduler()
            
            # 2. 执行最后一次维护（保存数据）
            if self.graph_store and self.persistence:
                logger.info("执行最终数据保存...")
                await self.persistence.save_graph_store(self.graph_store)
            
            # 3. 关闭存储组件
            if self.vector_store:
                # VectorStore 使用 chromadb，无需显式关闭
                pass
            
            self._initialized = False
            logger.info("✅ 记忆管理器已关闭")
            
        except Exception as e:
            logger.error(f"关闭记忆管理器失败: {e}", exc_info=True)

    # ==================== 记忆 CRUD 操作 ====================

    async def create_memory(
        self,
        subject: str,
        memory_type: str,
        topic: str,
        object: Optional[str] = None,
        attributes: Optional[Dict[str, str]] = None,
        importance: float = 0.5,
        **kwargs,
    ) -> Optional[Memory]:
        """
        创建新记忆
        
        Args:
            subject: 主体（谁）
            memory_type: 记忆类型（事件/观点/事实/关系）
            topic: 主题（做什么/想什么）
            object: 客体（对谁/对什么）
            attributes: 属性字典（时间、地点、原因等）
            importance: 重要性 (0.0-1.0)
            **kwargs: 其他参数
            
        Returns:
            创建的记忆对象，失败返回 None
        """
        if not self._initialized:
            await self.initialize()

        try:
            result = await self.tools.create_memory(
                subject=subject,
                memory_type=memory_type,
                topic=topic,
                object=object,
                attributes=attributes,
                importance=importance,
                **kwargs,
            )
            
            if result["success"]:
                memory_id = result["memory_id"]
                memory = self.graph_store.get_memory_by_id(memory_id)
                logger.info(f"记忆创建成功: {memory_id}")
                return memory
            else:
                logger.error(f"记忆创建失败: {result.get('error', 'Unknown error')}")
                return None
                
        except Exception as e:
            logger.error(f"创建记忆时发生异常: {e}", exc_info=True)
            return None

    async def get_memory(self, memory_id: str) -> Optional[Memory]:
        """
        根据 ID 获取记忆
        
        Args:
            memory_id: 记忆 ID
            
        Returns:
            记忆对象，不存在返回 None
        """
        if not self._initialized:
            await self.initialize()

        return self.graph_store.get_memory_by_id(memory_id)

    async def update_memory(
        self,
        memory_id: str,
        **updates,
    ) -> bool:
        """
        更新记忆
        
        Args:
            memory_id: 记忆 ID
            **updates: 要更新的字段
            
        Returns:
            是否更新成功
        """
        if not self._initialized:
            await self.initialize()

        try:
            memory = self.graph_store.get_memory_by_id(memory_id)
            if not memory:
                logger.warning(f"记忆不存在: {memory_id}")
                return False
            
            # 更新元数据
            if "importance" in updates:
                memory.importance = updates["importance"]
            
            if "metadata" in updates:
                memory.metadata.update(updates["metadata"])
            
            memory.updated_at = datetime.now()
            
            # 保存更新
            await self.persistence.save_graph_store(self.graph_store)
            logger.info(f"记忆更新成功: {memory_id}")
            return True
            
        except Exception as e:
            logger.error(f"更新记忆失败: {e}", exc_info=True)
            return False

    async def delete_memory(self, memory_id: str) -> bool:
        """
        删除记忆
        
        Args:
            memory_id: 记忆 ID
            
        Returns:
            是否删除成功
        """
        if not self._initialized:
            await self.initialize()

        try:
            memory = self.graph_store.get_memory_by_id(memory_id)
            if not memory:
                logger.warning(f"记忆不存在: {memory_id}")
                return False
            
            # 从向量存储删除节点
            for node in memory.nodes:
                if node.embedding is not None:
                    await self.vector_store.delete_node(node.id)
            
            # 从图存储删除记忆
            self.graph_store.remove_memory(memory_id)
            
            # 保存更新
            await self.persistence.save_graph_store(self.graph_store)
            logger.info(f"记忆删除成功: {memory_id}")
            return True
            
        except Exception as e:
            logger.error(f"删除记忆失败: {e}", exc_info=True)
            return False

    # ==================== 记忆检索操作 ====================

    async def generate_multi_queries(
        self,
        query: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> List[Tuple[str, float]]:
        """
        使用小模型生成多个查询语句（用于多路召回）
        
        简化版多查询策略：直接让小模型生成3-5个不同角度的查询，
        避免复杂的查询分解和组合逻辑。
        
        Args:
            query: 原始查询
            context: 上下文信息（聊天历史、发言人、参与者等）
            
        Returns:
            List of (query_string, weight) - 查询语句和权重
        """
        try:
            from src.llm_models.utils_model import LLMRequest
            from src.config.config import model_config
            
            llm = LLMRequest(
                model_set=model_config.model_task_config.utils_small,
                request_type="memory.multi_query_generator"
            )
            
            # 构建上下文信息
            chat_history = context.get("chat_history", "") if context else ""
    
            prompt = f"""你是记忆检索助手。为提高检索准确率，请为查询生成3-5个不同角度的搜索语句。

**核心原则（重要！）：**
对于包含多个概念的复杂查询（如"小明如何评价小王"），应该生成：
1. 完整查询（包含所有要素）- 权重1.0
2. 每个关键概念的独立查询（如"小明"、"小王"）- 权重0.8，避免被主体淹没！
3. 主体+动作组合（如"小明 评价"）- 权重0.6
4. 泛化查询（如"评价"）- 权重0.7

**要求：**
- 第一个必须是原始查询或同义改写
- 识别查询中的所有重要概念，为每个概念生成独立查询
- 查询简洁（5-20字）
- 直接输出JSON，不要添加说明

**对话上下文：** {chat_history[-300:] if chat_history else "无"}

**输出JSON格式：**
```json
{{
    "queries": [
        {{"text": "完整查询", "weight": 1.0}},
        {{"text": "关键概念1", "weight": 0.8}},
        {{"text": "关键概念2", "weight": 0.8}},
        {{"text": "组合查询", "weight": 0.6}}
    ]
}}
```"""

            response, _ = await llm.generate_response_async(prompt, temperature=0.3, max_tokens=300)
            
            # 解析JSON
            import json, re
            response = re.sub(r'```json\s*', '', response)
            response = re.sub(r'```\s*$', '', response).strip()
            
            try:
                data = json.loads(response)
                queries = data.get("queries", [])
                
                result = []
                for item in queries:
                    text = item.get("text", "").strip()
                    weight = float(item.get("weight", 0.5))
                    if text:
                        result.append((text, weight))
                
                if result:
                    logger.info(f"生成 {len(result)} 个查询: {[q for q, _ in result]}")
                    return result
                    
            except json.JSONDecodeError as e:
                logger.warning(f"解析失败: {e}, response={response[:100]}")
            
        except Exception as e:
            logger.warning(f"多查询生成失败: {e}")
        
        # 回退到原始查询
        return [(query, 1.0)]

    async def search_memories(
        self,
        query: str,
        top_k: int = 10,
        memory_types: Optional[List[str]] = None,
        time_range: Optional[Tuple[datetime, datetime]] = None,
        min_importance: float = 0.0,
        include_forgotten: bool = False,
        use_multi_query: bool = True,
        expand_depth: int | None = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> List[Memory]:
        """
        搜索记忆
        
        使用多策略检索优化，解决复杂查询问题。
        例如："杰瑞喵如何评价新的记忆系统" 会被分解为多个子查询，
        确保同时匹配"杰瑞喵"和"新的记忆系统"两个关键概念。
        
        同时支持图扩展：从初始检索结果出发，沿图结构查找语义相关的邻居记忆。
        
        Args:
            query: 搜索查询
            top_k: 返回结果数
            memory_types: 记忆类型过滤
            time_range: 时间范围过滤 (start, end)
            min_importance: 最小重要性
            include_forgotten: 是否包含已遗忘的记忆
            use_multi_query: 是否使用多查询策略（推荐，默认True）
            expand_depth: 图扩展深度（0=禁用, 1=推荐, 2-3=深度探索）
            context: 查询上下文（用于优化）
            
        Returns:
            记忆列表
        """
        if not self._initialized:
            await self.initialize()

        try:
            # 准备搜索参数
            params = {
                "query": query,
                "top_k": top_k,
                "use_multi_query": use_multi_query,
                "expand_depth": expand_depth or global_config.memory.search_max_expand_depth,  # 传递图扩展深度
                "context": context,
            }
            
            if memory_types:
                params["memory_types"] = memory_types
            
            # 执行搜索
            result = await self.tools.search_memories(**params)
            
            if not result["success"]:
                logger.error(f"搜索失败: {result.get('error', 'Unknown error')}")
                return []
            
            memories = result.get("results", [])
            
            # 后处理过滤
            filtered_memories = []
            for mem_dict in memories:
                # 从字典重建 Memory 对象
                memory_id = mem_dict.get("memory_id", "")
                if not memory_id:
                    continue
                    
                memory = self.graph_store.get_memory_by_id(memory_id)
                if not memory:
                    continue
                
                # 重要性过滤
                if min_importance is not None and memory.importance < min_importance:
                    continue
                
                # 遗忘状态过滤
                if not include_forgotten and memory.metadata.get("forgotten", False):
                    continue
                
                # 时间范围过滤
                if time_range:
                    mem_time = memory.created_at
                    if not (time_range[0] <= mem_time <= time_range[1]):
                        continue
                
                filtered_memories.append(memory)
            
            strategy = result.get("strategy", "unknown")
            logger.info(
                f"搜索完成: 找到 {len(filtered_memories)} 条记忆 (策略={strategy})"
            )
            return filtered_memories[:top_k]
            
        except Exception as e:
            logger.error(f"搜索记忆失败: {e}", exc_info=True)
            return []

    async def link_memories(
        self,
        source_description: str,
        target_description: str,
        relation_type: str,
        importance: float = 0.5,
    ) -> bool:
        """
        关联两条记忆
        
        Args:
            source_description: 源记忆描述
            target_description: 目标记忆描述
            relation_type: 关系类型（导致/引用/相似/相反）
            importance: 关系重要性
            
        Returns:
            是否关联成功
        """
        if not self._initialized:
            await self.initialize()

        try:
            result = await self.tools.link_memories(
                source_memory_description=source_description,
                target_memory_description=target_description,
                relation_type=relation_type,
                importance=importance,
            )
            
            if result["success"]:
                logger.info(
                    f"记忆关联成功: {result['source_memory_id']} -> "
                    f"{result['target_memory_id']} ({relation_type})"
                )
                return True
            else:
                logger.error(f"记忆关联失败: {result.get('error', 'Unknown error')}")
                return False
                
        except Exception as e:
            logger.error(f"关联记忆失败: {e}", exc_info=True)
            return False

    # ==================== 记忆生命周期管理 ====================

    async def activate_memory(self, memory_id: str, strength: float = 1.0) -> bool:
        """
        激活记忆
        
        更新记忆的激活度，并传播到相关记忆
        
        Args:
            memory_id: 记忆 ID
            strength: 激活强度 (0.0-1.0)
            
        Returns:
            是否激活成功
        """
        if not self._initialized:
            await self.initialize()

        try:
            memory = self.graph_store.get_memory_by_id(memory_id)
            if not memory:
                logger.warning(f"记忆不存在: {memory_id}")
                return False
            
            # 更新激活信息
            now = datetime.now()
            activation_info = memory.metadata.get("activation", {})
            
            # 更新激活度（考虑时间衰减）
            last_access = activation_info.get("last_access")
            if last_access:
                # 计算时间衰减
                last_access_dt = datetime.fromisoformat(last_access)
                hours_passed = (now - last_access_dt).total_seconds() / 3600
                decay_rate = getattr(self.config, 'activation_decay_rate', 0.95)
                decay_factor = decay_rate ** (hours_passed / 24)
                current_activation = activation_info.get("level", 0.0) * decay_factor
            else:
                current_activation = 0.0
            
            # 新的激活度 = 当前激活度 + 激活强度
            new_activation = min(1.0, current_activation + strength)
            
            activation_info.update({
                "level": new_activation,
                "last_access": now.isoformat(),
                "access_count": activation_info.get("access_count", 0) + 1,
            })
            
            memory.metadata["activation"] = activation_info
            memory.last_accessed = now
            
            # 激活传播：激活相关记忆
            if strength > 0.1:  # 只有足够强的激活才传播
                propagation_depth = getattr(self.config, 'activation_propagation_depth', 2)
                related_memories = self._get_related_memories(
                    memory_id,
                    max_depth=propagation_depth
                )
                propagation_strength_factor = getattr(self.config, 'activation_propagation_strength', 0.5)
                propagation_strength = strength * propagation_strength_factor
                
                max_related = getattr(self.config, 'max_related_memories', 5)
                for related_id in related_memories[:max_related]:
                    await self.activate_memory(related_id, propagation_strength)
            
            # 保存更新
            await self.persistence.save_graph_store(self.graph_store)
            logger.debug(f"记忆已激活: {memory_id} (level={new_activation:.3f})")
            return True
            
        except Exception as e:
            logger.error(f"激活记忆失败: {e}", exc_info=True)
            return False

    def _get_related_memories(self, memory_id: str, max_depth: int = 1) -> List[str]:
        """
        获取相关记忆 ID 列表（旧版本，保留用于激活传播）
        
        Args:
            memory_id: 记忆 ID
            max_depth: 最大遍历深度
            
        Returns:
            相关记忆 ID 列表
        """
        memory = self.graph_store.get_memory_by_id(memory_id)
        if not memory:
            return []
        
        related_ids = set()
        
        # 遍历记忆的节点
        for node in memory.nodes:
            # 获取节点的邻居
            neighbors = list(self.graph_store.graph.neighbors(node.id))
            
            for neighbor_id in neighbors:
                # 获取邻居节点所属的记忆
                neighbor_node = self.graph_store.graph.nodes.get(neighbor_id)
                if neighbor_node:
                    neighbor_memory_ids = neighbor_node.get("memory_ids", [])
                    for mem_id in neighbor_memory_ids:
                        if mem_id != memory_id:
                            related_ids.add(mem_id)
        
        return list(related_ids)

    async def expand_memories_with_semantic_filter(
        self,
        initial_memory_ids: List[str],
        query_embedding: "np.ndarray",
        max_depth: int = 2,
        semantic_threshold: float = 0.5,
        max_expanded: int = 20
    ) -> List[Tuple[str, float]]:
        """
        从初始记忆集合出发，沿图结构扩展，并用语义相似度过滤
        
        这个方法解决了纯向量搜索可能遗漏的"语义相关且图结构相关"的记忆。
        
        Args:
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
            import numpy as np
            
            # 记录已访问的记忆，避免重复
            visited_memories = set(initial_memory_ids)
            # 记录扩展的记忆及其分数
            expanded_memories: Dict[str, float] = {}
            
            # BFS扩展
            current_level = initial_memory_ids
            
            for depth in range(max_depth):
                next_level = []
                
                for memory_id in current_level:
                    memory = self.graph_store.get_memory_by_id(memory_id)
                    if not memory:
                        continue
                    
                    # 遍历该记忆的所有节点
                    for node in memory.nodes:
                        if not node.has_embedding():
                            continue
                        
                        # 获取邻居节点
                        try:
                            neighbors = list(self.graph_store.graph.neighbors(node.id))
                        except:
                            continue
                        
                        for neighbor_id in neighbors:
                            # 获取邻居节点信息
                            neighbor_node_data = self.graph_store.graph.nodes.get(neighbor_id)
                            if not neighbor_node_data:
                                continue
                            
                            # 获取邻居节点的向量（从向量存储）
                            neighbor_vector_data = await self.vector_store.get_node_by_id(neighbor_id)
                            if not neighbor_vector_data or neighbor_vector_data.get("embedding") is None:
                                continue
                            
                            neighbor_embedding = neighbor_vector_data["embedding"]
                            
                            # 计算与查询的语义相似度
                            semantic_sim = self._cosine_similarity(
                                query_embedding,
                                neighbor_embedding
                            )
                            
                            # 获取边的权重
                            try:
                                edge_data = self.graph_store.graph.get_edge_data(node.id, neighbor_id)
                                edge_importance = edge_data.get("importance", 0.5) if edge_data else 0.5
                            except:
                                edge_importance = 0.5
                            
                            # 综合评分：语义相似度(70%) + 图结构权重(20%) + 深度衰减(10%)
                            depth_decay = 1.0 / (depth + 1)  # 深度越深，权重越低
                            relevance_score = (
                                semantic_sim * 0.7 + 
                                edge_importance * 0.2 + 
                                depth_decay * 0.1
                            )
                            
                            # 只保留超过阈值的节点
                            if relevance_score < semantic_threshold:
                                continue
                            
                            # 提取邻居节点所属的记忆
                            neighbor_memory_ids = neighbor_node_data.get("memory_ids", [])
                            if isinstance(neighbor_memory_ids, str):
                                import json
                                try:
                                    neighbor_memory_ids = json.loads(neighbor_memory_ids)
                                except:
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
                                        expanded_memories[neighbor_mem_id],
                                        relevance_score
                                    )
                
                # 如果没有新节点或已达到数量限制，提前终止
                if not next_level or len(expanded_memories) >= max_expanded:
                    break
                
                current_level = next_level[:max_expanded]  # 限制每层的扩展数量
            
            # 排序并返回
            sorted_results = sorted(
                expanded_memories.items(),
                key=lambda x: x[1],
                reverse=True
            )[:max_expanded]
            
            logger.info(
                f"图扩展完成: 初始{len(initial_memory_ids)}个 → "
                f"扩展{len(sorted_results)}个新记忆 "
                f"(深度={max_depth}, 阈值={semantic_threshold:.2f})"
            )
            
            return sorted_results
            
        except Exception as e:
            logger.error(f"语义图扩展失败: {e}", exc_info=True)
            return []
    
    def _cosine_similarity(self, vec1: "np.ndarray", vec2: "np.ndarray") -> float:
        """计算余弦相似度"""
        try:
            import numpy as np
            
            # 确保是numpy数组
            if not isinstance(vec1, np.ndarray):
                vec1 = np.array(vec1)
            if not isinstance(vec2, np.ndarray):
                vec2 = np.array(vec2)
            
            # 归一化
            vec1_norm = np.linalg.norm(vec1)
            vec2_norm = np.linalg.norm(vec2)
            
            if vec1_norm == 0 or vec2_norm == 0:
                return 0.0
            
            # 余弦相似度
            similarity = np.dot(vec1, vec2) / (vec1_norm * vec2_norm)
            return float(similarity)
            
        except Exception as e:
            logger.warning(f"计算余弦相似度失败: {e}")
            return 0.0

    async def forget_memory(self, memory_id: str) -> bool:
        """
        遗忘记忆（标记为已遗忘，不删除）
        
        Args:
            memory_id: 记忆 ID
            
        Returns:
            是否遗忘成功
        """
        if not self._initialized:
            await self.initialize()

        try:
            memory = self.graph_store.get_memory_by_id(memory_id)
            if not memory:
                logger.warning(f"记忆不存在: {memory_id}")
                return False
            
            memory.metadata["forgotten"] = True
            memory.metadata["forgotten_at"] = datetime.now().isoformat()
            
            # 保存更新
            await self.persistence.save_graph_store(self.graph_store)
            logger.info(f"记忆已遗忘: {memory_id}")
            return True
            
        except Exception as e:
            logger.error(f"遗忘记忆失败: {e}", exc_info=True)
            return False

    async def auto_forget_memories(self, threshold: float = 0.1) -> int:
        """
        自动遗忘低激活度的记忆
        
        Args:
            threshold: 激活度阈值
            
        Returns:
            遗忘的记忆数量
        """
        if not self._initialized:
            await self.initialize()

        try:
            forgotten_count = 0
            all_memories = self.graph_store.get_all_memories()
            
            for memory in all_memories:
                # 跳过已遗忘的记忆
                if memory.metadata.get("forgotten", False):
                    continue
                
                # 跳过高重要性记忆
                min_importance = getattr(self.config, 'forgetting_min_importance', 7.0)
                if memory.importance >= min_importance:
                    continue
                
                # 计算当前激活度
                activation_info = memory.metadata.get("activation", {})
                last_access = activation_info.get("last_access")
                
                if last_access:
                    last_access_dt = datetime.fromisoformat(last_access)
                    days_passed = (datetime.now() - last_access_dt).days
                    
                    # 长时间未访问的记忆，应用时间衰减
                    decay_factor = 0.9 ** days_passed
                    current_activation = activation_info.get("level", 0.0) * decay_factor
                    
                    # 低于阈值则遗忘
                    if current_activation < threshold:
                        await self.forget_memory(memory.id)
                        forgotten_count += 1
            
            logger.info(f"自动遗忘完成: 遗忘了 {forgotten_count} 条记忆")
            return forgotten_count
            
        except Exception as e:
            logger.error(f"自动遗忘失败: {e}", exc_info=True)
            return 0

    # ==================== 统计与维护 ====================

    def get_statistics(self) -> Dict[str, Any]:
        """
        获取记忆系统统计信息
        
        Returns:
            统计信息字典
        """
        if not self._initialized or not self.graph_store:
            return {}

        stats = self.graph_store.get_statistics()
        
        # 添加激活度统计
        all_memories = self.graph_store.get_all_memories()
        activation_levels = []
        forgotten_count = 0
        
        for memory in all_memories:
            if memory.metadata.get("forgotten", False):
                forgotten_count += 1
            else:
                activation_info = memory.metadata.get("activation", {})
                activation_levels.append(activation_info.get("level", 0.0))
        
        if activation_levels:
            stats["avg_activation"] = sum(activation_levels) / len(activation_levels)
            stats["max_activation"] = max(activation_levels)
        else:
            stats["avg_activation"] = 0.0
            stats["max_activation"] = 0.0
        
        stats["forgotten_memories"] = forgotten_count
        stats["active_memories"] = stats["total_memories"] - forgotten_count
        
        return stats

    async def consolidate_memories(
        self,
        similarity_threshold: float = 0.85,
        time_window_hours: float = 24.0,
        max_batch_size: int = 50,
    ) -> Dict[str, Any]:
        """
        整理记忆：直接合并去重相似记忆（不创建新边）

        性能优化版本：
        1. 使用 asyncio.create_task 在后台执行，避免阻塞主流程
        2. 向量计算批量处理，减少重复计算
        3. 延迟保存，批量写入数据库
        4. 更频繁的协作式多任务让出

        Args:
            similarity_threshold: 相似度阈值（默认0.85，建议提高到0.9减少误判）
            time_window_hours: 时间窗口（小时）
            max_batch_size: 单次最多处理的记忆数量

        Returns:
            整理结果（如果是异步执行，返回启动状态）
        """
        if not self._initialized:
            await self.initialize()

        try:
            logger.info(f"🚀 启动记忆整理任务 (similarity_threshold={similarity_threshold}, time_window={time_window_hours}h, max_batch={max_batch_size})...")

            # 创建后台任务执行整理
            task = asyncio.create_task(
                self._consolidate_memories_background(
                    similarity_threshold=similarity_threshold,
                    time_window_hours=time_window_hours,
                    max_batch_size=max_batch_size
                )
            )

            # 返回任务启动状态，不等待完成
            return {
                "task_started": True,
                "task_id": id(task),
                "message": "记忆整理任务已在后台启动"
            }

        except Exception as e:
            logger.error(f"启动记忆整理任务失败: {e}", exc_info=True)
            return {"error": str(e), "task_started": False}

    async def _consolidate_memories_background(
        self,
        similarity_threshold: float,
        time_window_hours: float,
        max_batch_size: int,
    ) -> None:
        """
        后台执行记忆整理的具体实现

        这个方法会在独立任务中运行，不阻塞主流程
        """
        try:
            result = {
                "merged_count": 0,
                "checked_count": 0,
                "skipped_count": 0,
            }

            # 获取最近创建的记忆
            cutoff_time = datetime.now() - timedelta(hours=time_window_hours)
            all_memories = self.graph_store.get_all_memories()

            recent_memories = [
                mem for mem in all_memories
                if mem.created_at >= cutoff_time and not mem.metadata.get("forgotten", False)
            ]

            if not recent_memories:
                logger.info("✅ 记忆整理完成: 没有需要整理的记忆")
                return

            # 限制批量处理数量
            if len(recent_memories) > max_batch_size:
                logger.info(f"📊 记忆数量 {len(recent_memories)} 超过批量限制 {max_batch_size}，仅处理最新的 {max_batch_size} 条")
                recent_memories = sorted(recent_memories, key=lambda m: m.created_at, reverse=True)[:max_batch_size]
                result["skipped_count"] = len(all_memories) - max_batch_size

            logger.info(f"📋 找到 {len(recent_memories)} 条待整理记忆")
            result["checked_count"] = len(recent_memories)

            # 按记忆类型分组，减少跨类型比较
            memories_by_type: Dict[str, List[Memory]] = {}
            for mem in recent_memories:
                mem_type = mem.metadata.get("memory_type", "")
                if mem_type not in memories_by_type:
                    memories_by_type[mem_type] = []
                memories_by_type[mem_type].append(mem)

            # 记录需要删除的记忆，延迟批量删除
            to_delete: List[Tuple[Memory, str]] = []  # (memory, reason)
            deleted_ids = set()

            # 对每个类型的记忆进行相似度检测
            for mem_type, memories in memories_by_type.items():
                if len(memories) < 2:
                    continue

                logger.debug(f"🔍 检查类型 '{mem_type}' 的 {len(memories)} 条记忆")

                # 预提取所有主题节点的嵌入向量
                embeddings_map: Dict[str, "np.ndarray"] = {}
                valid_memories = []

                for mem in memories:
                    topic_node = next((n for n in mem.nodes if n.node_type == NodeType.TOPIC), None)
                    if topic_node and topic_node.embedding is not None:
                        embeddings_map[mem.id] = topic_node.embedding
                        valid_memories.append(mem)

                # 批量计算相似度矩阵（比逐个计算更高效）
                import numpy as np

                for i in range(len(valid_memories)):
                    # 更频繁的协作式多任务让出
                    if i % 5 == 0:
                        await asyncio.sleep(0.001)  # 1ms让出

                    mem_i = valid_memories[i]
                    if mem_i.id in deleted_ids:
                        continue

                    for j in range(i + 1, len(valid_memories)):
                        if valid_memories[j].id in deleted_ids:
                            continue

                        mem_j = valid_memories[j]

                        # 快速向量相似度计算
                        embedding_i = embeddings_map[mem_i.id]
                        embedding_j = embeddings_map[mem_j.id]

                        # 优化的余弦相似度计算
                        similarity = self._fast_cosine_similarity(embedding_i, embedding_j)

                        if similarity >= similarity_threshold:
                            # 决定保留哪个记忆
                            if mem_i.importance >= mem_j.importance:
                                keep_mem, remove_mem = mem_i, mem_j
                            else:
                                keep_mem, remove_mem = mem_j, mem_i

                            logger.debug(
                                f"🔄 标记相似记忆 (similarity={similarity:.3f}): "
                                f"保留 {keep_mem.id[:8]}, 删除 {remove_mem.id[:8]}"
                            )

                            # 增强保留记忆的重要性
                            keep_mem.importance = min(1.0, keep_mem.importance + 0.05)

                            # 累加访问次数
                            if hasattr(keep_mem, 'access_count') and hasattr(remove_mem, 'access_count'):
                                keep_mem.access_count += remove_mem.access_count

                            # 标记为待删除（不立即删除）
                            to_delete.append((remove_mem, f"与记忆 {keep_mem.id[:8]} 相似度 {similarity:.3f}"))
                            deleted_ids.add(remove_mem.id)
                            result["merged_count"] += 1

                # 每处理完一个类型就让出控制权
                await asyncio.sleep(0.005)  # 5ms让出

            # 批量删除标记的记忆
            if to_delete:
                logger.info(f"🗑️ 开始批量删除 {len(to_delete)} 条相似记忆")

                for memory, reason in to_delete:
                    try:
                        # 从向量存储删除节点
                        for node in memory.nodes:
                            if node.embedding is not None:
                                await self.vector_store.delete_node(node.id)

                        # 从图存储删除记忆
                        self.graph_store.remove_memory(memory.id)

                    except Exception as e:
                        logger.warning(f"删除记忆 {memory.id[:8]} 失败: {e}")

                # 批量保存（一次性写入，减少I/O）
                await self.persistence.save_graph_store(self.graph_store)
                logger.info(f"💾 批量保存完成")

            logger.info(f"✅ 记忆整理完成: {result}")

        except Exception as e:
            logger.error(f"❌ 记忆整理失败: {e}", exc_info=True)

    def _fast_cosine_similarity(self, vec1: "np.ndarray", vec2: "np.ndarray") -> float:
        """
        快速余弦相似度计算（优化版本）

        Args:
            vec1, vec2: 向量

        Returns:
            余弦相似度
        """
        try:
            import numpy as np

            # 避免重复的类型检查和转换
            # 向量应该是numpy数组，如果不是，转换一次
            if not isinstance(vec1, np.ndarray):
                vec1 = np.asarray(vec1, dtype=np.float32)
            if not isinstance(vec2, np.ndarray):
                vec2 = np.asarray(vec2, dtype=np.float32)

            # 使用更高效的范数计算
            norm1 = np.linalg.norm(vec1)
            norm2 = np.linalg.norm(vec2)

            if norm1 == 0 or norm2 == 0:
                return 0.0

            # 直接计算点积和除法
            return float(np.dot(vec1, vec2) / (norm1 * norm2))

        except Exception as e:
            logger.warning(f"计算余弦相似度失败: {e}")
            return 0.0

    async def auto_link_memories(
        self,
        time_window_hours: float = None,
        max_candidates: int = None,
        min_confidence: float = None,
    ) -> Dict[str, Any]:
        """
        自动关联记忆
        
        使用LLM分析记忆之间的关系，自动建立关联边。
        
        Args:
            time_window_hours: 分析时间窗口（小时）
            max_candidates: 每个记忆最多关联的候选数
            min_confidence: 最低置信度阈值
            
        Returns:
            关联结果统计
        """
        if not self._initialized:
            await self.initialize()

        # 使用配置值或参数覆盖
        time_window_hours = time_window_hours if time_window_hours is not None else 24
        max_candidates = max_candidates if max_candidates is not None else getattr(self.config, 'auto_link_max_candidates', 10)
        min_confidence = min_confidence if min_confidence is not None else getattr(self.config, 'auto_link_min_confidence', 0.7)

        try:
            logger.info(f"开始自动关联记忆 (时间窗口={time_window_hours}h)...")
            
            result = {
                "checked_count": 0,
                "linked_count": 0,
                "relation_stats": {},  # 关系类型统计 {类型: 数量}
                "relations": {},  # 详细关系 {source_id: [关系列表]}
            }
            
            # 1. 获取时间窗口内的记忆
            time_threshold = datetime.now() - timedelta(hours=time_window_hours)
            all_memories = self.graph_store.get_all_memories()
            
            recent_memories = [
                mem for mem in all_memories
                if mem.created_at >= time_threshold
                and not mem.metadata.get("forgotten", False)
            ]
            
            if len(recent_memories) < 2:
                logger.info("记忆数量不足，跳过自动关联")
                return result
            
            logger.info(f"找到 {len(recent_memories)} 条待关联记忆")
            
            # 2. 为每个记忆寻找关联候选
            for memory in recent_memories:
                result["checked_count"] += 1
                
                # 跳过已经有很多连接的记忆
                existing_edges = len([
                    e for e in memory.edges
                    if e.edge_type == EdgeType.RELATION
                ])
                if existing_edges >= 10:
                    continue
                
                # 3. 使用向量搜索找候选记忆
                candidates = await self._find_link_candidates(
                    memory,
                    exclude_ids={memory.id},
                    max_results=max_candidates
                )
                
                if not candidates:
                    continue
                
                # 4. 使用LLM分析关系
                relations = await self._analyze_memory_relations(
                    source_memory=memory,
                    candidate_memories=candidates,
                    min_confidence=min_confidence
                )
                
                # 5. 建立关联
                for relation in relations:
                    try:
                        # 创建关联边
                        edge = MemoryEdge(
                            id=f"edge_{uuid.uuid4().hex[:12]}",
                            source_id=memory.subject_id,
                            target_id=relation["target_memory"].subject_id,
                            relation=relation["relation_type"],
                            edge_type=EdgeType.RELATION,
                            importance=relation["confidence"],
                            metadata={
                                "auto_linked": True,
                                "confidence": relation["confidence"],
                                "reasoning": relation["reasoning"],
                                "created_at": datetime.now().isoformat(),
                            }
                        )
                        
                        # 添加到图
                        self.graph_store.graph.add_edge(
                            edge.source_id,
                            edge.target_id,
                            edge_id=edge.id,
                            relation=edge.relation,
                            edge_type=edge.edge_type.value,
                            importance=edge.importance,
                            metadata=edge.metadata,
                        )
                        
                        # 同时添加到记忆的边列表
                        memory.edges.append(edge)
                        
                        result["linked_count"] += 1
                        
                        # 更新统计
                        result["relation_stats"][relation["relation_type"]] = \
                            result["relation_stats"].get(relation["relation_type"], 0) + 1
                        
                        # 记录详细关系
                        if memory.id not in result["relations"]:
                            result["relations"][memory.id] = []
                        result["relations"][memory.id].append({
                            "target_id": relation["target_memory"].id,
                            "relation_type": relation["relation_type"],
                            "confidence": relation["confidence"],
                            "reasoning": relation["reasoning"],
                        })
                        
                        logger.info(
                            f"建立关联: {memory.id[:8]} --[{relation['relation_type']}]--> "
                            f"{relation['target_memory'].id[:8]} "
                            f"(置信度={relation['confidence']:.2f})"
                        )
                        
                    except Exception as e:
                        logger.warning(f"建立关联失败: {e}")
                        continue
            
            # 保存更新后的图数据
            if result["linked_count"] > 0:
                await self.persistence.save_graph_store(self.graph_store)
                logger.info(f"已保存 {result['linked_count']} 条自动关联边")
            
            logger.info(f"自动关联完成: {result}")
            return result
            
        except Exception as e:
            logger.error(f"自动关联失败: {e}", exc_info=True)
            return {"error": str(e), "checked_count": 0, "linked_count": 0}

    async def _find_link_candidates(
        self,
        memory: Memory,
        exclude_ids: Set[str],
        max_results: int = 5,
    ) -> List[Memory]:
        """
        为记忆寻找关联候选
        
        使用向量相似度 + 时间接近度找到潜在相关记忆
        """
        try:
            # 获取记忆的主题
            topic_node = next(
                (n for n in memory.nodes if n.node_type == NodeType.TOPIC),
                None
            )
            
            if not topic_node or not topic_node.content:
                return []
            
            # 使用主题内容搜索相似记忆
            candidates = await self.search_memories(
                query=topic_node.content,
                top_k=max_results * 2,
                include_forgotten=False,
            )
            
            # 过滤：排除自己和已关联的
            existing_targets = {
                e.target_id for e in memory.edges
                if e.edge_type == EdgeType.RELATION
            }
            
            filtered = [
                c for c in candidates
                if c.id not in exclude_ids
                and c.id not in existing_targets
            ]
            
            return filtered[:max_results]
            
        except Exception as e:
            logger.warning(f"查找候选失败: {e}")
            return []

    async def _analyze_memory_relations(
        self,
        source_memory: Memory,
        candidate_memories: List[Memory],
        min_confidence: float = 0.7,
    ) -> List[Dict[str, Any]]:
        """
        使用LLM分析记忆之间的关系
        
        Args:
            source_memory: 源记忆
            candidate_memories: 候选记忆列表
            min_confidence: 最低置信度
            
        Returns:
            关系列表，每项包含:
            - target_memory: 目标记忆
            - relation_type: 关系类型
            - confidence: 置信度
            - reasoning: 推理过程
        """
        try:
            from src.llm_models.utils_model import LLMRequest
            from src.config.config import model_config
            
            # 构建LLM请求
            llm = LLMRequest(
                model_set=model_config.model_task_config.utils_small,
                request_type="memory.relation_analysis"
            )
            
            # 格式化记忆信息
            source_desc = self._format_memory_for_llm(source_memory)
            candidates_desc = "\n\n".join([
                f"记忆{i+1}:\n{self._format_memory_for_llm(mem)}"
                for i, mem in enumerate(candidate_memories)
            ])
            
            # 构建提示词
            prompt = f"""你是一个记忆关系分析专家。请分析源记忆与候选记忆之间是否存在有意义的关系。

**关系类型说明：**
- 导致: A的发生导致了B的发生（因果关系）
- 引用: A提到或涉及B（引用关系）
- 相似: A和B描述相似的内容（相似关系）
- 相反: A和B表达相反的观点（对立关系）
- 关联: A和B存在某种关联但不属于以上类型（一般关联）

**源记忆：**
{source_desc}

**候选记忆：**
{candidates_desc}

**任务要求：**
1. 对每个候选记忆，判断是否与源记忆存在关系
2. 如果存在关系，指定关系类型和置信度(0.0-1.0)
3. 简要说明判断理由
4. 只返回置信度 >= {min_confidence} 的关系

**输出格式（JSON）：**
```json
[
  {{
    "candidate_id": 1,
    "has_relation": true,
    "relation_type": "导致",
    "confidence": 0.85,
    "reasoning": "记忆1是记忆源的结果"
  }},
  {{
    "candidate_id": 2,
    "has_relation": false,
    "reasoning": "两者无明显关联"
  }}
]
```

请分析并输出JSON结果："""

            # 调用LLM
            response, _ = await llm.generate_response_async(
                prompt,
                temperature=0.3,
                max_tokens=1000,
            )
            
            # 解析响应
            import json
            import re
            
            # 提取JSON
            json_match = re.search(r'```json\s*(.*?)\s*```', response, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                json_str = response.strip()
            
            try:
                analysis_results = json.loads(json_str)
            except json.JSONDecodeError:
                logger.warning(f"LLM返回格式错误，尝试修复: {response[:200]}")
                # 尝试简单修复
                json_str = re.sub(r'[\r\n\t]', '', json_str)
                analysis_results = json.loads(json_str)
            
            # 转换为结果格式
            relations = []
            for result in analysis_results:
                if not result.get("has_relation", False):
                    continue
                
                confidence = result.get("confidence", 0.0)
                if confidence < min_confidence:
                    continue
                
                candidate_id = result.get("candidate_id", 0) - 1
                if 0 <= candidate_id < len(candidate_memories):
                    relations.append({
                        "target_memory": candidate_memories[candidate_id],
                        "relation_type": result.get("relation_type", "关联"),
                        "confidence": confidence,
                        "reasoning": result.get("reasoning", ""),
                    })
            
            logger.debug(f"LLM分析完成: 发现 {len(relations)} 个关系")
            return relations
            
        except Exception as e:
            logger.error(f"LLM关系分析失败: {e}", exc_info=True)
            return []

    def _format_memory_for_llm(self, memory: Memory) -> str:
        """格式化记忆为LLM可读的文本"""
        try:
            # 获取关键节点
            subject_node = next(
                (n for n in memory.nodes if n.node_type == NodeType.SUBJECT),
                None
            )
            topic_node = next(
                (n for n in memory.nodes if n.node_type == NodeType.TOPIC),
                None
            )
            object_node = next(
                (n for n in memory.nodes if n.node_type == NodeType.OBJECT),
                None
            )
            
            parts = []
            parts.append(f"类型: {memory.memory_type.value}")
            
            if subject_node:
                parts.append(f"主体: {subject_node.content}")
            
            if topic_node:
                parts.append(f"主题: {topic_node.content}")
            
            if object_node:
                parts.append(f"对象: {object_node.content}")
            
            parts.append(f"重要性: {memory.importance:.2f}")
            parts.append(f"时间: {memory.created_at.strftime('%Y-%m-%d %H:%M')}")
            
            return " | ".join(parts)
            
        except Exception as e:
            logger.warning(f"格式化记忆失败: {e}")
            return f"记忆ID: {memory.id}"

    async def maintenance(self) -> Dict[str, Any]:
        """
        执行维护任务（优化版本）

        包括：
        - 记忆整理（异步后台执行）
        - 自动关联记忆（轻量级执行）
        - 自动遗忘低激活度记忆
        - 保存数据

        Returns:
            维护结果
        """
        if not self._initialized:
            await self.initialize()

        try:
            logger.info("🔧 开始执行记忆系统维护（优化版）...")

            result = {
                "consolidation_task": "none",
                "linked": 0,
                "forgotten": 0,
                "saved": False,
                "total_time": 0,
            }

            start_time = datetime.now()

            # 1. 记忆整理（异步后台执行，不阻塞主流程）
            if getattr(self.config, 'consolidation_enabled', False):
                logger.info("🚀 启动异步记忆整理任务...")
                consolidate_result = await self.consolidate_memories(
                    similarity_threshold=getattr(self.config, 'consolidation_deduplication_threshold', 0.93),
                    time_window_hours=getattr(self.config, 'consolidation_time_window_hours', 2.0),  # 统一时间窗口
                    max_batch_size=getattr(self.config, 'consolidation_max_batch_size', 30)
                )

                if consolidate_result.get("task_started"):
                    result["consolidation_task"] = f"background_task_{consolidate_result.get('task_id', 'unknown')}"
                    logger.info("✅ 记忆整理任务已启动到后台执行")
                else:
                    result["consolidation_task"] = "failed"
                    logger.warning("❌ 记忆整理任务启动失败")

            # 2. 自动关联记忆（使用统一的时间窗口）
            if getattr(self.config, 'consolidation_linking_enabled', True):
                logger.info("🔗 执行轻量级自动关联...")
                link_result = await self._lightweight_auto_link_memories()
                result["linked"] = link_result.get("linked_count", 0)

            # 3. 自动遗忘（快速执行）
            if getattr(self.config, 'forgetting_enabled', True):
                logger.info("🗑️ 执行自动遗忘...")
                forgotten_count = await self.auto_forget_memories(
                    threshold=getattr(self.config, 'forgetting_activation_threshold', 0.1)
                )
                result["forgotten"] = forgotten_count

            # 4. 保存数据（如果记忆整理不在后台执行）
            if result["consolidation_task"] == "none":
                await self.persistence.save_graph_store(self.graph_store)
                result["saved"] = True
                logger.info("💾 数据保存完成")

            self._last_maintenance = datetime.now()

            # 计算维护耗时
            total_time = (datetime.now() - start_time).total_seconds()
            result["total_time"] = total_time

            logger.info(f"✅ 维护完成 (耗时 {total_time:.2f}s): {result}")
            return result

        except Exception as e:
            logger.error(f"❌ 维护失败: {e}", exc_info=True)
            return {"error": str(e), "total_time": 0}

    async def _lightweight_auto_link_memories(
        self,
        time_window_hours: float = None,  # 从配置读取
        max_candidates: int = None,  # 从配置读取
        max_memories: int = None,  # 从配置读取
    ) -> Dict[str, Any]:
        """
        智能轻量级自动关联记忆（保留LLM判断，优化性能）

        优化策略：
        1. 从配置读取处理参数，尊重用户设置
        2. 使用向量相似度预筛选，仅对高相似度记忆调用LLM
        3. 批量LLM调用，减少网络开销
        4. 异步执行，避免阻塞
        """
        try:
            result = {
                "checked_count": 0,
                "linked_count": 0,
                "llm_calls": 0,
            }

            # 从配置读取参数，使用统一的时间窗口
            if time_window_hours is None:
                time_window_hours = getattr(self.config, 'consolidation_time_window_hours', 2.0)
            if max_candidates is None:
                max_candidates = getattr(self.config, 'consolidation_linking_max_candidates', 10)
            if max_memories is None:
                max_memories = getattr(self.config, 'consolidation_linking_max_memories', 20)

            # 获取用户配置时间窗口内的记忆
            time_threshold = datetime.now() - timedelta(hours=time_window_hours)
            all_memories = self.graph_store.get_all_memories()

            recent_memories = [
                mem for mem in all_memories
                if mem.created_at >= time_threshold
                and not mem.metadata.get("forgotten", False)
                and mem.importance >= getattr(self.config, 'consolidation_linking_min_importance', 0.5)  # 从配置读取重要性阈值
            ]

            if len(recent_memories) > max_memories:
                recent_memories = sorted(recent_memories, key=lambda m: m.created_at, reverse=True)[:max_memories]

            if len(recent_memories) < 2:
                logger.debug("记忆数量不足，跳过智能关联")
                return result

            logger.debug(f"🧠 智能关联: 检查 {len(recent_memories)} 条重要记忆")

            # 第一步：向量相似度预筛选，找到潜在关联对
            candidate_pairs = []
            import numpy as np

            for i, memory in enumerate(recent_memories):
                # 获取主题节点
                topic_node = next(
                    (n for n in memory.nodes if n.node_type == NodeType.TOPIC),
                    None
                )

                if not topic_node or topic_node.embedding is None:
                    continue

                # 与其他记忆计算相似度
                for j, other_memory in enumerate(recent_memories[i+1:], i+1):
                    other_topic = next(
                        (n for n in other_memory.nodes if n.node_type == NodeType.TOPIC),
                        None
                    )

                    if not other_topic or other_topic.embedding is None:
                        continue

                    # 快速相似度计算
                    similarity = self._fast_cosine_similarity(
                        topic_node.embedding,
                        other_topic.embedding
                    )

                    # 使用配置的预筛选阈值
                    pre_filter_threshold = getattr(self.config, 'consolidation_linking_pre_filter_threshold', 0.7)
                    if similarity >= pre_filter_threshold:
                        candidate_pairs.append((memory, other_memory, similarity))

                # 让出控制权
                if i % 3 == 0:
                    await asyncio.sleep(0.001)

            logger.debug(f"🔍 预筛选找到 {len(candidate_pairs)} 个候选关联对")

            if not candidate_pairs:
                return result

            # 第二步：批量LLM分析（使用配置的最大候选对数）
            max_pairs_for_llm = getattr(self.config, 'consolidation_linking_max_pairs_for_llm', 5)
            if len(candidate_pairs) <= max_pairs_for_llm:
                link_relations = await self._batch_analyze_memory_relations(candidate_pairs)
                result["llm_calls"] = 1

                # 第三步：建立LLM确认的关联
                for relation_info in link_relations:
                    try:
                        memory_a, memory_b = relation_info["memory_pair"]
                        relation_type = relation_info["relation_type"]
                        confidence = relation_info["confidence"]

                        # 创建关联边
                        edge = MemoryEdge(
                            id=f"smart_edge_{uuid.uuid4().hex[:12]}",
                            source_id=memory_a.subject_id,
                            target_id=memory_b.subject_id,
                            relation=relation_type,
                            edge_type=EdgeType.RELATION,
                            importance=confidence,
                            metadata={
                                "auto_linked": True,
                                "method": "llm_analyzed",
                                "vector_similarity": relation_info.get("vector_similarity", 0.0),
                                "confidence": confidence,
                                "reasoning": relation_info.get("reasoning", ""),
                                "created_at": datetime.now().isoformat(),
                            }
                        )

                        # 添加到图
                        self.graph_store.graph.add_edge(
                            edge.source_id,
                            edge.target_id,
                            edge_id=edge.id,
                            relation=edge.relation,
                            edge_type=edge.edge_type.value,
                            importance=edge.importance,
                            metadata=edge.metadata,
                        )

                        memory_a.edges.append(edge)
                        result["linked_count"] += 1

                        logger.debug(f"🧠 智能关联: {memory_a.id[:8]} --[{relation_type}]--> {memory_b.id[:8]} (置信度={confidence:.2f})")

                    except Exception as e:
                        logger.warning(f"建立智能关联失败: {e}")
                        continue

            # 保存关联结果
            if result["linked_count"] > 0:
                await self.persistence.save_graph_store(self.graph_store)

            logger.debug(f"✅ 智能关联完成: 建立了 {result['linked_count']} 个关联，LLM调用 {result['llm_calls']} 次")
            return result

        except Exception as e:
            logger.error(f"智能关联失败: {e}", exc_info=True)
            return {"error": str(e), "checked_count": 0, "linked_count": 0}

    async def _batch_analyze_memory_relations(
        self,
        candidate_pairs: List[Tuple[Memory, Memory, float]]
    ) -> List[Dict[str, Any]]:
        """
        批量分析记忆关系（优化LLM调用）

        Args:
            candidate_pairs: 候选记忆对列表，每项包含 (memory_a, memory_b, vector_similarity)

        Returns:
            关系分析结果列表
        """
        try:
            from src.llm_models.utils_model import LLMRequest
            from src.config.config import model_config

            llm = LLMRequest(
                model_set=model_config.model_task_config.utils_small,
                request_type="memory.batch_relation_analysis"
            )

            # 格式化所有候选记忆对
            candidates_text = ""
            for i, (mem_a, mem_b, similarity) in enumerate(candidate_pairs):
                desc_a = self._format_memory_for_llm(mem_a)
                desc_b = self._format_memory_for_llm(mem_b)
                candidates_text += f"""
候选对 {i+1}:
记忆A: {desc_a}
记忆B: {desc_b}
向量相似度: {similarity:.3f}
"""

            # 构建批量分析提示词（使用配置的置信度阈值）
            min_confidence = getattr(self.config, 'consolidation_linking_min_confidence', 0.7)

            prompt = f"""你是记忆关系分析专家。请批量分析以下候选记忆对之间的关系。

**关系类型说明：**
- 导致: A的发生导致了B的发生（因果关系）
- 引用: A提到或涉及B（引用关系）
- 相似: A和B描述相似的内容（相似关系）
- 相反: A和B表达相反的观点（对立关系）
- 关联: A和B存在某种关联但不属于以上类型（一般关联）

**候选记忆对：**
{candidates_text}

**任务要求：**
1. 对每个候选对，判断是否存在有意义的关系
2. 如果存在关系，指定关系类型和置信度(0.0-1.0)
3. 简要说明判断理由
4. 只返回置信度 >= {min_confidence} 的关系
5. 优先考虑因果、引用等强关系，谨慎建立相似关系

**输出格式（JSON）：**
```json
[
  {{
    "candidate_id": 1,
    "has_relation": true,
    "relation_type": "导致",
    "confidence": 0.85,
    "reasoning": "记忆A描述的原因导致记忆B的结果"
  }},
  {{
    "candidate_id": 2,
    "has_relation": false,
    "reasoning": "两者无明显关联"
  }}
]
```

请分析并输出JSON结果："""

            # 调用LLM（使用配置的参数）
            llm_temperature = getattr(self.config, 'consolidation_linking_llm_temperature', 0.2)
            llm_max_tokens = getattr(self.config, 'consolidation_linking_llm_max_tokens', 1500)

            response, _ = await llm.generate_response_async(
                prompt,
                temperature=llm_temperature,
                max_tokens=llm_max_tokens,
            )

            # 解析响应
            import json
            import re

            # 提取JSON
            json_match = re.search(r'```json\s*(.*?)\s*```', response, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                json_str = response.strip()

            try:
                analysis_results = json.loads(json_str)
            except json.JSONDecodeError:
                logger.warning(f"LLM返回格式错误，尝试修复: {response[:200]}")
                # 尝试简单修复
                json_str = re.sub(r'[\r\n\t]', '', json_str)
                analysis_results = json.loads(json_str)

            # 转换为结果格式
            relations = []
            for result in analysis_results:
                if not result.get("has_relation", False):
                    continue

                confidence = result.get("confidence", 0.0)
                if confidence < min_confidence:  # 使用配置的置信度阈值
                    continue

                candidate_id = result.get("candidate_id", 0) - 1
                if 0 <= candidate_id < len(candidate_pairs):
                    mem_a, mem_b, vector_similarity = candidate_pairs[candidate_id]
                    relations.append({
                        "memory_pair": (mem_a, mem_b),
                        "relation_type": result.get("relation_type", "关联"),
                        "confidence": confidence,
                        "reasoning": result.get("reasoning", ""),
                        "vector_similarity": vector_similarity,
                    })

            logger.debug(f"🧠 LLM批量分析完成: 发现 {len(relations)} 个关系")
            return relations

        except Exception as e:
            logger.error(f"LLM批量关系分析失败: {e}", exc_info=True)
            return []

    async def start_maintenance_scheduler(self) -> None:
        """
        启动记忆维护调度任务
        
        使用 unified_scheduler 定期执行维护任务：
        - 记忆整合（合并相似记忆）
        - 自动遗忘低激活度记忆
        - 保存数据
        
        默认间隔：1小时
        """
        try:
            from src.schedule.unified_scheduler import TriggerType, unified_scheduler
            
            # 如果已有调度任务，先移除
            if self._maintenance_schedule_id:
                await unified_scheduler.remove_schedule(self._maintenance_schedule_id)
                logger.info("移除旧的维护调度任务")
            
            # 创建新的调度任务
            interval_seconds = self._maintenance_interval_hours * 3600
            
            self._maintenance_schedule_id = await unified_scheduler.create_schedule(
                callback=self.maintenance,
                trigger_type=TriggerType.TIME,
                trigger_config={
                    "delay_seconds": interval_seconds,  # 首次延迟（启动后1小时）
                    "interval_seconds": interval_seconds,  # 循环间隔
                },
                is_recurring=True,
                task_name="memory_maintenance",
            )
            
            logger.info(
                f"✅ 记忆维护调度任务已启动 "
                f"(间隔={self._maintenance_interval_hours}小时, "
                f"schedule_id={self._maintenance_schedule_id[:8]}...)"
            )
            
        except ImportError:
            logger.warning("无法导入 unified_scheduler，维护调度功能不可用")
        except Exception as e:
            logger.error(f"启动维护调度任务失败: {e}", exc_info=True)

    async def stop_maintenance_scheduler(self) -> None:
        """
        停止记忆维护调度任务
        """
        if not self._maintenance_schedule_id:
            return
        
        try:
            from src.schedule.unified_scheduler import unified_scheduler
            
            success = await unified_scheduler.remove_schedule(self._maintenance_schedule_id)
            if success:
                logger.info(f"✅ 记忆维护调度任务已停止 (schedule_id={self._maintenance_schedule_id[:8]}...)")
            else:
                logger.warning(f"停止维护调度任务失败 (schedule_id={self._maintenance_schedule_id[:8]}...)")
            
            self._maintenance_schedule_id = None
            
        except ImportError:
            logger.warning("无法导入 unified_scheduler")
        except Exception as e:
            logger.error(f"停止维护调度任务失败: {e}", exc_info=True)
