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

from src.memory_graph.config import MemoryGraphConfig
from src.memory_graph.core.builder import MemoryBuilder
from src.memory_graph.core.extractor import MemoryExtractor
from src.memory_graph.models import Memory, MemoryNode, MemoryType, NodeType
from src.memory_graph.storage.graph_store import GraphStore
from src.memory_graph.storage.persistence import PersistenceManager
from src.memory_graph.storage.vector_store import VectorStore
from src.memory_graph.tools.memory_tools import MemoryTools
from src.memory_graph.utils.embeddings import EmbeddingGenerator

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
        config: Optional[MemoryGraphConfig] = None,
        data_dir: Optional[Path] = None,
    ):
        """
        初始化记忆管理器
        
        Args:
            config: 记忆图配置
            data_dir: 数据目录
        """
        self.config = config or MemoryGraphConfig()
        self.data_dir = data_dir or Path("data/memory_graph")
        
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
        self._maintenance_interval_hours = self.config.consolidation_interval_hours  # 从配置读取
        self._maintenance_schedule_id: Optional[str] = None  # 调度任务ID
        
        logger.info(f"记忆管理器已创建 (data_dir={data_dir}, enable={self.config.enable})")

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
            
            self.vector_store = VectorStore(
                collection_name=self.config.storage.vector_collection_name,
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
            
            self.tools = MemoryTools(
                vector_store=self.vector_store,
                graph_store=self.graph_store,
                persistence_manager=self.persistence,
                embedding_generator=self.embedding_generator,
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

    async def optimize_search_query(
        self,
        query: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        使用小模型优化搜索查询
        
        Args:
            query: 原始查询
            context: 上下文信息（聊天历史、发言人等）
            
        Returns:
            优化后的查询字符串
        """
        if not context:
            return query
        
        try:
            from src.llm_models.utils_model import LLMRequest
            from src.config.config import model_config
            
            # 使用小模型优化查询
            llm = LLMRequest(
                model_set=model_config.model_task_config.utils_small,
                request_type="memory.query_optimizer"
            )
            
            # 构建优化提示
            chat_history = context.get("chat_history", "")
            sender = context.get("sender", "")
            
            prompt = f"""你是一个记忆检索查询优化助手。请将用户的查询转换为更适合语义搜索的表述。

要求：
1. 提取查询的核心意图和关键信息
2. 使用更具体、描述性的语言
3. 如果查询涉及人物，明确指出是谁
4. 保持简洁，只输出优化后的查询文本

当前查询: {query}

{f"发言人: {sender}" if sender else ""}
{f"最近对话: {chat_history[-200:]}" if chat_history else ""}

优化后的查询:"""

            optimized_query, _ = await llm.generate_response_async(
                prompt,
                temperature=0.3,
                max_tokens=100
            )
            
            # 清理输出
            optimized_query = optimized_query.strip()
            if optimized_query and len(optimized_query) > 5:
                logger.debug(f"[查询优化] '{query}' -> '{optimized_query}'")
                return optimized_query
            
            return query
            
        except Exception as e:
            logger.warning(f"查询优化失败，使用原始查询: {e}")
            return query

    async def search_memories(
        self,
        query: str,
        top_k: int = 10,
        memory_types: Optional[List[str]] = None,
        time_range: Optional[Tuple[datetime, datetime]] = None,
        min_importance: float = 0.0,
        include_forgotten: bool = False,
        optimize_query: bool = True,
        context: Optional[Dict[str, Any]] = None,
    ) -> List[Memory]:
        """
        搜索记忆
        
        Args:
            query: 搜索查询
            top_k: 返回结果数
            memory_types: 记忆类型过滤
            time_range: 时间范围过滤 (start, end)
            min_importance: 最小重要性
            include_forgotten: 是否包含已遗忘的记忆
            optimize_query: 是否使用小模型优化查询
            context: 查询上下文（用于优化）
            
        Returns:
            记忆列表
        """
        if not self._initialized:
            await self.initialize()

        try:
            # 查询优化
            search_query = query
            if optimize_query and context:
                search_query = await self.optimize_search_query(query, context)
            
            params = {
                "query": search_query,
                "top_k": top_k,
            }
            
            if memory_types:
                params["memory_types"] = memory_types
            
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
            
            logger.info(f"搜索完成: 找到 {len(filtered_memories)} 条记忆")
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
                decay_factor = self.config.activation_decay_rate ** (hours_passed / 24)
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
                related_memories = self._get_related_memories(
                    memory_id,
                    max_depth=self.config.activation_propagation_depth
                )
                propagation_strength = strength * self.config.activation_propagation_strength
                
                for related_id in related_memories[:self.config.max_related_memories]:
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
        获取相关记忆 ID 列表
        
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
                if memory.importance >= self.config.forgetting_min_importance:
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
        time_window_hours: int = 24,
    ) -> Dict[str, Any]:
        """
        整理记忆：合并相似记忆
        
        Args:
            similarity_threshold: 相似度阈值
            time_window_hours: 时间窗口（小时）
            
        Returns:
            整理结果
        """
        if not self._initialized:
            await self.initialize()

        try:
            logger.info(f"开始记忆整理 (similarity_threshold={similarity_threshold}, time_window={time_window_hours}h)...")
            
            result = {
                "merged_count": 0,
                "checked_count": 0,
            }
            
            # 获取最近创建的记忆
            cutoff_time = datetime.now() - timedelta(hours=time_window_hours)
            all_memories = self.graph_store.get_all_memories()
            
            recent_memories = [
                mem for mem in all_memories
                if mem.created_at >= cutoff_time and not mem.metadata.get("forgotten", False)
            ]
            
            if not recent_memories:
                logger.info("没有需要整理的记忆")
                return result
            
            logger.info(f"找到 {len(recent_memories)} 条待整理记忆")
            result["checked_count"] = len(recent_memories)
            
            # 按记忆类型分组
            memories_by_type: Dict[str, List[Memory]] = {}
            for mem in recent_memories:
                mem_type = mem.metadata.get("memory_type", "")
                if mem_type not in memories_by_type:
                    memories_by_type[mem_type] = []
                memories_by_type[mem_type].append(mem)
            
            # 对每个类型的记忆进行相似度检测
            for mem_type, memories in memories_by_type.items():
                if len(memories) < 2:
                    continue
                
                logger.debug(f"检查类型 '{mem_type}' 的 {len(memories)} 条记忆")
                
                # 使用向量相似度检测
                for i in range(len(memories)):
                    for j in range(i + 1, len(memories)):
                        mem_i = memories[i]
                        mem_j = memories[j]
                        
                        # 获取主题节点的向量
                        topic_i = next((n for n in mem_i.nodes if n.node_type == NodeType.TOPIC), None)
                        topic_j = next((n for n in mem_j.nodes if n.node_type == NodeType.TOPIC), None)
                        
                        if not topic_i or not topic_j:
                            continue
                        
                        if topic_i.embedding is None or topic_j.embedding is None:
                            continue
                        
                        # 计算余弦相似度
                        import numpy as np
                        similarity = np.dot(topic_i.embedding, topic_j.embedding) / (
                            np.linalg.norm(topic_i.embedding) * np.linalg.norm(topic_j.embedding)
                        )
                        
                        if similarity >= similarity_threshold:
                            # 合并记忆：保留重要性高的，删除另一个
                            if mem_i.importance >= mem_j.importance:
                                keep_mem, remove_mem = mem_i, mem_j
                            else:
                                keep_mem, remove_mem = mem_j, mem_i
                            
                            logger.info(
                                f"合并相似记忆 (similarity={similarity:.3f}): "
                                f"保留 {keep_mem.id}, 删除 {remove_mem.id}"
                            )
                            
                            # 增加保留记忆的重要性
                            keep_mem.importance = min(1.0, keep_mem.importance + 0.1)
                            keep_mem.activation = min(1.0, keep_mem.activation + 0.1)
                            
                            # 删除相似记忆
                            await self.delete_memory(remove_mem.id)
                            result["merged_count"] += 1
            
            logger.info(f"记忆整理完成: {result}")
            return result
            
        except Exception as e:
            logger.error(f"记忆整理失败: {e}", exc_info=True)
            return {"error": str(e), "merged_count": 0, "checked_count": 0}

    async def maintenance(self) -> Dict[str, Any]:
        """
        执行维护任务
        
        包括：
        - 记忆整理（合并相似记忆）
        - 清理过期记忆
        - 自动遗忘低激活度记忆
        - 保存数据
        
        Returns:
            维护结果
        """
        if not self._initialized:
            await self.initialize()

        try:
            logger.info("开始执行记忆系统维护...")
            
            result = {
                "consolidated": 0,
                "forgotten": 0,
                "deleted": 0,
                "saved": False,
            }
            
            # 1. 记忆整理（合并相似记忆）
            if self.config.consolidation_enabled:
                consolidate_result = await self.consolidate_memories(
                    similarity_threshold=self.config.consolidation_similarity_threshold,
                    time_window_hours=self.config.consolidation_time_window_hours
                )
                result["consolidated"] = consolidate_result.get("merged_count", 0)
            
            # 2. 自动遗忘
            if self.config.forgetting_enabled:
                forgotten_count = await self.auto_forget_memories(
                    threshold=self.config.forgetting_activation_threshold
                )
                result["forgotten"] = forgotten_count
            
            # 3. 清理非常旧的已遗忘记忆（可选）
            # TODO: 实现清理逻辑
            
            # 4. 保存数据
            await self.persistence.save_graph_store(self.graph_store)
            result["saved"] = True
            
            self._last_maintenance = datetime.now()
            logger.info(f"维护完成: {result}")
            return result
            
        except Exception as e:
            logger.error(f"维护失败: {e}", exc_info=True)
            return {"error": str(e)}

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
