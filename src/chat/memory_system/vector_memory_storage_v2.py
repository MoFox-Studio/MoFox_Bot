"""
基于Vector DB的统一记忆存储系统 V2
使用ChromaDB作为底层存储，替代JSON存储方式

主要特性:
- 统一的向量存储接口
- 高效的语义检索
- 元数据过滤支持
- 批量操作优化
- 自动清理过期记忆
"""

import asyncio
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import orjson

from src.chat.memory_system.memory_chunk import ConfidenceLevel, ImportanceLevel, MemoryChunk
from src.chat.memory_system.memory_forgetting_engine import MemoryForgettingEngine
from src.chat.memory_system.memory_metadata_index import MemoryMetadataIndex, MemoryMetadataIndexEntry
from src.chat.utils.utils import get_embedding
from src.common.logger import get_logger
from src.common.vector_db import vector_db_service

logger = get_logger(__name__)

# 全局枚举映射表缓存
_ENUM_MAPPINGS_CACHE = {}


def _build_enum_mapping(enum_class: type) -> dict[str, Any]:
    """构建枚举类的完整映射表

    Args:
        enum_class: 枚举类

    Returns:
        Dict[str, Any]: 包含各种映射格式的字典
    """
    cache_key = f"{enum_class.__module__}.{enum_class.__name__}"

    # 如果已经缓存过，直接返回
    if cache_key in _ENUM_MAPPINGS_CACHE:
        return _ENUM_MAPPINGS_CACHE[cache_key]

    mapping = {
        "name_to_enum": {},  # 枚举名称 -> 枚举实例 (HIGH -> ImportanceLevel.HIGH)
        "value_to_enum": {},  # 整数值 -> 枚举实例 (3 -> ImportanceLevel.HIGH)
        "value_str_to_enum": {},  # 字符串value -> 枚举实例 ("3" -> ImportanceLevel.HIGH)
        "enum_value_to_name": {},  # 枚举实例 -> 名称映射 (反向)
        "all_possible_strings": set(),  # 所有可能的字符串表示
    }

    for member in enum_class:
        # 名称映射 (支持大小写)
        mapping["name_to_enum"][member.name] = member
        mapping["name_to_enum"][member.name.lower()] = member
        mapping["name_to_enum"][member.name.upper()] = member

        # 值映射
        mapping["value_to_enum"][member.value] = member
        mapping["value_str_to_enum"][str(member.value)] = member

        # 反向映射
        mapping["enum_value_to_name"][member] = member.name

        # 收集所有可能的字符串表示
        mapping["all_possible_strings"].add(member.name)
        mapping["all_possible_strings"].add(member.name.lower())
        mapping["all_possible_strings"].add(member.name.upper())
        mapping["all_possible_strings"].add(str(member.value))

    # 缓存结果
    _ENUM_MAPPINGS_CACHE[cache_key] = mapping
    logger.debug(
        f"构建枚举映射表: {enum_class.__name__} -> {len(mapping['name_to_enum'])} 个名称映射, {len(mapping['value_to_enum'])} 个值映射"
    )

    return mapping


@dataclass
class VectorStorageConfig:
    """Vector存储配置"""

    # 集合配置
    memory_collection: str = "unified_memory_v2"
    metadata_collection: str = "memory_metadata_v2"

    # 检索配置
    similarity_threshold: float = 0.5  # 降低阈值以提高召回率（0.5-0.6 是合理范围）
    search_limit: int = 20
    batch_size: int = 100

    # 性能配置
    enable_caching: bool = True
    cache_size_limit: int = 1000
    auto_cleanup_interval: int = 3600  # 1小时

    # 遗忘配置
    enable_forgetting: bool = True
    retention_hours: int = 24 * 30  # 30天

    @classmethod
    def from_global_config(cls):
        """从全局配置创建实例"""
        from src.config.config import global_config

        memory_cfg = global_config.memory

        return cls(
            similarity_threshold=getattr(memory_cfg, "vector_db_similarity_threshold", 0.5),
            search_limit=getattr(memory_cfg, "vector_db_search_limit", 20),
            batch_size=getattr(memory_cfg, "vector_db_batch_size", 100),
            enable_caching=getattr(memory_cfg, "vector_db_enable_caching", True),
            cache_size_limit=getattr(memory_cfg, "vector_db_cache_size_limit", 1000),
            auto_cleanup_interval=getattr(memory_cfg, "vector_db_auto_cleanup_interval", 3600),
            enable_forgetting=getattr(memory_cfg, "enable_memory_forgetting", True),
            retention_hours=getattr(memory_cfg, "vector_db_retention_hours", 720),
        )


class VectorMemoryStorage:
    @property
    def keyword_index(self) -> dict:
        """
        动态构建关键词倒排索引（仅兼容旧接口，基于当前缓存）
        返回: {keyword: [memory_id, ...]}
        """
        index = {}
        for memory in self.memory_cache.values():
            for kw in getattr(memory, "keywords", []):
                if not kw:
                    continue
                kw_norm = kw.strip().lower()
                if kw_norm:
                    index.setdefault(kw_norm, []).append(getattr(memory.metadata, "memory_id", None))
        return index

    """基于Vector DB的记忆存储系统"""

    def __init__(self, config: VectorStorageConfig | None = None):
        # 默认从全局配置读取，如果没有传入config
        if config is None:
            try:
                self.config = VectorStorageConfig.from_global_config()
                logger.info("✅ Vector存储配置已从全局配置加载")
            except Exception as e:
                logger.warning(f"从全局配置加载失败，使用默认配置: {e}")
                self.config = VectorStorageConfig()
        else:
            self.config = config

        # 从配置中获取批处理大小和集合名称
        self.batch_size = self.config.batch_size
        self.collection_name = self.config.memory_collection
        self.vector_db_service = vector_db_service

        # 内存缓存
        self.memory_cache: dict[str, MemoryChunk] = {}
        self.cache_timestamps: dict[str, float] = {}
        self._cache = self.memory_cache  # 别名，兼容旧代码

        # 元数据索引管理器（JSON文件索引）
        self.metadata_index = MemoryMetadataIndex()

        # 遗忘引擎
        self.forgetting_engine: MemoryForgettingEngine | None = None
        if self.config.enable_forgetting:
            self.forgetting_engine = MemoryForgettingEngine()

        # 统计信息
        self.stats = {
            "total_memories": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "total_searches": 0,
            "total_stores": 0,
            "last_cleanup_time": 0.0,
            "forgetting_stats": {},
        }

        # 线程锁
        self._lock = threading.RLock()

        # 定时清理任务
        self._cleanup_task = None
        self._stop_cleanup = False

        # 初始化系统
        self._initialize_storage()
        self._start_cleanup_task()

    def _initialize_storage(self):
        """初始化Vector DB存储"""
        try:
            # 创建记忆集合
            vector_db_service.get_or_create_collection(
                name=self.config.memory_collection,
                metadata={"description": "统一记忆存储V2", "hnsw:space": "cosine", "version": "2.0"},
            )

            # 创建元数据集合（用于复杂查询）
            vector_db_service.get_or_create_collection(
                name=self.config.metadata_collection,
                metadata={"description": "记忆元数据索引", "hnsw:space": "cosine", "version": "2.0"},
            )

            # 获取当前记忆总数
            self.stats["total_memories"] = vector_db_service.count(self.config.memory_collection)

            logger.info(f"Vector记忆存储初始化完成，当前记忆数: {self.stats['total_memories']}")

        except Exception as e:
            logger.error(f"Vector存储系统初始化失败: {e}", exc_info=True)
            raise

    def _start_cleanup_task(self):
        """启动定时清理任务"""
        if self.config.auto_cleanup_interval > 0:

            def cleanup_worker():
                # 在新线程中创建事件循环
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

                try:
                    while not self._stop_cleanup:
                        try:
                            time.sleep(self.config.auto_cleanup_interval)
                            if not self._stop_cleanup:
                                # 在线程的事件循环中运行异步清理任务
                                loop.run_until_complete(self._perform_auto_cleanup())
                        except Exception as e:
                            logger.error(f"定时清理任务出错: {e}")
                finally:
                    loop.close()
                    logger.debug("清理任务事件循环已关闭")

            self._cleanup_task = threading.Thread(target=cleanup_worker, daemon=True)
            self._cleanup_task.start()
            logger.info(f"定时清理任务已启动，间隔: {self.config.auto_cleanup_interval}秒")

    async def _perform_auto_cleanup(self):
        """执行自动清理"""
        try:
            current_time = time.time()

            # 清理过期缓存
            if self.config.enable_caching:
                expired_keys = [
                    memory_id
                    for memory_id, timestamp in self.cache_timestamps.items()
                    if current_time - timestamp > 3600  # 1小时过期
                ]

                for key in expired_keys:
                    self.memory_cache.pop(key, None)
                    self.cache_timestamps.pop(key, None)

                if expired_keys:
                    logger.debug(f"清理了 {len(expired_keys)} 个过期缓存项")

            # 执行遗忘检查
            if self.forgetting_engine:
                await self.perform_forgetting_check()

            self.stats["last_cleanup_time"] = current_time

        except Exception as e:
            logger.error(f"自动清理失败: {e}")

    def _memory_to_vector_format(self, memory: MemoryChunk) -> dict[str, Any]:
        """将MemoryChunk转换为向量存储格式"""
        try:
            # 获取memory_id
            memory_id = getattr(memory.metadata, "memory_id", None) or getattr(memory, "memory_id", None)

            # 生成向量表示的文本
            display_text = (
                getattr(memory, "display", None) or getattr(memory, "text_content", None) or str(memory.content)
            )
            if not display_text.strip():
                logger.warning(f"记忆 {memory_id} 缺少有效的显示文本")
                display_text = f"{memory.memory_type.value}: {', '.join(memory.subjects)}"

            # 构建元数据 - 修复枚举值和列表序列化
            metadata = {
                "memory_id": memory_id,
                "user_id": memory.metadata.user_id or "unknown",
                "memory_type": memory.memory_type.value,
                "importance": memory.metadata.importance.name,  # 使用 .name 而不是枚举对象
                "confidence": memory.metadata.confidence.name,  # 使用 .name 而不是枚举对象
                "created_at": memory.metadata.created_at,
                "last_accessed": memory.metadata.last_accessed or memory.metadata.created_at,
                "access_count": memory.metadata.access_count,
                "subjects": orjson.dumps(memory.subjects).decode("utf-8"),  # 列表转JSON字符串
                "keywords": orjson.dumps(memory.keywords).decode("utf-8"),  # 列表转JSON字符串
                "tags": orjson.dumps(memory.tags).decode("utf-8"),  # 列表转JSON字符串
                "categories": orjson.dumps(memory.categories).decode("utf-8"),  # 列表转JSON字符串
                "relevance_score": memory.metadata.relevance_score,
            }

            # 添加可选字段
            if memory.metadata.source_context:
                metadata["source_context"] = str(memory.metadata.source_context)

            if memory.content.predicate:
                metadata["predicate"] = memory.content.predicate

            if memory.content.object:
                if isinstance(memory.content.object, dict | list):
                    metadata["object"] = orjson.dumps(memory.content.object).decode()
                else:
                    metadata["object"] = str(memory.content.object)

            return {
                "id": memory_id,
                "embedding": None,  # 将由vector_db_service生成
                "metadata": metadata,
                "document": display_text,
            }

        except Exception as e:
            memory_id = getattr(memory.metadata, "memory_id", None) or getattr(memory, "memory_id", "unknown")
            logger.error(f"转换记忆 {memory_id} 到向量格式失败: {e}", exc_info=True)
            raise

    def _vector_result_to_memory(self, document: str, metadata: dict[str, Any]) -> MemoryChunk | None:
        """将Vector DB结果转换为MemoryChunk"""
        try:
            # 从元数据中恢复完整记忆
            if "memory_data" in metadata:
                memory_dict = orjson.loads(metadata["memory_data"])
                return MemoryChunk.from_dict(memory_dict)

            # 兜底：从基础字段重建（使用新的结构化格式）
            logger.warning(f"未找到memory_data，使用兜底逻辑重建记忆 (id={metadata.get('memory_id', 'unknown')})")

            # 构建符合MemoryChunk.from_dict期望的结构
            memory_dict = {
                "metadata": {
                    "memory_id": metadata.get("memory_id", f"recovered_{int(time.time())}"),
                    "user_id": metadata.get("user_id", "unknown"),
                    "created_at": metadata.get("timestamp", time.time()),
                    "last_accessed": metadata.get("last_access_time", time.time()),
                    "last_modified": metadata.get("timestamp", time.time()),
                    "access_count": metadata.get("access_count", 0),
                    "relevance_score": 0.0,
                    "confidence": self._parse_enum_value(
                        metadata.get("confidence", 2), ConfidenceLevel, ConfidenceLevel.MEDIUM
                    ),
                    "importance": self._parse_enum_value(
                        metadata.get("importance", 2), ImportanceLevel, ImportanceLevel.NORMAL
                    ),
                    "source_context": None,
                },
                "content": {
                    "subject": "",
                    "predicate": "",
                    "object": "",
                    "display": document,  # 使用document作为显示文本
                },
                "memory_type": metadata.get("memory_type", "contextual"),
                "keywords": orjson.loads(metadata.get("keywords", "[]"))
                if isinstance(metadata.get("keywords"), str)
                else metadata.get("keywords", []),
                "tags": [],
                "categories": [],
                "embedding": None,
                "semantic_hash": None,
                "related_memories": [],
                "temporal_context": None,
            }

            return MemoryChunk.from_dict(memory_dict)

        except Exception as e:
            logger.error(f"转换Vector结果到MemoryChunk失败: {e}", exc_info=True)
            return None

    def _parse_enum_value(self, value: Any, enum_class: type, default: Any) -> Any:
        """解析枚举值，支持字符串、整数和枚举实例

        Args:
            value: 要解析的值（可能是字符串、整数或枚举实例）
            enum_class: 目标枚举类
            default: 默认值

        Returns:
            解析后的枚举实例
        """
        if value is None:
            return default

        # 如果已经是枚举实例，直接返回
        if isinstance(value, enum_class):
            return value

        # 如果是整数，尝试按value值匹配
        if isinstance(value, int):
            try:
                for member in enum_class:
                    if member.value == value:
                        return member
                # 如果没找到匹配的，返回默认值
                logger.warning(f"无法找到{enum_class.__name__}中value={value}的枚举项，使用默认值")
                return default
            except Exception as e:
                logger.warning(f"解析{enum_class.__name__}整数值{value}时出错: {e}，使用默认值")
                return default

        # 如果是字符串，尝试按名称或value值匹配
        if isinstance(value, str):
            str_value = value.strip().upper()

            # 先尝试按枚举名称匹配
            try:
                if hasattr(enum_class, str_value):
                    return getattr(enum_class, str_value)
            except AttributeError:
                pass

            # 再尝试按value值匹配（如果value是字符串形式的数字）
            try:
                int_value = int(str_value)
                return self._parse_enum_value(int_value, enum_class, default)
            except ValueError:
                pass

            # 最后尝试按小写名称匹配
            try:
                for member in enum_class:
                    if member.value.upper() == str_value:
                        return member
                logger.warning(f"无法找到{enum_class.__name__}中名称或value为'{value}'的枚举项，使用默认值")
                return default
            except Exception as e:
                logger.warning(f"解析{enum_class.__name__}字符串值'{value}'时出错: {e}，使用默认值")
                return default

        # 其他类型，返回默认值
        logger.warning(f"不支持的{enum_class.__name__}值类型: {type(value)}，使用默认值")
        return default

    def _get_from_cache(self, memory_id: str) -> MemoryChunk | None:
        """从缓存获取记忆"""
        if not self.config.enable_caching:
            return None

        with self._lock:
            if memory_id in self.memory_cache:
                self.cache_timestamps[memory_id] = time.time()
                self.stats["cache_hits"] += 1
                return self.memory_cache[memory_id]

            self.stats["cache_misses"] += 1
            return None

    def _add_to_cache(self, memory: MemoryChunk):
        """添加记忆到缓存"""
        if not self.config.enable_caching:
            return

        with self._lock:
            # 检查缓存大小限制
            if len(self.memory_cache) >= self.config.cache_size_limit:
                # 移除最老的缓存项
                oldest_id = min(self.cache_timestamps.keys(), key=lambda k: self.cache_timestamps[k])
                self.memory_cache.pop(oldest_id, None)
                self.cache_timestamps.pop(oldest_id, None)

            memory_id = getattr(memory.metadata, "memory_id", None) or getattr(memory, "memory_id", None)
            if memory_id:
                self.memory_cache[memory_id] = memory
                self.cache_timestamps[memory_id] = time.time()

    async def store_memories(self, memories: list[MemoryChunk]) -> int:
        """批量存储记忆"""
        if not memories:
            return 0

        start_time = datetime.now()
        success_count = 0

        try:
            # 转换为向量格式
            vector_data_list = []
            for memory in memories:
                try:
                    vector_data = self._memory_to_vector_format(memory)
                    vector_data_list.append(vector_data)
                except Exception as e:
                    memory_id = getattr(memory.metadata, "memory_id", None) or getattr(memory, "memory_id", "unknown")
                    logger.error(f"处理记忆 {memory_id} 失败: {e}")
                    continue

            if not vector_data_list:
                logger.warning("没有有效的记忆数据可存储")
                return 0

            # 批量存储到向量数据库
            for i in range(0, len(vector_data_list), self.batch_size):
                batch = vector_data_list[i : i + self.batch_size]

                try:
                    # 生成embeddings
                    embeddings = []
                    for item in batch:
                        try:
                            embedding = await get_embedding(item["document"])
                            embeddings.append(embedding)
                        except Exception as e:
                            logger.error(f"生成embedding失败: {e}")
                            # 使用零向量作为后备
                            embeddings.append([0.0] * 768)  # 默认维度

                    # vector_db_service.add 需要embeddings参数
                    self.vector_db_service.add(
                        collection_name=self.collection_name,
                        embeddings=embeddings,
                        ids=[item["id"] for item in batch],
                        documents=[item["document"] for item in batch],
                        metadatas=[item["metadata"] for item in batch],
                    )
                    success = True

                    if success:
                        # 更新缓存和元数据索引
                        metadata_entries = []
                        for item in batch:
                            memory_id = item["id"]
                            # 从原始 memories 列表中找到对应的 MemoryChunk
                            memory = next(
                                (
                                    m
                                    for m in memories
                                    if (getattr(m.metadata, "memory_id", None) or getattr(m, "memory_id", None))
                                    == memory_id
                                ),
                                None,
                            )
                            if memory:
                                # 更新缓存
                                self._cache[memory_id] = memory
                                success_count += 1

                                # 创建元数据索引条目
                                try:
                                    index_entry = MemoryMetadataIndexEntry(
                                        memory_id=memory_id,
                                        user_id=memory.metadata.user_id or "unknown",
                                        memory_type=memory.memory_type.value,
                                        subjects=memory.subjects,
                                        objects=[str(memory.content.object)] if memory.content.object else [],
                                        keywords=memory.keywords,
                                        tags=memory.tags,
                                        importance=memory.metadata.importance.value,
                                        confidence=memory.metadata.confidence.value,
                                        created_at=memory.metadata.created_at,
                                        access_count=memory.metadata.access_count,
                                        chat_id=memory.metadata.chat_id,
                                        content_preview=str(memory.content)[:100] if memory.content else None,
                                    )
                                    metadata_entries.append(index_entry)
                                except Exception as e:
                                    logger.warning(f"创建元数据索引条目失败 (memory_id={memory_id}): {e}")

                        # 批量更新元数据索引
                        if metadata_entries:
                            try:
                                self.metadata_index.batch_add_or_update(metadata_entries)
                                logger.debug(f"更新元数据索引: {len(metadata_entries)} 条")
                            except Exception as e:
                                logger.error(f"批量更新元数据索引失败: {e}")
                    else:
                        logger.warning(f"批次存储失败，跳过 {len(batch)} 条记忆")

                except Exception as e:
                    logger.error(f"批量存储失败: {e}", exc_info=True)
                    continue

            duration = (datetime.now() - start_time).total_seconds()
            logger.info(f"成功存储 {success_count}/{len(memories)} 条记忆，耗时 {duration:.2f}秒")

            # 保存元数据索引到磁盘
            if success_count > 0:
                try:
                    self.metadata_index.save()
                    logger.debug("元数据索引已保存到磁盘")
                except Exception as e:
                    logger.error(f"保存元数据索引失败: {e}")

            return success_count

        except Exception as e:
            logger.error(f"批量存储记忆失败: {e}", exc_info=True)
            return success_count

    async def store_memory(self, memory: MemoryChunk) -> bool:
        """存储单条记忆"""
        result = await self.store_memories([memory])
        return result > 0

    async def search_similar_memories(
        self,
        query_text: str,
        limit: int = 10,
        similarity_threshold: float | None = None,
        filters: dict[str, Any] | None = None,
        # 新增：元数据过滤参数（用于JSON索引粗筛）
        metadata_filters: dict[str, Any] | None = None,
    ) -> list[tuple[MemoryChunk, float]]:
        """
        搜索相似记忆（混合索引模式）

        Args:
            query_text: 查询文本
            limit: 返回数量限制
            similarity_threshold: 相似度阈值
            filters: ChromaDB where条件（保留用于兼容）
            metadata_filters: JSON元数据索引过滤条件，支持:
                - memory_types: List[str]
                - subjects: List[str]
                - keywords: List[str]
                - tags: List[str]
                - importance_min: int
                - importance_max: int
                - created_after: float
                - created_before: float
                - user_id: str
        """
        if not query_text.strip():
            return []

        try:
            # === 阶段一：JSON元数据粗筛（可选） ===
            candidate_ids: list[str] | None = None
            if metadata_filters:
                logger.debug(f"[JSON元数据粗筛] 开始，过滤条件: {metadata_filters}")
                candidate_ids = self.metadata_index.search(
                    memory_types=metadata_filters.get("memory_types"),
                    subjects=metadata_filters.get("subjects"),
                    keywords=metadata_filters.get("keywords"),
                    tags=metadata_filters.get("tags"),
                    importance_min=metadata_filters.get("importance_min"),
                    importance_max=metadata_filters.get("importance_max"),
                    created_after=metadata_filters.get("created_after"),
                    created_before=metadata_filters.get("created_before"),
                    user_id=metadata_filters.get("user_id"),
                    limit=self.config.search_limit * 2,  # 粗筛返回更多候选
                    flexible_mode=True,  # 使用灵活匹配模式
                )
                logger.debug(f"[JSON元数据粗筛] 完成，筛选出 {len(candidate_ids)} 个候选ID")

                # 如果粗筛后没有结果，回退到全部记忆搜索
                if not candidate_ids:
                    total_memories = len(self.metadata_index.index)
                    logger.warning(
                        f"JSON元数据粗筛后无候选，启用回退机制：在全部 {total_memories} 条记忆中进行向量搜索"
                    )
                    logger.info("💡 提示：这可能是因为查询条件过于严格，或相关记忆的元数据与查询条件不完全匹配")
                    candidate_ids = None  # 设为None表示不限制候选ID
                else:
                    logger.debug("[JSON元数据粗筛] 成功筛选出候选，进入向量精筛阶段")

            # === 阶段二：向量精筛 ===
            # 生成查询向量
            query_embedding = await get_embedding(query_text)
            if not query_embedding:
                return []

            threshold = similarity_threshold or self.config.similarity_threshold

            # 构建where条件
            where_conditions = filters or {}

            # 如果有候选ID列表，添加到where条件
            if candidate_ids:
                # ChromaDB的where条件需要使用$in操作符
                where_conditions["memory_id"] = {"$in": candidate_ids}
                logger.debug(f"[向量精筛] 限制在 {len(candidate_ids)} 个候选ID内搜索")
            else:
                logger.debug("[向量精筛] 在全部记忆中搜索（元数据筛选无结果回退）")

            # 查询Vector DB
            logger.debug(f"[向量精筛] 开始，limit={min(limit, self.config.search_limit)}")
            results = vector_db_service.query(
                collection_name=self.config.memory_collection,
                query_embeddings=[query_embedding],
                n_results=min(limit, self.config.search_limit),
                where=where_conditions if where_conditions else None,
            )

            # 处理结果
            similar_memories = []

            if results.get("documents") and results["documents"][0]:
                documents = results["documents"][0]
                distances = results.get("distances", [[]])[0]
                metadatas = results.get("metadatas", [[]])[0]
                ids = results.get("ids", [[]])[0]

                logger.debug(
                    f"向量检索返回原始结果：documents={len(documents)}, ids={len(ids)}, metadatas={len(metadatas)}"
                )
                for i, (doc, metadata, memory_id) in enumerate(zip(documents, metadatas, ids, strict=False)):
                    # 计算相似度
                    distance = distances[i] if i < len(distances) else 1.0
                    similarity = 1 - distance  # ChromaDB返回距离，转换为相似度

                    if similarity < threshold:
                        continue

                    # 首先尝试从缓存获取
                    memory = self._get_from_cache(memory_id)

                    if not memory:
                        # 从Vector结果重建
                        memory = self._vector_result_to_memory(doc, metadata)
                        if memory:
                            self._add_to_cache(memory)

                    if memory:
                        similar_memories.append((memory, similarity))
                        # 记录单条结果的关键日志（id，相似度，简短文本）
                        try:
                            short_text = (
                                (str(memory.content)[:120])
                                if hasattr(memory, "content")
                                else (doc[:120] if isinstance(doc, str) else "")
                            )
                        except Exception:
                            short_text = ""
                        logger.debug(f"检索结果 - id={memory_id}, similarity={similarity:.4f}, summary={short_text}")

            # 按相似度排序
            similar_memories.sort(key=lambda x: x[1], reverse=True)

            self.stats["total_searches"] += 1
            logger.debug(
                f"搜索相似记忆: query='{query_text[:60]}...', limit={limit}, threshold={threshold}, filters={where_conditions}, 返回数={len(similar_memories)}"
            )
            logger.debug(f"搜索相似记忆 详细结果数={len(similar_memories)}")

            return similar_memories

        except Exception as e:
            logger.error(f"搜索相似记忆失败: {e}")
            return []

    async def get_memory_by_id(self, memory_id: str) -> MemoryChunk | None:
        """根据ID获取记忆"""
        # 首先尝试从缓存获取
        memory = self._get_from_cache(memory_id)
        if memory:
            return memory

        try:
            # 从Vector DB获取
            results = vector_db_service.get(collection_name=self.config.memory_collection, ids=[memory_id])

            if results.get("documents") and results["documents"]:
                document = results["documents"][0]
                metadata = results["metadatas"][0] if results.get("metadatas") else {}

                memory = self._vector_result_to_memory(document, metadata)
                if memory:
                    self._add_to_cache(memory)

                return memory

        except Exception as e:
            logger.error(f"获取记忆 {memory_id} 失败: {e}")

        return None

    async def get_memories_by_filters(self, filters: dict[str, Any], limit: int = 100) -> list[MemoryChunk]:
        """根据过滤条件获取记忆"""
        try:
            results = vector_db_service.get(collection_name=self.config.memory_collection, where=filters, limit=limit)

            memories = []
            if results.get("documents"):
                documents = results["documents"]
                metadatas = results.get("metadatas", [{}] * len(documents))
                ids = results.get("ids", [])

                logger.debug(f"按过滤条件获取返回: docs={len(documents)}, ids={len(ids)}")
                for i, (doc, metadata) in enumerate(zip(documents, metadatas, strict=False)):
                    memory_id = ids[i] if i < len(ids) else None

                    # 首先尝试从缓存获取
                    if memory_id:
                        memory = self._get_from_cache(memory_id)
                        if memory:
                            memories.append(memory)
                            logger.debug(f"过滤获取命中缓存: id={memory_id}")
                            continue

                    # 从Vector结果重建
                    memory = self._vector_result_to_memory(doc, metadata)
                    if memory:
                        memories.append(memory)
                        if memory_id:
                            self._add_to_cache(memory)
                        logger.debug(f"过滤获取结果: id={memory_id}, meta_keys={list(metadata.keys())}")

            return memories

        except Exception as e:
            logger.error(f"根据过滤条件获取记忆失败: {e}")
            return []

    async def update_memory(self, memory: MemoryChunk) -> bool:
        """更新记忆"""
        try:
            memory_id = getattr(memory.metadata, "memory_id", None) or getattr(memory, "memory_id", None)
            if not memory_id:
                logger.error("无法更新记忆：缺少memory_id")
                return False

            # 先删除旧记忆
            await self.delete_memory(memory_id)

            # 重新存储更新后的记忆
            return await self.store_memory(memory)

        except Exception as e:
            memory_id = getattr(memory.metadata, "memory_id", None) or getattr(memory, "memory_id", "unknown")
            logger.error(f"更新记忆 {memory_id} 失败: {e}")
            return False

    async def delete_memory(self, memory_id: str) -> bool:
        """删除记忆"""
        try:
            # 从Vector DB删除
            vector_db_service.delete(collection_name=self.config.memory_collection, ids=[memory_id])

            # 从缓存删除
            with self._lock:
                self.memory_cache.pop(memory_id, None)
                self.cache_timestamps.pop(memory_id, None)

            self.stats["total_memories"] = max(0, self.stats["total_memories"] - 1)
            logger.debug(f"删除记忆: {memory_id}")

            return True

        except Exception as e:
            logger.error(f"删除记忆 {memory_id} 失败: {e}")
            return False

    async def delete_memories_by_filters(self, filters: dict[str, Any]) -> int:
        """根据过滤条件批量删除记忆"""
        try:
            # 先获取要删除的记忆ID
            results = vector_db_service.get(
                collection_name=self.config.memory_collection, where=filters, include=["metadatas"]
            )

            if not results.get("ids"):
                return 0

            memory_ids = results["ids"]

            # 批量删除
            vector_db_service.delete(collection_name=self.config.memory_collection, where=filters)

            # 从缓存删除
            with self._lock:
                for memory_id in memory_ids:
                    self.memory_cache.pop(memory_id, None)
                    self.cache_timestamps.pop(memory_id, None)

            deleted_count = len(memory_ids)
            self.stats["total_memories"] = max(0, self.stats["total_memories"] - deleted_count)
            logger.info(f"批量删除记忆: {deleted_count} 条")

            return deleted_count

        except Exception as e:
            logger.error(f"批量删除记忆失败: {e}")
            return 0

    async def perform_forgetting_check(self) -> dict[str, Any]:
        """执行遗忘检查"""
        if not self.forgetting_engine:
            return {"error": "遗忘引擎未启用"}

        try:
            # 获取所有记忆进行遗忘检查
            # 注意：对于大型数据集，这里应该分批处理
            current_time = time.time()
            cutoff_time = current_time - (self.config.retention_hours * 3600)

            # 先删除明显过期的记忆
            expired_filters = {"timestamp": {"$lt": cutoff_time}}
            expired_count = await self.delete_memories_by_filters(expired_filters)

            # 对剩余记忆执行智能遗忘检查
            # 这里为了性能考虑，只检查一部分记忆
            sample_memories = await self.get_memories_by_filters({}, limit=500)

            if sample_memories:
                result = await self.forgetting_engine.perform_forgetting_check(sample_memories)

                # 遗忘标记的记忆
                forgetting_ids = result.get("normal_forgetting", []) + result.get("force_forgetting", [])
                forgotten_count = 0

                for memory_id in forgetting_ids:
                    if await self.delete_memory(memory_id):
                        forgotten_count += 1

                result["forgotten_count"] = forgotten_count
                result["expired_count"] = expired_count

                # 更新统计
                self.stats["forgetting_stats"] = self.forgetting_engine.get_forgetting_stats()

                logger.info(f"遗忘检查完成: 过期删除 {expired_count}, 智能遗忘 {forgotten_count}")
                return result

            return {"expired_count": expired_count, "forgotten_count": 0}

        except Exception as e:
            logger.error(f"执行遗忘检查失败: {e}")
            return {"error": str(e)}

    def get_storage_stats(self) -> dict[str, Any]:
        """获取存储统计信息"""
        try:
            current_total = vector_db_service.count(self.config.memory_collection)
            self.stats["total_memories"] = current_total
        except Exception:
            pass

        return {
            **self.stats,
            "cache_size": len(self.memory_cache),
            "collection_name": self.config.memory_collection,
            "storage_type": "vector_db_v2",
            "uptime": time.time() - self.stats.get("start_time", time.time()),
        }

    def stop(self):
        """停止存储系统"""
        self._stop_cleanup = True

        if self._cleanup_task and self._cleanup_task.is_alive():
            logger.info("正在停止定时清理任务...")

        # 清空缓存
        with self._lock:
            self.memory_cache.clear()
            self.cache_timestamps.clear()

        logger.info("Vector记忆存储系统已停止")

    def cleanup(self):
        """清理资源，兼容旧接口"""
        logger.info("正在清理VectorMemoryStorage资源...")
        self.stop()


# 全局实例（可选）
_global_vector_storage = None


def get_vector_memory_storage(config: VectorStorageConfig | None = None) -> VectorMemoryStorage:
    """获取全局Vector记忆存储实例"""
    global _global_vector_storage

    if _global_vector_storage is None:
        _global_vector_storage = VectorMemoryStorage(config)

    return _global_vector_storage


# 兼容性接口
class VectorMemoryStorageAdapter:
    """适配器类，提供与原UnifiedMemoryStorage兼容的接口"""

    def __init__(self, config: VectorStorageConfig | None = None):
        self.storage = VectorMemoryStorage(config)

    async def store_memories(self, memories: list[MemoryChunk]) -> int:
        return await self.storage.store_memories(memories)

    async def search_similar_memories(
        self, query_text: str, limit: int = 10, scope_id: str | None = None, filters: dict[str, Any] | None = None
    ) -> list[tuple[str, float]]:
        results = await self.storage.search_similar_memories(query_text, limit, filters=filters)
        # 转换为原格式：(memory_id, similarity)
        return [
            (getattr(memory.metadata, "memory_id", None) or getattr(memory, "memory_id", "unknown"), similarity)
            for memory, similarity in results
        ]

    def get_stats(self) -> dict[str, Any]:
        return self.storage.get_storage_stats()


if __name__ == "__main__":
    # 简单测试
    async def test_vector_storage():
        storage = VectorMemoryStorage()

        # 创建测试记忆
        from src.chat.memory_system.memory_chunk import MemoryType

        test_memory = MemoryChunk(
            memory_id="test_001",
            user_id="test_user",
            text_content="今天天气很好，适合出门散步",
            memory_type=MemoryType.FACT,
            keywords=["天气", "散步"],
            importance=0.7,
        )

        # 存储记忆
        success = await storage.store_memory(test_memory)
        print(f"存储结果: {success}")

        # 搜索记忆
        results = await storage.search_similar_memories("天气怎么样", limit=5)
        print(f"搜索结果: {len(results)} 条")

        for memory, similarity in results:
            print(f"  - {memory.text_content[:50]}... (相似度: {similarity:.3f})")

        # 获取统计信息
        stats = storage.get_storage_stats()
        print(f"存储统计: {stats}")

        storage.stop()

    asyncio.run(test_vector_storage())
