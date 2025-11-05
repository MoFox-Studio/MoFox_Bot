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
        
        logger.info(f"记忆管理器已创建 (data_dir={data_dir})")

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
            
        except Exception as e:
            logger.error(f"记忆管理器初始化失败: {e}", exc_info=True)
            raise

    async def shutdown(self) -> None:
        """
        关闭记忆管理器，保存所有数据
        """
        if not self._initialized:
            return

        try:
            logger.info("正在关闭记忆管理器...")
            
            # 保存图数据
            if self.graph_store and self.persistence:
                await self.persistence.save_graph_store(self.graph_store)
                logger.info("图数据已保存")
            
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

    async def search_memories(
        self,
        query: str,
        top_k: int = 10,
        memory_types: Optional[List[str]] = None,
        time_range: Optional[Tuple[datetime, datetime]] = None,
        min_importance: float = 0.0,
        include_forgotten: bool = False,
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
            
        Returns:
            记忆列表
        """
        if not self._initialized:
            await self.initialize()

        try:
            params = {
                "query": query,
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
                decay_factor = 0.9 ** (hours_passed / 24)  # 每天衰减 10%
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
            
            # 激活传播：激活相关记忆（强度减半）
            if strength > 0.1:  # 只有足够强的激活才传播
                related_memories = self._get_related_memories(memory_id)
                propagation_strength = strength * 0.5
                
                for related_id in related_memories[:5]:  # 最多传播到 5 个相关记忆
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
                if memory.importance >= 0.8:
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

    async def maintenance(self) -> Dict[str, Any]:
        """
        执行维护任务
        
        包括：
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
                "forgotten": 0,
                "deleted": 0,
                "saved": False,
            }
            
            # 1. 自动遗忘
            forgotten_count = await self.auto_forget_memories(threshold=0.1)
            result["forgotten"] = forgotten_count
            
            # 2. 清理非常旧的已遗忘记忆（可选）
            # TODO: 实现清理逻辑
            
            # 3. 保存数据
            await self.persistence.save_graph_store(self.graph_store)
            result["saved"] = True
            
            self._last_maintenance = datetime.now()
            logger.info(f"维护完成: {result}")
            return result
            
        except Exception as e:
            logger.error(f"维护失败: {e}", exc_info=True)
            return {"error": str(e)}
