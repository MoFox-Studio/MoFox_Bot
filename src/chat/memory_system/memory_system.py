"""
精准记忆系统核心模块
1. 基于文档设计的高效记忆构建、存储与召回优化系统，覆盖构建、向量化与多阶段检索全流程。
2. 内置 LLM 查询规划器与嵌入维度自动解析机制，直接从模型配置推断向量存储参数。
"""

import asyncio
import hashlib
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Any

import orjson

from src.chat.memory_system.memory_builder import MemoryBuilder, MemoryExtractionError
from src.chat.memory_system.memory_chunk import MemoryChunk
from src.chat.memory_system.memory_fusion import MemoryFusionEngine
from src.chat.memory_system.memory_query_planner import MemoryQueryPlanner
from src.chat.memory_system.message_collection_storage import MessageCollectionStorage


# 记忆采样模式枚举
class MemorySamplingMode(Enum):
    """记忆采样模式"""

    HIPPOCAMPUS = "hippocampus"  # 海马体模式：定时任务采样
    IMMEDIATE = "immediate"  # 即时模式：回复后立即采样
    ALL = "all"  # 所有模式：同时使用海马体和即时采样


from src.common.logger import get_logger
from src.config.config import global_config, model_config
from src.llm_models.utils_model import LLMRequest

if TYPE_CHECKING:
    from src.chat.memory_system.memory_forgetting_engine import MemoryForgettingEngine
    from src.chat.memory_system.vector_memory_storage_v2 import VectorMemoryStorage
    from src.common.data_models.database_data_model import DatabaseMessages

logger = get_logger("memory_system")

# 全局记忆作用域（共享记忆库）
GLOBAL_MEMORY_SCOPE = "global"


class MemorySystemStatus(Enum):
    """记忆系统状态"""

    INITIALIZING = "initializing"
    READY = "ready"
    BUILDING = "building"
    RETRIEVING = "retrieving"
    ERROR = "error"


@dataclass
class MemorySystemConfig:
    """记忆系统配置"""

    # 记忆构建配置
    min_memory_length: int = 10
    max_memory_length: int = 500
    memory_value_threshold: float = 0.7
    min_build_interval_seconds: float = 300.0

    # 向量存储配置（嵌入维度自动来自模型配置）
    vector_dimension: int = 1024
    similarity_threshold: float = 0.8

    # 召回配置
    coarse_recall_limit: int = 50
    fine_recall_limit: int = 10
    semantic_rerank_limit: int = 20
    final_recall_limit: int = 5
    semantic_similarity_threshold: float = 0.6
    vector_weight: float = 0.4
    semantic_weight: float = 0.3
    context_weight: float = 0.2
    recency_weight: float = 0.1

    # 融合配置
    fusion_similarity_threshold: float = 0.85
    deduplication_window: timedelta = timedelta(hours=24)

    @classmethod
    def from_global_config(cls):
        """从全局配置创建配置实例"""

        embedding_dimension = None
        try:
            embedding_task = getattr(model_config.model_task_config, "embedding", None)
            if embedding_task is not None:
                embedding_dimension = getattr(embedding_task, "embedding_dimension", None)
        except Exception:
            embedding_dimension = None

        if not embedding_dimension:
            try:
                embedding_dimension = getattr(global_config.lpmm_knowledge, "embedding_dimension", None)
            except Exception:
                embedding_dimension = None

        if not embedding_dimension:
            embedding_dimension = 1024

        return cls(
            # 记忆构建配置
            min_memory_length=global_config.memory.min_memory_length,
            max_memory_length=global_config.memory.max_memory_length,
            memory_value_threshold=global_config.memory.memory_value_threshold,
            min_build_interval_seconds=getattr(global_config.memory, "memory_build_interval", 300.0),
            # 向量存储配置
            vector_dimension=int(embedding_dimension),
            similarity_threshold=global_config.memory.vector_similarity_threshold,
            # 召回配置
            coarse_recall_limit=global_config.memory.metadata_filter_limit,
            fine_recall_limit=global_config.memory.vector_search_limit,
            semantic_rerank_limit=global_config.memory.semantic_rerank_limit,
            final_recall_limit=global_config.memory.final_result_limit,
            semantic_similarity_threshold=getattr(global_config.memory, "semantic_similarity_threshold", 0.6),
            vector_weight=global_config.memory.vector_weight,
            semantic_weight=global_config.memory.semantic_weight,
            context_weight=global_config.memory.context_weight,
            recency_weight=global_config.memory.recency_weight,
            # 融合配置
            fusion_similarity_threshold=global_config.memory.fusion_similarity_threshold,
            deduplication_window=timedelta(hours=global_config.memory.deduplication_window_hours),
        )


class MemorySystem:
    """精准记忆系统核心类"""

    def __init__(self, llm_model: LLMRequest | None = None, config: MemorySystemConfig | None = None):
        self.config = config or MemorySystemConfig.from_global_config()
        self.llm_model = llm_model
        self.status = MemorySystemStatus.INITIALIZING

        # 核心组件（简化版）
        self.memory_builder: MemoryBuilder | None = None
        self.fusion_engine: MemoryFusionEngine | None = None
        self.unified_storage: VectorMemoryStorage | None = None  # 统一存储系统
        self.message_collection_storage: MessageCollectionStorage | None = None
        self.query_planner: MemoryQueryPlanner | None = None
        self.forgetting_engine: MemoryForgettingEngine | None = None

        # LLM模型
        self.value_assessment_model: LLMRequest | None = None
        self.memory_extraction_model: LLMRequest | None = None

        # 统计信息
        self.total_memories = 0
        self.last_build_time = None
        self.last_retrieval_time = None
        self.last_collection_cleanup_time: float = time.time()

        # 构建节流记录
        self._last_memory_build_times: dict[str, float] = {}

        # 记忆指纹缓存，用于快速检测重复记忆
        self._memory_fingerprints: dict[str, str] = {}

        # 海马体采样器
        self.hippocampus_sampler = None

        logger.info("MemorySystem 初始化开始")

    async def initialize(self):
        """异步初始化记忆系统"""
        try:
            # 初始化LLM模型
            fallback_task = getattr(self.llm_model, "model_for_task", None) if self.llm_model else None

            value_task_config = getattr(model_config.model_task_config, "utils_small", None)
            extraction_task_config = getattr(model_config.model_task_config, "utils", None)

            if value_task_config is None:
                logger.warning("未找到 utils_small 模型配置，回退到 utils 或外部提供的模型配置。")
                value_task_config = extraction_task_config or fallback_task

            if extraction_task_config is None:
                logger.warning("未找到 utils 模型配置，回退到 utils_small 或外部提供的模型配置。")
                extraction_task_config = value_task_config or fallback_task

            if value_task_config is None or extraction_task_config is None:
                raise RuntimeError(
                    "无法初始化记忆系统所需的模型配置，请检查 model_task_config 中的 utils / utils_small 设置。"
                )

            self.value_assessment_model = LLMRequest(
                model_set=value_task_config, request_type="memory.value_assessment"
            )

            self.memory_extraction_model = LLMRequest(
                model_set=extraction_task_config, request_type="memory.extraction"
            )

            # 初始化核心组件（简化版）
            self.memory_builder = MemoryBuilder(self.memory_extraction_model)
            self.fusion_engine = MemoryFusionEngine(self.config.fusion_similarity_threshold)

            # 初始化消息集合存储
            self.message_collection_storage = MessageCollectionStorage()

            # 初始化Vector DB存储系统（替代旧的unified_memory_storage）
            from src.chat.memory_system.vector_memory_storage_v2 import VectorMemoryStorage, VectorStorageConfig

            storage_config = VectorStorageConfig(
                memory_collection="unified_memory_v2",
                metadata_collection="memory_metadata_v2",
                similarity_threshold=self.config.similarity_threshold,
                search_limit=getattr(global_config.memory, "unified_storage_search_limit", 20),
                batch_size=getattr(global_config.memory, "unified_storage_batch_size", 100),
                enable_caching=getattr(global_config.memory, "unified_storage_enable_caching", True),
                cache_size_limit=getattr(global_config.memory, "unified_storage_cache_limit", 1000),
                auto_cleanup_interval=getattr(global_config.memory, "unified_storage_auto_cleanup_interval", 3600),
                enable_forgetting=getattr(global_config.memory, "enable_memory_forgetting", True),
                retention_hours=getattr(global_config.memory, "memory_retention_hours", 720),  # 30天
            )

            try:
                self.unified_storage = VectorMemoryStorage(storage_config)
                logger.info("✅ Vector DB存储系统初始化成功")
            except Exception as storage_error:
                logger.error(f"❌ Vector DB存储系统初始化失败: {storage_error}", exc_info=True)
                raise

            # 初始化遗忘引擎
            from src.chat.memory_system.memory_forgetting_engine import ForgettingConfig, MemoryForgettingEngine

            # 从全局配置创建遗忘引擎配置
            forgetting_config = ForgettingConfig(
                # 检查频率配置
                check_interval_hours=getattr(global_config.memory, "forgetting_check_interval_hours", 24),
                batch_size=100,  # 固定值，暂不配置
                # 遗忘阈值配置
                base_forgetting_days=getattr(global_config.memory, "base_forgetting_days", 30.0),
                min_forgetting_days=getattr(global_config.memory, "min_forgetting_days", 7.0),
                max_forgetting_days=getattr(global_config.memory, "max_forgetting_days", 365.0),
                # 重要程度权重
                critical_importance_bonus=getattr(global_config.memory, "critical_importance_bonus", 45.0),
                high_importance_bonus=getattr(global_config.memory, "high_importance_bonus", 30.0),
                normal_importance_bonus=getattr(global_config.memory, "normal_importance_bonus", 15.0),
                low_importance_bonus=getattr(global_config.memory, "low_importance_bonus", 0.0),
                # 置信度权重
                verified_confidence_bonus=getattr(global_config.memory, "verified_confidence_bonus", 30.0),
                high_confidence_bonus=getattr(global_config.memory, "high_confidence_bonus", 20.0),
                medium_confidence_bonus=getattr(global_config.memory, "medium_confidence_bonus", 10.0),
                low_confidence_bonus=getattr(global_config.memory, "low_confidence_bonus", 0.0),
                # 激活频率权重
                activation_frequency_weight=getattr(global_config.memory, "activation_frequency_weight", 0.5),
                max_frequency_bonus=getattr(global_config.memory, "max_frequency_bonus", 10.0),
                # 休眠配置
                dormant_threshold_days=getattr(global_config.memory, "dormant_threshold_days", 90),
            )

            self.forgetting_engine = MemoryForgettingEngine(forgetting_config)

            planner_task_config = model_config.model_task_config.utils_small
            planner_model: LLMRequest | None = None
            try:
                planner_model = LLMRequest(model_set=planner_task_config, request_type="memory.query_planner")
            except Exception as planner_exc:
                logger.warning("查询规划模型初始化失败，将使用默认规划策略: %s", planner_exc, exc_info=True)

            self.query_planner = MemoryQueryPlanner(planner_model, default_limit=self.config.final_recall_limit)

            # 初始化海马体采样器
            if global_config.memory.enable_hippocampus_sampling:
                try:
                    from .hippocampus_sampler import initialize_hippocampus_sampler

                    self.hippocampus_sampler = await initialize_hippocampus_sampler(self)
                    logger.info("✅ 海马体采样器初始化成功")
                except Exception as e:
                    logger.warning(f"海马体采样器初始化失败: {e}")
                    self.hippocampus_sampler = None

            # 统一存储已经自动加载数据，无需额外加载

            self.status = MemorySystemStatus.READY

        except Exception as e:
            self.status = MemorySystemStatus.ERROR
            logger.error(f"❌ 记忆系统初始化失败: {e}", exc_info=True)
            raise

    async def retrieve_memories_for_building(
        self, query_text: str, user_id: str | None = None, context: dict[str, Any] | None = None, limit: int = 5
    ) -> list[MemoryChunk]:
        """在构建记忆时检索相关记忆，使用统一存储系统

        Args:
            query_text: 查询文本
            context: 上下文信息
            limit: 返回结果数量限制

        Returns:
            相关记忆列表
        """
        if self.status not in [MemorySystemStatus.READY, MemorySystemStatus.BUILDING]:
            logger.warning(f"记忆系统状态不允许检索: {self.status.value}")
            return []

        if not self.unified_storage:
            logger.warning("统一存储系统未初始化")
            return []

        try:
            # 使用统一存储检索相似记忆
            filters = {"user_id": user_id} if user_id else None
            search_results = await self.unified_storage.search_similar_memories(
                query_text=query_text, limit=limit, filters=filters
            )

            # 转换为记忆对象
            memories = []
            for memory, similarity_score in search_results:
                if memory:
                    memory.update_access()  # 更新访问信息
                    memories.append(memory)

            return memories

        except Exception as e:
            logger.error(f"构建过程中检索记忆失败: {e}", exc_info=True)
            return []

    async def build_memory_from_conversation(
        self,
        conversation_text: str,
        context: dict[str, Any],
        timestamp: float | None = None,
        bypass_interval: bool = False,
    ) -> list[MemoryChunk]:
        """从对话中构建记忆

        Args:
            conversation_text: 对话文本
            context: 上下文信息
            timestamp: 时间戳，默认为当前时间
            bypass_interval: 是否绕过构建间隔检查（海马体采样器专用）

        Returns:
            构建的记忆块列表
        """
        original_status = self.status
        self.status = MemorySystemStatus.BUILDING
        start_time = time.time()

        build_scope_key: str | None = None
        build_marker_time: float | None = None

        try:
            normalized_context = self._normalize_context(context, GLOBAL_MEMORY_SCOPE, timestamp)

            build_scope_key = self._get_build_scope_key(normalized_context, GLOBAL_MEMORY_SCOPE)
            min_interval = max(0.0, getattr(self.config, "min_build_interval_seconds", 0.0))
            current_time = time.time()

            # 构建间隔检查（海马体采样器可以绕过）
            if build_scope_key and min_interval > 0 and not bypass_interval:
                last_time = self._last_memory_build_times.get(build_scope_key)
                if last_time and (current_time - last_time) < min_interval:
                    remaining = min_interval - (current_time - last_time)
                    logger.info(
                        f"距离上次记忆构建间隔不足，跳过此次构建 | key={build_scope_key} | 剩余{remaining:.2f}秒",
                    )
                    self.status = MemorySystemStatus.READY
                    return []

                build_marker_time = current_time
                self._last_memory_build_times[build_scope_key] = current_time
            elif bypass_interval:
                # 海马体采样模式：不更新构建时间记录，避免影响即时模式
                logger.debug("海马体采样模式：绕过构建间隔检查")

            conversation_text = await self._resolve_conversation_context(conversation_text, normalized_context)

            logger.debug("开始构建记忆，文本长度: %d", len(conversation_text))

            # 1. 信息价值评估（海马体采样器可以绕过）
            if not bypass_interval and not context.get("bypass_value_threshold", False):
                value_score = await self._assess_information_value(conversation_text, normalized_context)

                if value_score < self.config.memory_value_threshold:
                    logger.info(f"信息价值评分 {value_score:.2f} 低于阈值，跳过记忆构建")
                    self.status = original_status
                    return []
            else:
                # 海马体采样器：使用默认价值分数或简单评估
                value_score = 0.6  # 默认中等价值
                if context.get("is_hippocampus_sample", False):
                    # 对海马体样本进行简单价值评估
                    if len(conversation_text) > 100:  # 长文本可能有更多信息
                        value_score = 0.7
                    elif len(conversation_text) > 50:
                        value_score = 0.6
                    else:
                        value_score = 0.5

                logger.debug(f"海马体采样模式：使用价值评分 {value_score:.2f}")

            # 2. 构建记忆块（所有记忆统一使用 global 作用域，实现完全共享）
            memory_chunks = await self.memory_builder.build_memories(
                conversation_text,
                normalized_context,
                GLOBAL_MEMORY_SCOPE,  # 强制使用 global，不区分用户
                timestamp or time.time(),
            )

            if not memory_chunks:
                logger.debug("未提取到有效记忆块")
                self.status = original_status
                return []

            # 3. 记忆融合与去重（包含与历史记忆的融合）
            existing_candidates = await self._collect_fusion_candidates(memory_chunks)
            fused_chunks = await self.fusion_engine.fuse_memories(memory_chunks, existing_candidates)

            # 4. 存储记忆到统一存储
            stored_count = await self._store_memories_unified(fused_chunks)

            # 4.1 控制台预览
            self._log_memory_preview(fused_chunks)

            # 5. 更新统计
            self.total_memories += stored_count
            self.last_build_time = time.time()
            if build_scope_key:
                self._last_memory_build_times[build_scope_key] = self.last_build_time

            build_time = time.time() - start_time
            logger.info(
                f"✅ 生成 {len(fused_chunks)} 条记忆，成功入库 {stored_count} 条，耗时 {build_time:.2f}秒",
            )

            self.status = original_status
            return fused_chunks

        except MemoryExtractionError as e:
            if build_scope_key and build_marker_time is not None:
                recorded_time = self._last_memory_build_times.get(build_scope_key)
                if recorded_time == build_marker_time:
                    self._last_memory_build_times.pop(build_scope_key, None)
            self.status = original_status
            logger.warning("记忆构建因LLM响应问题中断: %s", e)
            return []

        except Exception as e:
            if build_scope_key and build_marker_time is not None:
                recorded_time = self._last_memory_build_times.get(build_scope_key)
                if recorded_time == build_marker_time:
                    self._last_memory_build_times.pop(build_scope_key, None)
            self.status = MemorySystemStatus.ERROR
            logger.error(f"❌ 记忆构建失败: {e}", exc_info=True)
            raise

    def _log_memory_preview(self, memories: list[MemoryChunk]) -> None:
        """在控制台输出记忆预览，便于人工检查"""
        if not memories:
            logger.info("📝 本次未生成新的记忆")
            return

        logger.info(f"📝 本次生成的记忆预览 ({len(memories)} 条):")
        for idx, memory in enumerate(memories, start=1):
            text = memory.text_content or ""
            if len(text) > 120:
                text = text[:117] + "..."

            logger.info(
                f"  {idx}) 类型={memory.memory_type.value} 重要性={memory.metadata.importance.name} "
                f"置信度={memory.metadata.confidence.name} | 内容={text}"
            )

    async def _collect_fusion_candidates(self, new_memories: list[MemoryChunk]) -> list[MemoryChunk]:
        """收集与新记忆相似的现有记忆，便于融合去重"""
        if not new_memories:
            return []

        candidate_ids: set[str] = set()
        new_memory_ids = {memory.memory_id for memory in new_memories if memory and getattr(memory, "memory_id", None)}

        # 基于指纹的直接匹配
        for memory in new_memories:
            try:
                fingerprint = self._build_memory_fingerprint(memory)
                fingerprint_key = self._fingerprint_key(memory.user_id, fingerprint)
                existing_id = self._memory_fingerprints.get(fingerprint_key)
                if existing_id and existing_id not in new_memory_ids:
                    candidate_ids.add(existing_id)
            except Exception as exc:  # noqa: PERF203
                logger.debug("构建记忆指纹失败，跳过候选收集: %s", exc)

        # 基于主体索引的候选（使用统一存储）
        if self.unified_storage and self.unified_storage.keyword_index:
            for memory in new_memories:
                for subject in memory.subjects:
                    normalized = subject.strip().lower() if isinstance(subject, str) else ""
                    if not normalized:
                        continue
                    subject_candidates = self.unified_storage.keyword_index.get(normalized)
                    if subject_candidates:
                        candidate_ids.update(subject_candidates)

        # 基于向量搜索的候选（使用统一存储）
        total_vectors = 0
        if self.unified_storage:
            storage_stats = self.unified_storage.get_storage_stats()
            total_vectors = storage_stats.get("total_vectors", 0) or 0

        if self.unified_storage and total_vectors > 0:
            search_tasks = []
            for memory in new_memories:
                display_text = (memory.display or "").strip()
                if not display_text:
                    continue
                search_tasks.append(
                    self.unified_storage.search_similar_memories(
                        query_text=display_text, limit=8, filters={"user_id": GLOBAL_MEMORY_SCOPE}
                    )
                )

            if search_tasks:
                search_results = await asyncio.gather(*search_tasks, return_exceptions=True)
                similarity_threshold = getattr(
                    self.fusion_engine,
                    "similarity_threshold",
                    self.config.similarity_threshold,
                )
                min_threshold = max(0.0, min(1.0, similarity_threshold * 0.8))

                for result in search_results:
                    if isinstance(result, Exception):
                        logger.warning("融合候选向量搜索失败: %s", result)
                        continue
                    for memory_id, similarity in result:
                        if memory_id in new_memory_ids:
                            continue
                        if similarity is None or similarity < min_threshold:
                            continue
                        candidate_ids.add(memory_id)

        existing_candidates: list[MemoryChunk] = []
        cache = self.unified_storage.memory_cache if self.unified_storage else {}
        for candidate_id in candidate_ids:
            if candidate_id in new_memory_ids:
                continue
            candidate_memory = cache.get(candidate_id)
            if candidate_memory:
                existing_candidates.append(candidate_memory)

        if existing_candidates:
            logger.debug(
                "融合候选收集完成，新记忆=%d，候选=%d",
                len(new_memories),
                len(existing_candidates),
            )

        return existing_candidates

    async def process_conversation_memory(self, context: dict[str, Any]) -> dict[str, Any]:
        """对外暴露的对话记忆处理接口，支持海马体、精准记忆、自适应三种采样模式"""
        start_time = time.time()

        try:
            context = dict(context or {})

            # 获取配置的采样模式
            sampling_mode = getattr(global_config.memory, "memory_sampling_mode", "precision")
            current_mode = MemorySamplingMode(sampling_mode)

            context["__sampling_mode"] = current_mode.value
            logger.debug(f"使用记忆采样模式: {current_mode.value}")

            # 根据采样模式处理记忆
            if current_mode == MemorySamplingMode.HIPPOCAMPUS:
                # 海马体模式：仅后台定时采样，不立即处理
                return {
                    "success": True,
                    "created_memories": [],
                    "memory_count": 0,
                    "processing_time": time.time() - start_time,
                    "status": self.status.value,
                    "processing_mode": "hippocampus",
                    "message": "海马体模式：记忆将由后台定时任务采样处理",
                }

            elif current_mode == MemorySamplingMode.IMMEDIATE:
                # 即时模式：立即处理记忆构建
                return await self._process_immediate_memory(context, start_time)

            elif current_mode == MemorySamplingMode.ALL:
                # 所有模式：同时进行即时处理和海马体采样
                immediate_result = await self._process_immediate_memory(context, start_time)

                # 海马体采样器会在后台继续处理，这里只是记录
                if self.hippocampus_sampler:
                    immediate_result["processing_mode"] = "all_modes"
                    immediate_result["hippocampus_status"] = "background_sampling_enabled"
                    immediate_result["message"] = "所有模式：即时处理已完成，海马体采样将在后台继续"
                else:
                    immediate_result["processing_mode"] = "immediate_fallback"
                    immediate_result["hippocampus_status"] = "not_available"
                    immediate_result["message"] = "海马体采样器不可用，回退到即时模式"

                return immediate_result

            else:
                # 默认回退到即时模式
                logger.warning(f"未知的采样模式 {sampling_mode}，回退到即时模式")
                return await self._process_immediate_memory(context, start_time)

        except Exception as e:
            processing_time = time.time() - start_time
            logger.error(f"对话记忆处理失败: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "processing_time": processing_time,
                "status": self.status.value,
                "processing_mode": "error",
            }

    async def _process_immediate_memory(self, context: dict[str, Any], start_time: float) -> dict[str, Any]:
        """即时记忆处理的辅助方法"""
        try:
            conversation_candidate = (
                context.get("conversation_text")
                or context.get("message_content")
                or context.get("latest_message")
                or context.get("raw_text")
                or ""
            )

            conversation_text = (
                conversation_candidate if isinstance(conversation_candidate, str) else str(conversation_candidate)
            )

            timestamp = context.get("timestamp")
            if timestamp is None:
                timestamp = time.time()

            normalized_context = self._normalize_context(context, GLOBAL_MEMORY_SCOPE, timestamp)
            normalized_context.setdefault("conversation_text", conversation_text)

            # 检查信息价值阈值
            value_score = await self._assess_information_value(conversation_text, normalized_context)
            threshold = getattr(global_config.memory, "precision_memory_reply_threshold", 0.5)

            if value_score < threshold:
                logger.debug(f"信息价值评分 {value_score:.2f} 低于阈值 {threshold}，跳过记忆构建")
                return {
                    "success": True,
                    "created_memories": [],
                    "memory_count": 0,
                    "processing_time": time.time() - start_time,
                    "status": self.status.value,
                    "processing_mode": "immediate",
                    "skip_reason": f"value_score_{value_score:.2f}_below_threshold_{threshold}",
                    "value_score": value_score,
                }

            memories = await self.build_memory_from_conversation(
                conversation_text=conversation_text, context=normalized_context, timestamp=timestamp
            )

            processing_time = time.time() - start_time
            memory_count = len(memories)

            return {
                "success": True,
                "created_memories": memories,
                "memory_count": memory_count,
                "processing_time": processing_time,
                "status": self.status.value,
                "processing_mode": "immediate",
                "value_score": value_score,
            }

        except Exception as e:
            processing_time = time.time() - start_time
            logger.error(f"即时记忆处理失败: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "processing_time": processing_time,
                "status": self.status.value,
                "processing_mode": "immediate_error",
            }

    async def retrieve_relevant_memories(
        self,
        query_text: str | None = None,
        user_id: str | None = None,
        context: dict[str, Any] | None = None,
        limit: int = 5,
        **kwargs,
    ) -> list[MemoryChunk]:
        """检索相关记忆（三阶段召回：元数据粗筛 → 向量精筛 → 综合重排），并融合瞬时记忆"""
        raw_query = query_text or kwargs.get("query")
        if not raw_query:
            raise ValueError("query_text 或 query 参数不能为空")

        if not self.unified_storage:
            logger.warning("统一存储系统未初始化")
            return []

        context = context or {}

        # 所有记忆完全共享，统一使用 global 作用域，不区分用户
        resolved_user_id = GLOBAL_MEMORY_SCOPE

        self.status = MemorySystemStatus.RETRIEVING
        start_time = time.time()

        try:
            normalized_context = self._normalize_context(context, GLOBAL_MEMORY_SCOPE, None)
            effective_limit = self.config.final_recall_limit

            # === 阶段一：元数据粗筛（软性过滤） ===
            coarse_filters = {
                "user_id": GLOBAL_MEMORY_SCOPE,  # 必选：确保作用域正确
            }

            # 应用查询规划（优化查询语句并构建元数据过滤）
            optimized_query = raw_query
            metadata_filters = {}

            if self.query_planner:
                try:
                    # 构建包含未读消息的增强上下文
                    enhanced_context = await self._build_enhanced_query_context(raw_query, normalized_context)
                    query_plan = await self.query_planner.plan_query(raw_query, enhanced_context)

                    # 使用LLM优化后的查询语句（更精确的语义表达）
                    if getattr(query_plan, "semantic_query", None):
                        optimized_query = query_plan.semantic_query

                    # 构建JSON元数据过滤条件（用于阶段一粗筛）
                    # 将查询规划的结果转换为元数据过滤条件
                    if getattr(query_plan, "memory_types", None):
                        metadata_filters["memory_types"] = [mt.value for mt in query_plan.memory_types]

                    if getattr(query_plan, "subject_includes", None):
                        metadata_filters["subjects"] = query_plan.subject_includes

                    if getattr(query_plan, "required_keywords", None):
                        metadata_filters["keywords"] = query_plan.required_keywords

                    # 时间范围过滤
                    recency = getattr(query_plan, "recency_preference", "any")
                    current_time = time.time()
                    if recency == "recent":
                        # 最近7天
                        metadata_filters["created_after"] = current_time - (7 * 24 * 3600)
                    elif recency == "historical":
                        # 30天以前
                        metadata_filters["created_before"] = current_time - (30 * 24 * 3600)

                    # 添加用户ID到元数据过滤
                    metadata_filters["user_id"] = GLOBAL_MEMORY_SCOPE

                    logger.debug(f"[阶段一] 查询优化: '{raw_query}' → '{optimized_query}'")
                    logger.debug(f"[阶段一] 元数据过滤条件: {metadata_filters}")

                except Exception as plan_exc:
                    logger.warning("查询规划失败，使用原始查询: %s", plan_exc, exc_info=True)
                    # 即使查询规划失败，也保留基本的user_id过滤
                    metadata_filters = {"user_id": GLOBAL_MEMORY_SCOPE}

            # === 阶段二：向量精筛 ===
            coarse_limit = self.config.coarse_recall_limit  # 粗筛阶段返回更多候选

            logger.debug(f"[阶段二] 开始向量搜索: query='{optimized_query[:60]}...', limit={coarse_limit}")

            search_results = await self.unified_storage.search_similar_memories(
                query_text=optimized_query,
                limit=coarse_limit,
                filters=coarse_filters,  # ChromaDB where条件（保留兼容）
                metadata_filters=metadata_filters,  # JSON元数据索引过滤
            )

            logger.info(f"[阶段二] 向量搜索完成: 返回 {len(search_results)} 条候选")

            # === 阶段三：综合重排 ===
            scored_memories = []
            current_time = time.time()

            for memory, vector_similarity in search_results:
                # 1. 向量相似度得分（已归一化到 0-1）
                vector_score = vector_similarity

                # 2. 时效性得分（指数衰减，30天半衰期）
                age_seconds = current_time - memory.metadata.created_at
                age_days = age_seconds / (24 * 3600)
                # 使用 math.exp 而非 np.exp（避免依赖numpy）
                import math

                recency_score = math.exp(-age_days / 30)

                # 3. 重要性得分（枚举值转换为归一化得分 0-1）
                # ImportanceLevel: LOW=1, NORMAL=2, HIGH=3, CRITICAL=4
                importance_enum = memory.metadata.importance
                if hasattr(importance_enum, "value"):
                    # 枚举类型，转换为0-1范围：(value - 1) / 3
                    importance_score = (importance_enum.value - 1) / 3.0
                else:
                    # 如果已经是数值，直接使用
                    importance_score = float(importance_enum) if importance_enum else 0.5

                # 4. 访问频率得分（归一化，访问10次以上得满分）
                access_count = memory.metadata.access_count
                frequency_score = min(access_count / 10.0, 1.0)

                # 综合得分（加权平均）
                final_score = (
                    self.config.vector_weight * vector_score
                    + self.config.recency_weight * recency_score
                    + self.config.context_weight * importance_score
                    + 0.1 * frequency_score  # 访问频率权重（固定10%）
                )

                scored_memories.append(
                    (
                        memory,
                        final_score,
                        {
                            "vector": vector_score,
                            "recency": recency_score,
                            "importance": importance_score,
                            "frequency": frequency_score,
                            "final": final_score,
                        },
                    )
                )

                # 更新访问记录
                memory.update_access()

            # 按综合得分排序
            scored_memories.sort(key=lambda x: x[1], reverse=True)

            # 返回 Top-K
            final_memories = [mem for mem, score, details in scored_memories]

            # === 新增：融合瞬时记忆 ===
            try:
                chat_id = normalized_context.get("chat_id")
                instant_memories = await self._retrieve_instant_memories(raw_query, chat_id)
                if instant_memories:
                    # 将瞬时记忆放在列表最前面
                    final_memories = instant_memories + final_memories
                    logger.info(f"融合了 {len(instant_memories)} 条瞬时记忆")

            except Exception as e:
                logger.warning(f"检索瞬时记忆失败: {e}", exc_info=True)
            
            # 最终截断
            final_memories = final_memories[:effective_limit]

            retrieval_time = time.time() - start_time

            # 详细日志 - 打印检索到的有效记忆的完整内容
            if scored_memories:
                logger.debug("🧠 检索到的有效记忆内容详情:")
                for i, (mem, score, details) in enumerate(scored_memories[:effective_limit], 1):
                    try:
                        # 获取记忆的完整内容
                        memory_content = ""
                        if hasattr(mem, "text_content") and mem.text_content:
                            memory_content = mem.text_content
                        elif hasattr(mem, "display") and mem.display:
                            memory_content = mem.display
                        elif hasattr(mem, "content") and mem.content:
                            memory_content = str(mem.content)

                        # 获取记忆的元数据信息
                        memory_type = mem.memory_type.value if hasattr(mem, "memory_type") and mem.memory_type else "unknown"
                        importance = mem.metadata.importance.name if hasattr(mem.metadata, "importance") and mem.metadata.importance else "unknown"
                        confidence = mem.metadata.confidence.name if hasattr(mem.metadata, "confidence") and mem.metadata.confidence else "unknown"
                        created_time = mem.metadata.created_at if hasattr(mem.metadata, "created_at") else 0

                        # 格式化时间
                        import datetime
                        created_time_str = datetime.datetime.fromtimestamp(created_time).strftime("%Y-%m-%d %H:%M:%S") if created_time else "unknown"

                        # 打印记忆详细信息
                        logger.debug(f"  📝 记忆 #{i}")
                        logger.debug(f"     类型: {memory_type} | 重要性: {importance} | 置信度: {confidence}")
                        logger.debug(f"     创建时间: {created_time_str}")
                        logger.debug(f"     综合得分: {details['final']:.3f} (向量:{details['vector']:.3f}, 时效:{details['recency']:.3f}, 重要性:{details['importance']:.3f}, 频率:{details['frequency']:.3f})")

                        # 处理长内容，如果超过200字符则截断并添加省略号
                        display_content = memory_content
                        if len(memory_content) > 200:
                            display_content = memory_content[:200] + "..."

                        logger.debug(f"     内容: {display_content}")

                        # 如果有关键词，也打印出来
                        if hasattr(mem, "keywords") and mem.keywords:
                            keywords_str = ", ".join(mem.keywords[:10])  # 最多显示10个关键词
                            if len(mem.keywords) > 10:
                                keywords_str += f" ... (共{len(mem.keywords)}个关键词)"
                            logger.debug(f"     关键词: {keywords_str}")

                        logger.debug("")  # 空行分隔

                    except Exception as e:
                        logger.warning(f"打印记忆详情时出错: {e}")
                        continue

            logger.info(
                "✅ 三阶段记忆检索完成"
                f" | user={resolved_user_id}"
                f" | 粗筛={len(search_results)}"
                f" | 精筛={len(scored_memories)}"
                f" | 返回={len(final_memories)}"
                f" | duration={retrieval_time:.3f}s"
                f" | query='{optimized_query[:60]}...'"
            )

            self.last_retrieval_time = time.time()
            self.status = MemorySystemStatus.READY

            return final_memories

        except Exception as e:
            self.status = MemorySystemStatus.ERROR
            logger.error(f"❌ 记忆检索失败: {e}", exc_info=True)
            raise

    async def _retrieve_instant_memories(self, query_text: str, chat_id: str | None) -> list[MemoryChunk]:
        """检索瞬时记忆（消息集合）并转换为MemoryChunk"""
        if not self.message_collection_storage or not chat_id:
            return []

        context_text = await self.message_collection_storage.get_message_collection_context(query_text, chat_id=chat_id)
        if not context_text:
            return []

        from src.chat.memory_system.memory_chunk import (
            ContentStructure,
            ImportanceLevel,
            MemoryMetadata,
            MemoryType,
        )

        metadata = MemoryMetadata(
            memory_id=f"instant_{chat_id}_{time.time()}",
            user_id=GLOBAL_MEMORY_SCOPE,
            chat_id=chat_id,
            created_at=time.time(),
            importance=ImportanceLevel.HIGH,  # 瞬时记忆通常具有高重要性
        )
        content = ContentStructure(
            subject="近期对话上下文",
            predicate="相关内容",
            object=context_text,
            display=context_text,
        )
        chunk = MemoryChunk(
            metadata=metadata,
            content=content,
            memory_type=MemoryType.CONTEXTUAL,
        )

        return [chunk]

    @staticmethod
    def _extract_json_payload(response: str) -> str | None:
        """从模型响应中提取JSON部分，兼容Markdown代码块等格式"""
        if not response:
            return None

        stripped = response.strip()

        # 优先处理Markdown代码块格式 ```json ... ```
        code_block_match = re.search(r"```(?:json)?\s*(.*?)```", stripped, re.IGNORECASE | re.DOTALL)
        if code_block_match:
            candidate = code_block_match.group(1).strip()
            if candidate:
                return candidate

        # 回退到查找第一个 JSON 对象的大括号范围
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start != -1 and end != -1 and end > start:
            return stripped[start : end + 1].strip()

        return stripped if stripped.startswith("{") and stripped.endswith("}") else None

    def _normalize_context(
        self, raw_context: dict[str, Any] | None, user_id: str | None, timestamp: float | None
    ) -> dict[str, Any]:
        """标准化上下文，确保必备字段存在且格式正确"""
        context: dict[str, Any] = {}
        if raw_context:
            try:
                context = dict(raw_context)
            except Exception:
                context = dict(raw_context or {})

        # 基础字段：强制使用传入的 user_id 参数（已统一为 GLOBAL_MEMORY_SCOPE）
        context["user_id"] = user_id or GLOBAL_MEMORY_SCOPE
        context["timestamp"] = context.get("timestamp") or timestamp or time.time()
        context["message_type"] = context.get("message_type") or "normal"
        context["platform"] = context.get("platform") or context.get("source_platform") or "unknown"

        # 标准化关键词类型
        keywords = context.get("keywords")
        if keywords is None:
            context["keywords"] = []
        elif isinstance(keywords, tuple):
            context["keywords"] = list(keywords)
        elif not isinstance(keywords, list):
            context["keywords"] = [str(keywords)] if keywords else []

        # 统一 stream_id
        stream_id = context.get("stream_id") or context.get("stram_id")
        if not stream_id:
            potential = context.get("chat_id") or context.get("session_id")
            if isinstance(potential, str) and potential:
                stream_id = potential
        if stream_id:
            context["stream_id"] = stream_id

        # 全局记忆无需聊天隔离
        context["chat_id"] = context.get("chat_id") or "global_chat"

        # 历史窗口配置
        window_candidate = (
            context.get("history_limit") or context.get("history_window") or context.get("memory_history_limit")
        )
        if window_candidate is not None:
            try:
                context["history_limit"] = int(window_candidate)
            except (TypeError, ValueError):
                context.pop("history_limit", None)

        return context

    async def _build_enhanced_query_context(self, raw_query: str, normalized_context: dict[str, Any]) -> dict[str, Any]:
        """构建包含未读消息综合上下文的增强查询上下文

        Args:
            raw_query: 原始查询文本
            normalized_context: 标准化后的基础上下文

        Returns:
            Dict[str, Any]: 包含未读消息综合信息的增强上下文
        """
        enhanced_context = dict(normalized_context)  # 复制基础上下文

        try:
            # 获取stream_id以查找未读消息
            stream_id = normalized_context.get("stream_id")
            if not stream_id:
                logger.debug("未找到stream_id，使用基础上下文进行查询规划")
                return enhanced_context

            # 获取未读消息作为上下文
            unread_messages_summary = await self._collect_unread_messages_context(stream_id)

            if unread_messages_summary:
                enhanced_context["unread_messages_context"] = unread_messages_summary
                enhanced_context["has_unread_context"] = True

                logger.debug(
                    f"为查询规划构建了增强上下文，包含 {len(unread_messages_summary.get('messages', []))} 条未读消息"
                )
            else:
                enhanced_context["has_unread_context"] = False
                logger.debug("未找到未读消息，使用基础上下文进行查询规划")

        except Exception as e:
            logger.warning(f"构建增强查询上下文失败: {e}", exc_info=True)
            enhanced_context["has_unread_context"] = False

        return enhanced_context

    async def _collect_unread_messages_context(self, stream_id: str) -> dict[str, Any] | None:
        """收集未读消息的综合上下文信息

        Args:
            stream_id: 流ID

        Returns:
            Optional[Dict[str, Any]]: 未读消息的综合信息，包含消息列表、关键词、主题等
        """
        try:
            from src.chat.message_receive.chat_stream import get_chat_manager

            chat_manager = get_chat_manager()
            chat_stream = await chat_manager.get_stream(stream_id)

            if not chat_stream or not hasattr(chat_stream, "context_manager"):
                logger.debug(f"未找到stream_id={stream_id}的聊天流或上下文管理器")
                return None

            # 获取未读消息
            context_manager = chat_stream.context_manager
            unread_messages = context_manager.get_unread_messages()

            if not unread_messages:
                logger.debug(f"stream_id={stream_id}没有未读消息")
                return None

            # 构建未读消息摘要
            messages_summary = []
            all_keywords = set()
            participant_names = set()

            for msg in unread_messages[:10]:  # 限制处理最近10条未读消息
                try:
                    # 提取消息内容
                    content = getattr(msg, "processed_plain_text", None) or getattr(msg, "display_message", None) or ""
                    if not content:
                        continue

                    # 提取发送者信息
                    sender_name = "未知用户"
                    if hasattr(msg, "user_info") and msg.user_info:
                        sender_name = (
                            getattr(msg.user_info, "user_nickname", None)
                            or getattr(msg.user_info, "user_cardname", None)
                            or getattr(msg.user_info, "user_id", None)
                            or "未知用户"
                        )

                    participant_names.add(sender_name)

                    # 添加到消息摘要
                    messages_summary.append(
                        {
                            "sender": sender_name,
                            "content": content[:200],  # 限制长度避免过长
                            "timestamp": getattr(msg, "time", None),
                        }
                    )

                    # 提取关键词（简单实现）
                    content_lower = content.lower()
                    # 这里可以添加更复杂的关键词提取逻辑
                    words = [w.strip() for w in content_lower.split() if len(w.strip()) > 1]
                    all_keywords.update(words[:5])  # 每条消息最多取5个词

                except Exception as msg_e:
                    logger.debug(f"处理未读消息时出错: {msg_e}")
                    continue

            if not messages_summary:
                return None

            # 构建综合上下文信息
            unread_context = {
                "messages": messages_summary,
                "total_count": len(unread_messages),
                "processed_count": len(messages_summary),
                "keywords": list(all_keywords)[:20],  # 最多20个关键词
                "participants": list(participant_names),
                "context_summary": self._build_unread_context_summary(messages_summary),
            }

            logger.debug(
                f"收集到未读消息上下文: {len(messages_summary)}条消息，{len(all_keywords)}个关键词，{len(participant_names)}个参与者"
            )
            return unread_context

        except Exception as e:
            logger.warning(f"收集未读消息上下文失败: {e}", exc_info=True)
            return None

    def _build_unread_context_summary(self, messages_summary: list[dict[str, Any]]) -> str:
        """构建未读消息的文本摘要

        Args:
            messages_summary: 未读消息摘要列表

        Returns:
            str: 未读消息的文本摘要
        """
        if not messages_summary:
            return ""

        summary_parts = []
        for msg_info in messages_summary:
            sender = msg_info.get("sender", "未知")
            content = msg_info.get("content", "")
            if content:
                summary_parts.append(f"{sender}: {content}")

        return " | ".join(summary_parts)

    async def _resolve_conversation_context(self, fallback_text: str, context: dict[str, Any] | None) -> str:
        """使用 stream_id 历史消息和相关记忆充实对话文本，默认回退到传入文本"""
        if not context:
            return fallback_text

        user_id = context.get("user_id")
        stream_id = context.get("stream_id") or context.get("stram_id")

        # 优先使用 stream_id 获取历史消息
        if stream_id:
            try:
                from src.chat.message_receive.chat_stream import get_chat_manager

                chat_manager = get_chat_manager()
                chat_stream = await chat_manager.get_stream(stream_id)
                if chat_stream and hasattr(chat_stream, "context_manager"):
                    history_limit = self._determine_history_limit(context)
                    messages = chat_stream.context_manager.get_messages(limit=history_limit, include_unread=True)
                    if messages:
                        transcript = self._format_history_messages(messages)
                        if transcript:
                            cleaned_fallback = (fallback_text or "").strip()
                            if cleaned_fallback and cleaned_fallback not in transcript:
                                transcript = f"{transcript}\n[当前消息] {cleaned_fallback}"

                            logger.debug(
                                "使用 stream_id=%s 的历史消息构建记忆上下文，消息数=%d，限制=%d",
                                stream_id,
                                len(messages),
                                history_limit,
                            )
                            return transcript
                        else:
                            logger.debug(f"stream_id={stream_id} 历史消息格式化失败")
                    else:
                        logger.debug(f"stream_id={stream_id} 未获取到历史消息")
                else:
                    logger.debug(f"未找到 stream_id={stream_id} 对应的聊天流或上下文管理器")
            except Exception as exc:
                logger.warning(f"获取 stream_id={stream_id} 的历史消息失败: {exc}", exc_info=True)

        # 如果无法获取历史消息，尝试检索相关记忆作为上下文
        if user_id and fallback_text:
            try:
                relevant_memories = await self.retrieve_memories_for_building(
                    query_text=fallback_text, user_id=user_id, context=context, limit=3
                )

                if relevant_memories:
                    memory_contexts = []
                    for memory in relevant_memories:
                        memory_contexts.append(f"[历史记忆] {memory.text_content}")

                    memory_transcript = "\n".join(memory_contexts)
                    cleaned_fallback = (fallback_text or "").strip()
                    if cleaned_fallback and cleaned_fallback not in memory_transcript:
                        memory_transcript = f"{memory_transcript}\n[当前消息] {cleaned_fallback}"

                    logger.debug(
                        "使用检索到的历史记忆构建记忆上下文，记忆数=%d，用户=%s", len(relevant_memories), user_id
                    )
                    return memory_transcript

            except Exception as exc:
                logger.warning(f"检索历史记忆作为上下文失败: {exc}", exc_info=True)

        # 回退到传入文本
        return fallback_text

    def _get_build_scope_key(self, context: dict[str, Any], user_id: str | None) -> str | None:
        """确定用于节流控制的记忆构建作用域"""
        return "global_scope"

    def _determine_history_limit(self, context: dict[str, Any]) -> int:
        """确定历史消息获取数量，限制在30-50之间"""
        default_limit = 40
        candidate = context.get("history_limit") or context.get("history_window") or context.get("memory_history_limit")

        if isinstance(candidate, str):
            try:
                candidate = int(candidate)
            except ValueError:
                candidate = None

        if isinstance(candidate, int):
            history_limit = max(30, min(50, candidate))
        else:
            history_limit = default_limit

        return history_limit

    def _format_history_messages(self, messages: list["DatabaseMessages"]) -> str | None:
        """将历史消息格式化为可供LLM处理的多轮对话文本"""
        if not messages:
            return None

        lines: list[str] = []
        for msg in messages:
            try:
                content = getattr(msg, "processed_plain_text", None) or getattr(msg, "display_message", None)
                if not content:
                    continue

                content = re.sub(r"\s+", " ", str(content).strip())
                if not content:
                    continue

                speaker = None
                if hasattr(msg, "user_info") and msg.user_info:
                    speaker = (
                        getattr(msg.user_info, "user_nickname", None)
                        or getattr(msg.user_info, "user_cardname", None)
                        or getattr(msg.user_info, "user_id", None)
                    )
                speaker = speaker or getattr(msg, "user_nickname", None) or getattr(msg, "user_id", None) or "用户"

                timestamp_value = getattr(msg, "time", None) or 0.0
                try:
                    timestamp_dt = datetime.fromtimestamp(float(timestamp_value)) if timestamp_value else datetime.now()
                except (TypeError, ValueError, OSError):
                    timestamp_dt = datetime.now()

                timestamp_str = timestamp_dt.strftime("%Y-%m-%d %H:%M:%S")
                lines.append(f"[{timestamp_str}] {speaker}: {content}")

            except Exception as message_exc:
                logger.debug(f"格式化历史消息失败: {message_exc}")
                continue

        return "\n".join(lines) if lines else None

    async def _assess_information_value(self, text: str, context: dict[str, Any]) -> float:
        """评估信息价值

        Args:
            text: 文本内容
            context: 上下文信息

        Returns:
            价值评分 (0.0-1.0)
        """
        try:
            # 构建评估提示
            prompt = f"""
请评估以下对话内容的信息价值，重点识别包含个人事实、事件、偏好、观点等重要信息的内容。

## 🎯 价值评估重点标准：

### 高价值信息 (0.7-1.0分)：
1. **个人事实** (personal_fact)：包含姓名、年龄、职业、联系方式、住址、健康状况、家庭情况等个人信息
2. **重要事件** (event)：约会、会议、旅行、考试、面试、搬家等重要活动或经历
3. **明确偏好** (preference)：表达喜欢/不喜欢的食物、电影、音乐、品牌、生活习惯等偏好信息
4. **观点态度** (opinion)：对事物的评价、看法、建议、态度等主观观点
5. **核心关系** (relationship)：重要的朋友、家人、同事等人际关系信息

### 中等价值信息 (0.4-0.7分)：
1. **情感表达**：当前情绪状态、心情变化
2. **日常活动**：常规的工作、学习、生活安排
3. **一般兴趣**：兴趣爱好、休闲活动
4. **短期计划**：即将进行的安排和计划

### 低价值信息 (0.0-0.4分)：
1. **寒暄问候**：简单的打招呼、礼貌用语
2. **重复信息**：已经多次提到的相同内容
3. **临时状态**：短暂的情绪波动、临时想法
4. **无关内容**：与用户画像建立无关的信息

对话内容：
{text}

上下文信息：
- 用户ID: {context.get("user_id", "unknown")}
- 消息类型: {context.get("message_type", "unknown")}
- 时间: {datetime.fromtimestamp(context.get("timestamp", time.time()))}

## 📋 评估要求：

### 积极识别原则：
- **宁可高估，不可低估** - 对于可能的个人信息给予较高评估
- **重点关注** - 特别注意包含 personal_fact、event、preference、opinion 的内容
- **细节丰富** - 具体的细节信息比笼统的描述更有价值
- **建立画像** - 有助于建立完整用户画像的信息更有价值

### 评分指导：
- **0.9-1.0**：核心个人信息（姓名、联系方式、重要偏好）
- **0.7-0.8**：重要的个人事实、观点、事件经历
- **0.5-0.6**：一般性偏好、日常活动、情感表达
- **0.3-0.4**：简单的兴趣表达、临时状态
- **0.0-0.2**：寒暄问候、重复内容、无关信息

请以JSON格式输出评估结果：
{{
    "value_score": 0.0到1.0之间的数值,
    "reasoning": "评估理由，包含具体识别到的信息类型",
    "key_factors": ["关键因素1", "关键因素2"],
    "detected_types": ["personal_fact", "preference", "opinion", "event", "relationship", "emotion", "goal"]
}}
"""

            response, _ = await self.value_assessment_model.generate_response_async(prompt, temperature=0.3)

            # 解析响应
            try:
                payload = self._extract_json_payload(response)
                if not payload:
                    raise ValueError("未在响应中找到有效的JSON负载")

                result = orjson.loads(payload)
                value_score = float(result.get("value_score", 0.0))
                reasoning = result.get("reasoning", "")
                key_factors = result.get("key_factors", [])

                logger.info(f"信息价值评估: {value_score:.2f}, 理由: {reasoning}")
                if key_factors:
                    logger.info(f"关键因素: {', '.join(key_factors)}")

                return max(0.0, min(1.0, value_score))

            except (orjson.JSONDecodeError, ValueError) as e:
                preview = response[:200].replace("\n", " ")
                logger.warning(f"解析价值评估响应失败: {e}, 响应片段: {preview}")
                return 0.5  # 默认中等价值

        except Exception as e:
            logger.error(f"信息价值评估失败: {e}", exc_info=True)
            return 0.5  # 默认中等价值

    async def _store_memories_unified(self, memory_chunks: list[MemoryChunk]) -> int:
        """使用统一存储系统存储记忆块"""
        if not memory_chunks or not self.unified_storage:
            return 0

        try:
            # 直接存储到统一存储系统
            stored_count = await self.unified_storage.store_memories(memory_chunks)

            logger.debug(
                "统一存储成功存储 %d 条记忆",
                stored_count,
            )

            return stored_count

        except Exception as e:
            logger.error(f"统一存储记忆失败: {e}", exc_info=True)
            return 0

    # 保留原有方法以兼容旧代码
    async def _store_memories(self, memory_chunks: list[MemoryChunk]) -> int:
        """兼容性方法：重定向到统一存储"""
        return await self._store_memories_unified(memory_chunks)

    def _merge_existing_memory(self, existing: MemoryChunk, incoming: MemoryChunk) -> None:
        """将新记忆的信息合并到已存在的记忆中"""
        updated = False

        for keyword in incoming.keywords:
            if keyword not in existing.keywords:
                existing.add_keyword(keyword)
                updated = True

        for tag in incoming.tags:
            if tag not in existing.tags:
                existing.add_tag(tag)
                updated = True

        for category in incoming.categories:
            if category not in existing.categories:
                existing.add_category(category)
                updated = True

        if incoming.metadata.source_context:
            existing.metadata.source_context = incoming.metadata.source_context

        if incoming.metadata.importance.value > existing.metadata.importance.value:
            existing.metadata.importance = incoming.metadata.importance
            updated = True

        if incoming.metadata.confidence.value > existing.metadata.confidence.value:
            existing.metadata.confidence = incoming.metadata.confidence
            updated = True

        if incoming.metadata.relevance_score > existing.metadata.relevance_score:
            existing.metadata.relevance_score = incoming.metadata.relevance_score
            updated = True

        if updated:
            existing.metadata.last_modified = time.time()

    def _populate_memory_fingerprints(self) -> None:
        """基于当前缓存构建记忆指纹映射"""
        self._memory_fingerprints.clear()
        for memory in self.unified_storage.memory_cache.values():
            fingerprint = self._build_memory_fingerprint(memory)
            key = self._fingerprint_key(memory.user_id, fingerprint)
            self._memory_fingerprints[key] = memory.memory_id

    def _register_memory_fingerprints(self, memories: list[MemoryChunk]) -> None:
        for memory in memories:
            fingerprint = self._build_memory_fingerprint(memory)
            key = self._fingerprint_key(memory.user_id, fingerprint)
            self._memory_fingerprints[key] = memory.memory_id

    def _build_memory_fingerprint(self, memory: MemoryChunk) -> str:
        subjects = memory.subjects or []
        subject_part = "|".join(sorted(s.strip() for s in subjects if s))
        predicate_part = (memory.content.predicate or "").strip()

        obj = memory.content.object
        if isinstance(obj, dict | list):
            obj_part = orjson.dumps(obj, option=orjson.OPT_SORT_KEYS).decode("utf-8")
        else:
            obj_part = str(obj).strip()

        base = "|".join(
            [
                str(memory.user_id or "unknown"),
                memory.memory_type.value,
                subject_part,
                predicate_part,
                obj_part,
            ]
        )

        return hashlib.sha256(base.encode("utf-8")).hexdigest()

    @staticmethod
    def _fingerprint_key(user_id: str, fingerprint: str) -> str:
        return f"{user_id!s}:{fingerprint}"

    def _compute_memory_score(self, query_text: str, memory: MemoryChunk, context: dict[str, Any]) -> float:
        """根据查询和上下文为记忆计算匹配分数"""
        tokens_query = self._tokenize_text(query_text)
        tokens_memory = self._tokenize_text(memory.text_content)

        if tokens_query and tokens_memory:
            base_score = len(tokens_query & tokens_memory) / len(tokens_query | tokens_memory)
        else:
            base_score = 0.0

        context_keywords = context.get("keywords") or []
        keyword_overlap = 0.0
        if context_keywords:
            memory_keywords = {k.lower() for k in memory.keywords}
            keyword_overlap = len(memory_keywords & {k.lower() for k in context_keywords}) / max(
                len(context_keywords), 1
            )

        importance_boost = (memory.metadata.importance.value - 1) / 3 * 0.1
        confidence_boost = (memory.metadata.confidence.value - 1) / 3 * 0.05

        final_score = base_score * 0.7 + keyword_overlap * 0.15 + importance_boost + confidence_boost
        return max(0.0, min(1.0, final_score))

    def _tokenize_text(self, text: str) -> set[str]:
        """简单分词，兼容中英文"""
        if not text:
            return set()

        tokens = re.findall(r"[\w\u4e00-\u9fa5]+", text.lower())
        return {token for token in tokens if len(token) > 1}

    async def maintenance(self):
        """系统维护操作（简化版）"""
        try:
            logger.info("开始简化记忆系统维护...")

            # 执行遗忘检查
            if self.unified_storage and self.forgetting_engine:
                forgetting_result = await self.unified_storage.perform_forgetting_check()
                if "error" not in forgetting_result:
                    logger.info(f"遗忘检查完成: {forgetting_result.get('stats', {})}")
                else:
                    logger.warning(f"遗忘检查失败: {forgetting_result['error']}")

            # 保存存储数据
            if self.unified_storage:
                await self.unified_storage.save_storage()

            # 记忆融合引擎维护
            if self.fusion_engine:
                await self.fusion_engine.maintenance()

            # 清理消息集合（每12小时）
            if self.message_collection_storage:
                current_time = time.time()
                if current_time - self.last_collection_cleanup_time > 12 * 3600:
                    logger.info("开始清理过期的消息集合...")
                    self.message_collection_storage.clear_all()
                    self.last_collection_cleanup_time = current_time
                    logger.info("✅ 消息集合清理完成")

            logger.info("✅ 简化记忆系统维护完成")

        except Exception as e:
            logger.error(f"❌ 记忆系统维护失败: {e}", exc_info=True)

    def start_hippocampus_sampling(self):
        """启动海马体采样"""
        if self.hippocampus_sampler:
            asyncio.create_task(self.hippocampus_sampler.start_background_sampling())
            logger.info("海马体后台采样已启动")
        else:
            logger.warning("海马体采样器未初始化，无法启动采样")

    def stop_hippocampus_sampling(self):
        """停止海马体采样"""
        if self.hippocampus_sampler:
            self.hippocampus_sampler.stop_background_sampling()
            logger.info("海马体后台采样已停止")

    def get_system_stats(self) -> dict[str, Any]:
        """获取系统统计信息"""
        base_stats = {
            "status": self.status.value,
            "total_memories": self.total_memories,
            "last_build_time": self.last_build_time,
            "last_retrieval_time": self.last_retrieval_time,
            "config": asdict(self.config),
        }

        # 添加海马体采样器统计
        if self.hippocampus_sampler:
            base_stats["hippocampus_sampler"] = self.hippocampus_sampler.get_sampling_stats()

        # 添加存储统计
        if self.unified_storage:
            try:
                storage_stats = self.unified_storage.get_storage_stats()
                base_stats["storage_stats"] = storage_stats
            except Exception as e:
                logger.debug(f"获取存储统计失败: {e}")

        return base_stats

    async def shutdown(self):
        """关闭系统（简化版）"""
        try:
            logger.info("正在关闭简化记忆系统...")

            # 停止海马体采样
            if self.hippocampus_sampler:
                self.hippocampus_sampler.stop_background_sampling()

            # 保存统一存储数据
            if self.unified_storage:
                self.unified_storage.cleanup()

            logger.info("简化记忆系统已关闭")

        except Exception as e:
            logger.error(f"记忆系统关闭失败: {e}", exc_info=True)

    async def _rebuild_vector_storage_if_needed(self):
        """重建向量存储（如果需要）"""
        try:
            # 检查是否有记忆缓存数据
            if not hasattr(self.unified_storage, "memory_cache") or not self.unified_storage.memory_cache:
                logger.info("无记忆缓存数据，跳过向量存储重建")
                return

            logger.info(f"开始重建向量存储，记忆数量: {len(self.unified_storage.memory_cache)}")

            # 收集需要重建向量的记忆
            memories_to_rebuild = []
            if self.unified_storage:
                for memory in self.unified_storage.memory_cache.values():
                    # 检查记忆是否有有效的 display 文本
                    if memory.display and memory.display.strip():
                        memories_to_rebuild.append(memory)
                    elif memory.text_content and memory.text_content.strip():
                        memories_to_rebuild.append(memory)

            if not memories_to_rebuild:
                logger.warning("没有找到可重建向量的记忆")
                return

            logger.info(f"准备为 {len(memories_to_rebuild)} 条记忆重建向量")

            # 批量重建向量
            batch_size = 10
            rebuild_count = 0

            for i in range(0, len(memories_to_rebuild), batch_size):
                batch = memories_to_rebuild[i : i + batch_size]
                try:
                    await self.unified_storage.store_memories(batch)
                    rebuild_count += len(batch)

                    if rebuild_count % 50 == 0:
                        logger.info(f"已重建向量: {rebuild_count}/{len(memories_to_rebuild)}")

                except Exception as e:
                    logger.error(f"批量重建向量失败: {e}")
                    continue

            # 向量数据在 store_memories 中已保存，此处无需额外操作
            if self.unified_storage:
                storage_stats = self.unified_storage.get_storage_stats()
                final_count = storage_stats.get("total_vectors", 0)
                logger.info(f"✅ 向量存储重建完成，最终向量数量: {final_count}")
            else:
                logger.warning("向量存储重建完成，但无法获取最终向量数量，因为存储系统未初始化")

        except Exception as e:
            logger.error(f"向量存储重建失败: {e}", exc_info=True)


# 全局记忆系统实例
memory_system: MemorySystem = None


def get_memory_system() -> MemorySystem:
    """获取全局记忆系统实例"""
    global memory_system
    if memory_system is None:
        memory_system = MemorySystem()
    return memory_system


async def initialize_memory_system(llm_model: LLMRequest | None = None):
    """初始化全局记忆系统"""
    global memory_system
    if memory_system is None:
        memory_system = MemorySystem(llm_model=llm_model)
    await memory_system.initialize()

    # 根据配置启动海马体采样
    sampling_mode = getattr(global_config.memory, "memory_sampling_mode", "immediate")
    if sampling_mode in ["hippocampus", "all"]:
        memory_system.start_hippocampus_sampling()

    return memory_system
