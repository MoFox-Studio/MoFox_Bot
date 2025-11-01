"""智能数据预加载器

实现智能的数据预加载策略：
- 热点数据识别：基于访问频率和时间衰减
- 关联数据预取：预测性地加载相关数据
- 自适应策略：根据命中率动态调整
- 异步预加载：不阻塞主线程
"""

import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.database.optimization.cache_manager import get_cache
from src.common.logger import get_logger

logger = get_logger("preloader")


@dataclass
class AccessPattern:
    """访问模式统计
    
    Attributes:
        key: 数据键
        access_count: 访问次数
        last_access: 最后访问时间
        score: 热度评分（时间衰减后的访问频率）
        related_keys: 关联数据键列表
    """
    key: str
    access_count: int = 0
    last_access: float = 0
    score: float = 0
    related_keys: list[str] = field(default_factory=list)


class DataPreloader:
    """数据预加载器
    
    通过分析访问模式，预测并预加载可能需要的数据
    """

    def __init__(
        self,
        decay_factor: float = 0.9,
        preload_threshold: float = 0.5,
        max_patterns: int = 1000,
    ):
        """初始化预加载器
        
        Args:
            decay_factor: 时间衰减因子（0-1），越小衰减越快
            preload_threshold: 预加载阈值，score超过此值时预加载
            max_patterns: 最大跟踪的访问模式数量
        """
        self.decay_factor = decay_factor
        self.preload_threshold = preload_threshold
        self.max_patterns = max_patterns
        
        # 访问模式跟踪
        self._patterns: dict[str, AccessPattern] = {}
        # 关联关系：key -> [related_keys]
        self._associations: dict[str, set[str]] = defaultdict(set)
        # 预加载任务
        self._preload_tasks: set[asyncio.Task] = set()
        # 统计信息
        self._total_accesses = 0
        self._preload_count = 0
        self._preload_hits = 0
        
        self._lock = asyncio.Lock()
        
        logger.info(
            f"数据预加载器初始化: 衰减因子={decay_factor}, "
            f"预加载阈值={preload_threshold}"
        )

    async def record_access(
        self,
        key: str,
        related_keys: Optional[list[str]] = None,
    ) -> None:
        """记录数据访问
        
        Args:
            key: 被访问的数据键
            related_keys: 关联访问的数据键列表
        """
        async with self._lock:
            self._total_accesses += 1
            now = time.time()
            
            # 更新或创建访问模式
            if key in self._patterns:
                pattern = self._patterns[key]
                pattern.access_count += 1
                pattern.last_access = now
            else:
                pattern = AccessPattern(
                    key=key,
                    access_count=1,
                    last_access=now,
                )
                self._patterns[key] = pattern
            
            # 更新热度评分（时间衰减）
            pattern.score = self._calculate_score(pattern)
            
            # 记录关联关系
            if related_keys:
                self._associations[key].update(related_keys)
                pattern.related_keys = list(self._associations[key])
            
            # 如果模式过多，删除评分最低的
            if len(self._patterns) > self.max_patterns:
                min_key = min(self._patterns, key=lambda k: self._patterns[k].score)
                del self._patterns[min_key]
                if min_key in self._associations:
                    del self._associations[min_key]

    async def should_preload(self, key: str) -> bool:
        """判断是否应该预加载某个数据
        
        Args:
            key: 数据键
            
        Returns:
            是否应该预加载
        """
        async with self._lock:
            pattern = self._patterns.get(key)
            if pattern is None:
                return False
            
            # 更新评分
            pattern.score = self._calculate_score(pattern)
            
            return pattern.score >= self.preload_threshold

    async def get_preload_keys(self, limit: int = 100) -> list[str]:
        """获取应该预加载的数据键列表
        
        Args:
            limit: 最大返回数量
            
        Returns:
            按评分排序的数据键列表
        """
        async with self._lock:
            # 更新所有评分
            for pattern in self._patterns.values():
                pattern.score = self._calculate_score(pattern)
            
            # 按评分排序
            sorted_patterns = sorted(
                self._patterns.values(),
                key=lambda p: p.score,
                reverse=True,
            )
            
            # 返回超过阈值的键
            return [
                p.key for p in sorted_patterns[:limit]
                if p.score >= self.preload_threshold
            ]

    async def get_related_keys(self, key: str) -> list[str]:
        """获取关联数据键
        
        Args:
            key: 数据键
            
        Returns:
            关联数据键列表
        """
        async with self._lock:
            return list(self._associations.get(key, []))

    async def preload_data(
        self,
        key: str,
        loader: Callable[[], Awaitable[Any]],
    ) -> None:
        """预加载数据
        
        Args:
            key: 数据键
            loader: 异步加载函数
        """
        try:
            cache = await get_cache()
            
            # 检查缓存中是否已存在
            if await cache.l1_cache.get(key) is not None:
                return
            
            # 加载数据
            logger.debug(f"预加载数据: {key}")
            data = await loader()
            
            if data is not None:
                # 写入缓存
                await cache.set(key, data)
                self._preload_count += 1
                
                # 预加载关联数据
                related_keys = await self.get_related_keys(key)
                for related_key in related_keys[:5]:  # 最多预加载5个关联项
                    if await cache.l1_cache.get(related_key) is None:
                        # 这里需要调用者提供关联数据的加载函数
                        # 暂时只记录，不实际加载
                        logger.debug(f"发现关联数据: {related_key}")
        
        except Exception as e:
            logger.error(f"预加载数据失败 {key}: {e}", exc_info=True)

    async def start_preload_batch(
        self,
        session: AsyncSession,
        loaders: dict[str, Callable[[], Awaitable[Any]]],
    ) -> None:
        """批量启动预加载任务
        
        Args:
            session: 数据库会话
            loaders: 数据键到加载函数的映射
        """
        preload_keys = await self.get_preload_keys()
        
        for key in preload_keys:
            if key in loaders:
                loader = loaders[key]
                task = asyncio.create_task(self.preload_data(key, loader))
                self._preload_tasks.add(task)
                task.add_done_callback(self._preload_tasks.discard)

    async def record_hit(self, key: str) -> None:
        """记录预加载命中
        
        当缓存命中的数据是预加载的，调用此方法统计
        
        Args:
            key: 数据键
        """
        async with self._lock:
            self._preload_hits += 1

    async def get_stats(self) -> dict[str, Any]:
        """获取统计信息"""
        async with self._lock:
            preload_hit_rate = (
                self._preload_hits / self._preload_count
                if self._preload_count > 0
                else 0.0
            )
            
            return {
                "total_accesses": self._total_accesses,
                "tracked_patterns": len(self._patterns),
                "associations": len(self._associations),
                "preload_count": self._preload_count,
                "preload_hits": self._preload_hits,
                "preload_hit_rate": preload_hit_rate,
                "active_tasks": len(self._preload_tasks),
            }

    async def clear(self) -> None:
        """清空所有统计信息"""
        async with self._lock:
            self._patterns.clear()
            self._associations.clear()
            self._total_accesses = 0
            self._preload_count = 0
            self._preload_hits = 0
            
            # 取消所有预加载任务
            for task in self._preload_tasks:
                task.cancel()
            self._preload_tasks.clear()

    def _calculate_score(self, pattern: AccessPattern) -> float:
        """计算热度评分
        
        使用时间衰减的访问频率：
        score = access_count * decay_factor^(time_since_last_access)
        
        Args:
            pattern: 访问模式
            
        Returns:
            热度评分
        """
        now = time.time()
        time_diff = now - pattern.last_access
        
        # 时间衰减（以小时为单位）
        hours_passed = time_diff / 3600
        decay = self.decay_factor ** hours_passed
        
        # 评分 = 访问次数 * 时间衰减
        score = pattern.access_count * decay
        
        return score


class CommonDataPreloader:
    """常见数据预加载器
    
    针对特定的数据类型提供预加载策略
    """

    def __init__(self, preloader: DataPreloader):
        """初始化
        
        Args:
            preloader: 基础预加载器
        """
        self.preloader = preloader

    async def preload_user_data(
        self,
        session: AsyncSession,
        user_id: str,
        platform: str,
    ) -> None:
        """预加载用户相关数据
        
        包括：个人信息、权限、关系等
        
        Args:
            session: 数据库会话
            user_id: 用户ID
            platform: 平台
        """
        from src.common.database.core.models import PersonInfo, UserPermissions, UserRelationships
        
        # 预加载个人信息
        await self._preload_model(
            session,
            f"person:{platform}:{user_id}",
            PersonInfo,
            {"platform": platform, "user_id": user_id},
        )
        
        # 预加载用户权限
        await self._preload_model(
            session,
            f"permissions:{platform}:{user_id}",
            UserPermissions,
            {"platform": platform, "user_id": user_id},
        )
        
        # 预加载用户关系
        await self._preload_model(
            session,
            f"relationship:{user_id}",
            UserRelationships,
            {"user_id": user_id},
        )

    async def preload_chat_context(
        self,
        session: AsyncSession,
        stream_id: str,
        limit: int = 50,
    ) -> None:
        """预加载聊天上下文
        
        包括：最近消息、聊天流信息等
        
        Args:
            session: 数据库会话
            stream_id: 聊天流ID
            limit: 消息数量限制
        """
        from src.common.database.core.models import ChatStreams, Messages
        
        # 预加载聊天流信息
        await self._preload_model(
            session,
            f"stream:{stream_id}",
            ChatStreams,
            {"stream_id": stream_id},
        )
        
        # 预加载最近消息（这个比较复杂，暂时跳过）
        # TODO: 实现消息列表的预加载

    async def _preload_model(
        self,
        session: AsyncSession,
        cache_key: str,
        model_class: type,
        filters: dict[str, Any],
    ) -> None:
        """预加载模型数据
        
        Args:
            session: 数据库会话
            cache_key: 缓存键
            model_class: 模型类
            filters: 过滤条件
        """
        async def loader():
            stmt = select(model_class)
            for key, value in filters.items():
                stmt = stmt.where(getattr(model_class, key) == value)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()
        
        await self.preloader.preload_data(cache_key, loader)


# 全局预加载器实例
_global_preloader: Optional[DataPreloader] = None
_preloader_lock = asyncio.Lock()


async def get_preloader() -> DataPreloader:
    """获取全局预加载器实例（单例）"""
    global _global_preloader
    
    if _global_preloader is None:
        async with _preloader_lock:
            if _global_preloader is None:
                _global_preloader = DataPreloader()
    
    return _global_preloader


async def close_preloader() -> None:
    """关闭全局预加载器"""
    global _global_preloader
    
    if _global_preloader is not None:
        await _global_preloader.clear()
        _global_preloader = None
        logger.info("全局预加载器已关闭")
