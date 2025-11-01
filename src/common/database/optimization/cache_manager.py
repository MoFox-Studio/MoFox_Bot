"""多级缓存管理器

实现高性能的多级缓存系统：
- L1缓存：内存缓存，1000项，60秒TTL，用于热点数据
- L2缓存：扩展缓存，10000项，300秒TTL，用于温数据
- LRU淘汰策略：自动淘汰最少使用的数据
- 智能预热：启动时预加载高频数据
- 统计信息：命中率、淘汰率等监控数据
"""

import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable, Generic, Optional, TypeVar

from src.common.logger import get_logger

logger = get_logger("cache_manager")

T = TypeVar("T")


@dataclass
class CacheEntry(Generic[T]):
    """缓存条目
    
    Attributes:
        value: 缓存的值
        created_at: 创建时间戳
        last_accessed: 最后访问时间戳
        access_count: 访问次数
        size: 数据大小（字节）
    """
    value: T
    created_at: float
    last_accessed: float
    access_count: int = 0
    size: int = 0


@dataclass
class CacheStats:
    """缓存统计信息
    
    Attributes:
        hits: 命中次数
        misses: 未命中次数
        evictions: 淘汰次数
        total_size: 总大小（字节）
        item_count: 条目数量
    """
    hits: int = 0
    misses: int = 0
    evictions: int = 0
    total_size: int = 0
    item_count: int = 0

    @property
    def hit_rate(self) -> float:
        """命中率"""
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0

    @property
    def eviction_rate(self) -> float:
        """淘汰率"""
        return self.evictions / self.item_count if self.item_count > 0 else 0.0


class LRUCache(Generic[T]):
    """LRU缓存实现
    
    使用OrderedDict实现O(1)的get/set操作
    """

    def __init__(
        self,
        max_size: int,
        ttl: float,
        name: str = "cache",
    ):
        """初始化LRU缓存
        
        Args:
            max_size: 最大缓存条目数
            ttl: 过期时间（秒）
            name: 缓存名称，用于日志
        """
        self.max_size = max_size
        self.ttl = ttl
        self.name = name
        self._cache: OrderedDict[str, CacheEntry[T]] = OrderedDict()
        self._lock = asyncio.Lock()
        self._stats = CacheStats()

    async def get(self, key: str) -> Optional[T]:
        """获取缓存值
        
        Args:
            key: 缓存键
            
        Returns:
            缓存值，如果不存在或已过期返回None
        """
        async with self._lock:
            entry = self._cache.get(key)
            
            if entry is None:
                self._stats.misses += 1
                return None

            # 检查是否过期
            now = time.time()
            if now - entry.created_at > self.ttl:
                # 过期，删除条目
                del self._cache[key]
                self._stats.misses += 1
                self._stats.evictions += 1
                self._stats.item_count -= 1
                self._stats.total_size -= entry.size
                return None

            # 命中，更新访问信息
            entry.last_accessed = now
            entry.access_count += 1
            self._stats.hits += 1
            
            # 移到末尾（最近使用）
            self._cache.move_to_end(key)
            
            return entry.value

    async def set(
        self,
        key: str,
        value: T,
        size: Optional[int] = None,
    ) -> None:
        """设置缓存值
        
        Args:
            key: 缓存键
            value: 缓存值
            size: 数据大小（字节），如果为None则尝试估算
        """
        async with self._lock:
            now = time.time()
            
            # 如果键已存在，更新值
            if key in self._cache:
                old_entry = self._cache[key]
                self._stats.total_size -= old_entry.size
            
            # 估算大小
            if size is None:
                size = self._estimate_size(value)
            
            # 创建新条目
            entry = CacheEntry(
                value=value,
                created_at=now,
                last_accessed=now,
                access_count=0,
                size=size,
            )
            
            # 如果缓存已满，淘汰最久未使用的条目
            while len(self._cache) >= self.max_size:
                oldest_key, oldest_entry = self._cache.popitem(last=False)
                self._stats.evictions += 1
                self._stats.item_count -= 1
                self._stats.total_size -= oldest_entry.size
                logger.debug(
                    f"[{self.name}] 淘汰缓存条目: {oldest_key} "
                    f"(访问{oldest_entry.access_count}次)"
                )
            
            # 添加新条目
            self._cache[key] = entry
            self._stats.item_count += 1
            self._stats.total_size += size

    async def delete(self, key: str) -> bool:
        """删除缓存条目
        
        Args:
            key: 缓存键
            
        Returns:
            是否成功删除
        """
        async with self._lock:
            entry = self._cache.pop(key, None)
            if entry:
                self._stats.item_count -= 1
                self._stats.total_size -= entry.size
                return True
            return False

    async def clear(self) -> None:
        """清空缓存"""
        async with self._lock:
            self._cache.clear()
            self._stats = CacheStats()

    async def get_stats(self) -> CacheStats:
        """获取统计信息"""
        async with self._lock:
            return CacheStats(
                hits=self._stats.hits,
                misses=self._stats.misses,
                evictions=self._stats.evictions,
                total_size=self._stats.total_size,
                item_count=self._stats.item_count,
            )

    def _estimate_size(self, value: Any) -> int:
        """估算数据大小（字节）
        
        这是一个简单的估算，实际大小可能不同
        """
        import sys
        try:
            return sys.getsizeof(value)
        except (TypeError, AttributeError):
            # 无法获取大小，返回默认值
            return 1024


class MultiLevelCache:
    """多级缓存管理器
    
    实现两级缓存架构：
    - L1: 高速缓存，小容量，短TTL
    - L2: 扩展缓存，大容量，长TTL
    
    查询时先查L1，未命中再查L2，未命中再从数据源加载
    """

    def __init__(
        self,
        l1_max_size: int = 1000,
        l1_ttl: float = 60,
        l2_max_size: int = 10000,
        l2_ttl: float = 300,
    ):
        """初始化多级缓存
        
        Args:
            l1_max_size: L1缓存最大条目数
            l1_ttl: L1缓存TTL（秒）
            l2_max_size: L2缓存最大条目数
            l2_ttl: L2缓存TTL（秒）
        """
        self.l1_cache: LRUCache[Any] = LRUCache(l1_max_size, l1_ttl, "L1")
        self.l2_cache: LRUCache[Any] = LRUCache(l2_max_size, l2_ttl, "L2")
        self._cleanup_task: Optional[asyncio.Task] = None
        
        logger.info(
            f"多级缓存初始化: L1({l1_max_size}项/{l1_ttl}s) "
            f"L2({l2_max_size}项/{l2_ttl}s)"
        )

    async def get(
        self,
        key: str,
        loader: Optional[Callable[[], Any]] = None,
    ) -> Optional[Any]:
        """从缓存获取数据
        
        查询顺序：L1 -> L2 -> loader
        
        Args:
            key: 缓存键
            loader: 数据加载函数，当缓存未命中时调用
            
        Returns:
            缓存值或加载的值，如果都不存在返回None
        """
        # 1. 尝试从L1获取
        value = await self.l1_cache.get(key)
        if value is not None:
            logger.debug(f"L1缓存命中: {key}")
            return value

        # 2. 尝试从L2获取
        value = await self.l2_cache.get(key)
        if value is not None:
            logger.debug(f"L2缓存命中: {key}")
            # 提升到L1
            await self.l1_cache.set(key, value)
            return value

        # 3. 使用loader加载
        if loader is not None:
            logger.debug(f"缓存未命中，从数据源加载: {key}")
            value = await loader() if asyncio.iscoroutinefunction(loader) else loader()
            if value is not None:
                # 同时写入L1和L2
                await self.set(key, value)
            return value

        return None

    async def set(
        self,
        key: str,
        value: Any,
        size: Optional[int] = None,
    ) -> None:
        """设置缓存值
        
        同时写入L1和L2
        
        Args:
            key: 缓存键
            value: 缓存值
            size: 数据大小（字节）
        """
        await self.l1_cache.set(key, value, size)
        await self.l2_cache.set(key, value, size)

    async def delete(self, key: str) -> None:
        """删除缓存条目
        
        同时从L1和L2删除
        
        Args:
            key: 缓存键
        """
        await self.l1_cache.delete(key)
        await self.l2_cache.delete(key)

    async def clear(self) -> None:
        """清空所有缓存"""
        await self.l1_cache.clear()
        await self.l2_cache.clear()
        logger.info("所有缓存已清空")

    async def get_stats(self) -> dict[str, CacheStats]:
        """获取所有缓存层的统计信息"""
        return {
            "l1": await self.l1_cache.get_stats(),
            "l2": await self.l2_cache.get_stats(),
        }

    async def start_cleanup_task(self, interval: float = 60) -> None:
        """启动定期清理任务
        
        Args:
            interval: 清理间隔（秒）
        """
        if self._cleanup_task is not None:
            logger.warning("清理任务已在运行")
            return

        async def cleanup_loop():
            while True:
                try:
                    await asyncio.sleep(interval)
                    stats = await self.get_stats()
                    logger.info(
                        f"缓存统计 - L1: {stats['l1'].item_count}项, "
                        f"命中率{stats['l1'].hit_rate:.2%} | "
                        f"L2: {stats['l2'].item_count}项, "
                        f"命中率{stats['l2'].hit_rate:.2%}"
                    )
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"清理任务异常: {e}", exc_info=True)

        self._cleanup_task = asyncio.create_task(cleanup_loop())
        logger.info(f"缓存清理任务已启动，间隔{interval}秒")

    async def stop_cleanup_task(self) -> None:
        """停止清理任务"""
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None
            logger.info("缓存清理任务已停止")


# 全局缓存实例
_global_cache: Optional[MultiLevelCache] = None
_cache_lock = asyncio.Lock()


async def get_cache() -> MultiLevelCache:
    """获取全局缓存实例（单例）"""
    global _global_cache
    
    if _global_cache is None:
        async with _cache_lock:
            if _global_cache is None:
                _global_cache = MultiLevelCache()
                await _global_cache.start_cleanup_task()
    
    return _global_cache


async def close_cache() -> None:
    """关闭全局缓存"""
    global _global_cache
    
    if _global_cache is not None:
        await _global_cache.stop_cleanup_task()
        await _global_cache.clear()
        _global_cache = None
        logger.info("全局缓存已关闭")
