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
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from src.common.logger import get_logger
from src.common.memory_utils import estimate_size_smart

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

    async def get(self, key: str) -> T | None:
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
        size: int | None = None,
        ttl: float | None = None,
    ) -> None:
        """设置缓存值

        Args:
            key: 缓存键
            value: 缓存值
            size: 数据大小（字节），如果为None则尝试估算
            ttl: 自定义过期时间（秒），如果为None则使用默认TTL
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

            # 创建新条目（如果指定了ttl，则修改created_at来实现自定义TTL）
            # 通过调整created_at，使得: now - created_at + custom_ttl = self.ttl
            # 即: created_at = now - (self.ttl - custom_ttl)
            if ttl is not None and ttl != self.ttl:
                # 调整创建时间以实现自定义TTL
                adjusted_created_at = now - (self.ttl - ttl)
                logger.debug(
                    f"[{self.name}] 使用自定义TTL {ttl}s (默认{self.ttl}s) for key: {key}"
                )
            else:
                adjusted_created_at = now

            entry = CacheEntry(
                value=value,
                created_at=adjusted_created_at,
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
        """估算数据大小（字节）- 使用准确的估算方法

        使用深度递归估算，比 sys.getsizeof() 更准确
        """
        try:
            return estimate_size_smart(value)
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
        max_memory_mb: int = 100,
        max_item_size_mb: int = 1,
    ):
        """初始化多级缓存

        Args:
            l1_max_size: L1缓存最大条目数
            l1_ttl: L1缓存TTL（秒）
            l2_max_size: L2缓存最大条目数
            l2_ttl: L2缓存TTL（秒）
            max_memory_mb: 最大内存占用（MB）
            max_item_size_mb: 单个缓存条目最大大小（MB）
        """
        self.l1_cache: LRUCache[Any] = LRUCache(l1_max_size, l1_ttl, "L1")
        self.l2_cache: LRUCache[Any] = LRUCache(l2_max_size, l2_ttl, "L2")
        self.max_memory_bytes = max_memory_mb * 1024 * 1024
        self.max_item_size_bytes = max_item_size_mb * 1024 * 1024
        self._cleanup_task: asyncio.Task | None = None
        self._is_closing = False  # 🔧 添加关闭标志

        logger.info(
            f"多级缓存初始化: L1({l1_max_size}项/{l1_ttl}s) "
            f"L2({l2_max_size}项/{l2_ttl}s) 内存上限({max_memory_mb}MB) "
            f"单项上限({max_item_size_mb}MB)"
        )

    async def get(
        self,
        key: str,
        loader: Callable[[], Any] | None = None,
    ) -> Any | None:
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
        size: int | None = None,
        ttl: float | None = None,
    ) -> None:
        """设置缓存值

        同时写入L1和L2

        Args:
            key: 缓存键
            value: 缓存值
            size: 数据大小（字节）
            ttl: 自定义过期时间（秒），如果为None则使用默认TTL
        """
        # 估算数据大小（如果未提供）
        if size is None:
            size = estimate_size_smart(value)

        # 检查单个条目大小是否超过限制
        if size > self.max_item_size_bytes:
            logger.warning(
                f"缓存条目过大，跳过缓存: key={key}, "
                f"size={size / (1024 * 1024):.2f}MB, "
                f"limit={self.max_item_size_bytes / (1024 * 1024):.2f}MB"
            )
            return

        # 根据TTL决定写入哪个缓存层
        if ttl is not None:
            # 有自定义TTL，根据TTL大小决定写入层级
            if ttl <= self.l1_cache.ttl:
                # 短TTL，只写入L1
                await self.l1_cache.set(key, value, size, ttl)
            elif ttl <= self.l2_cache.ttl:
                # 中等TTL，写入L1和L2
                await self.l1_cache.set(key, value, size, ttl)
                await self.l2_cache.set(key, value, size, ttl)
            else:
                # 长TTL，只写入L2
                await self.l2_cache.set(key, value, size, ttl)
        else:
            # 没有自定义TTL，使用默认行为（同时写入L1和L2）
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

    async def get_stats(self) -> dict[str, Any]:
        """获取所有缓存层的统计信息（修正版，避免重复计数）"""
        l1_stats = await self.l1_cache.get_stats()
        l2_stats = await self.l2_cache.get_stats()

        # 🔧 修复：计算实际独占的内存，避免L1和L2共享数据的重复计数
        l1_keys = set(self.l1_cache._cache.keys())
        l2_keys = set(self.l2_cache._cache.keys())

        shared_keys = l1_keys & l2_keys
        l1_only_keys = l1_keys - l2_keys
        l2_only_keys = l2_keys - l1_keys

        # 计算实际总内存（避免重复计数）
        # L1独占内存
        l1_only_size = sum(
            self.l1_cache._cache[k].size
            for k in l1_only_keys
            if k in self.l1_cache._cache
        )
        # L2独占内存
        l2_only_size = sum(
            self.l2_cache._cache[k].size
            for k in l2_only_keys
            if k in self.l2_cache._cache
        )
        # 共享内存（只计算一次，使用L1的数据）
        shared_size = sum(
            self.l1_cache._cache[k].size
            for k in shared_keys
            if k in self.l1_cache._cache
        )

        actual_total_size = l1_only_size + l2_only_size + shared_size

        return {
            "l1": l1_stats,
            "l2": l2_stats,
            "total_memory_mb": actual_total_size / (1024 * 1024),
            "l1_only_mb": l1_only_size / (1024 * 1024),
            "l2_only_mb": l2_only_size / (1024 * 1024),
            "shared_mb": shared_size / (1024 * 1024),
            "shared_keys_count": len(shared_keys),
            "dedup_savings_mb": (l1_stats.total_size + l2_stats.total_size - actual_total_size) / (1024 * 1024),
            "max_memory_mb": self.max_memory_bytes / (1024 * 1024),
            "memory_usage_percent": (actual_total_size / self.max_memory_bytes * 100) if self.max_memory_bytes > 0 else 0,
        }

    async def check_memory_limit(self) -> None:
        """检查并强制清理超出内存限制的缓存"""
        stats = await self.get_stats()
        total_size = stats["l1"].total_size + stats["l2"].total_size

        if total_size > self.max_memory_bytes:
            memory_mb = total_size / (1024 * 1024)
            max_mb = self.max_memory_bytes / (1024 * 1024)
            logger.warning(
                f"缓存内存超限: {memory_mb:.2f}MB / {max_mb:.2f}MB "
                f"({stats['memory_usage_percent']:.1f}%)，开始强制清理L2缓存"
            )
            # 优先清理L2缓存（温数据）
            await self.l2_cache.clear()

            # 如果清理L2后仍超限，清理L1
            stats_after_l2 = await self.get_stats()
            total_after_l2 = stats_after_l2["l1"].total_size + stats_after_l2["l2"].total_size
            if total_after_l2 > self.max_memory_bytes:
                logger.warning("清理L2后仍超限，继续清理L1缓存")
                await self.l1_cache.clear()

            logger.info("缓存强制清理完成")

    async def start_cleanup_task(self, interval: float = 60) -> None:
        """启动定期清理任务

        Args:
            interval: 清理间隔（秒）
        """
        if self._cleanup_task is not None:
            logger.warning("清理任务已在运行")
            return

        async def cleanup_loop():
            while not self._is_closing:
                try:
                    await asyncio.sleep(interval)

                    if self._is_closing:
                        break

                    stats = await self.get_stats()
                    l1_stats = stats["l1"]
                    l2_stats = stats["l2"]
                    logger.info(
                        f"缓存统计 - L1: {l1_stats.item_count}项, "
                        f"命中率{l1_stats.hit_rate:.2%} | "
                        f"L2: {l2_stats.item_count}项, "
                        f"命中率{l2_stats.hit_rate:.2%} | "
                        f"内存: {stats['total_memory_mb']:.2f}MB/{stats['max_memory_mb']:.2f}MB "
                        f"({stats['memory_usage_percent']:.1f}%) | "
                        f"共享: {stats['shared_keys_count']}键/{stats['shared_mb']:.2f}MB "
                        f"(去重节省{stats['dedup_savings_mb']:.2f}MB)"
                    )

                    # 🔧 清理过期条目
                    await self._clean_expired_entries()

                    # 检查内存限制
                    await self.check_memory_limit()

                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"清理任务异常: {e}", exc_info=True)

        self._cleanup_task = asyncio.create_task(cleanup_loop())
        logger.info(f"缓存清理任务已启动，间隔{interval}秒")

    async def stop_cleanup_task(self) -> None:
        """停止清理任务"""
        self._is_closing = True

        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None
            logger.info("缓存清理任务已停止")

    async def _clean_expired_entries(self) -> None:
        """清理过期的缓存条目"""
        try:
            current_time = time.time()

            # 清理 L1 过期条目
            async with self.l1_cache._lock:
                expired_keys = [
                    key for key, entry in self.l1_cache._cache.items()
                    if current_time - entry.created_at > self.l1_cache.ttl
                ]

                for key in expired_keys:
                    entry = self.l1_cache._cache.pop(key, None)
                    if entry:
                        self.l1_cache._stats.evictions += 1
                        self.l1_cache._stats.item_count -= 1
                        self.l1_cache._stats.total_size -= entry.size

            # 清理 L2 过期条目
            async with self.l2_cache._lock:
                expired_keys = [
                    key for key, entry in self.l2_cache._cache.items()
                    if current_time - entry.created_at > self.l2_cache.ttl
                ]

                for key in expired_keys:
                    entry = self.l2_cache._cache.pop(key, None)
                    if entry:
                        self.l2_cache._stats.evictions += 1
                        self.l2_cache._stats.item_count -= 1
                        self.l2_cache._stats.total_size -= entry.size

            if expired_keys:
                logger.debug(f"清理了 {len(expired_keys)} 个过期缓存条目")

        except Exception as e:
            logger.error(f"清理过期条目失败: {e}", exc_info=True)


# 全局缓存实例
_global_cache: MultiLevelCache | None = None
_cache_lock = asyncio.Lock()


async def get_cache() -> MultiLevelCache:
    """获取全局缓存实例（单例）

    从配置文件读取缓存参数，如果配置未加载则使用默认值
    如果配置中禁用了缓存，返回一个最小化的缓存实例（容量为1）
    """
    global _global_cache

    if _global_cache is None:
        async with _cache_lock:
            if _global_cache is None:
                # 尝试从配置读取参数
                try:
                    from src.config.config import global_config

                    db_config = global_config.database

                    # 检查是否启用缓存
                    if not db_config.enable_database_cache:
                        logger.info("数据库缓存已禁用，使用最小化缓存实例")
                        _global_cache = MultiLevelCache(
                            l1_max_size=1,
                            l1_ttl=1,
                            l2_max_size=1,
                            l2_ttl=1,
                            max_memory_mb=1,
                        )
                        return _global_cache

                    l1_max_size = db_config.cache_l1_max_size
                    l1_ttl = db_config.cache_l1_ttl
                    l2_max_size = db_config.cache_l2_max_size
                    l2_ttl = db_config.cache_l2_ttl
                    max_memory_mb = db_config.cache_max_memory_mb
                    max_item_size_mb = db_config.cache_max_item_size_mb
                    cleanup_interval = db_config.cache_cleanup_interval

                    logger.info(
                        f"从配置加载缓存参数: L1({l1_max_size}/{l1_ttl}s), "
                        f"L2({l2_max_size}/{l2_ttl}s), 内存限制({max_memory_mb}MB), "
                        f"单项限制({max_item_size_mb}MB)"
                    )
                except Exception as e:
                    # 配置未加载，使用默认值
                    logger.warning(f"无法从配置加载缓存参数，使用默认值: {e}")
                    l1_max_size = 1000
                    l1_ttl = 60
                    l2_max_size = 10000
                    l2_ttl = 300
                    max_memory_mb = 100
                    max_item_size_mb = 1
                    cleanup_interval = 60

                _global_cache = MultiLevelCache(
                    l1_max_size=l1_max_size,
                    l1_ttl=l1_ttl,
                    l2_max_size=l2_max_size,
                    l2_ttl=l2_ttl,
                    max_memory_mb=max_memory_mb,
                    max_item_size_mb=max_item_size_mb,
                )
                await _global_cache.start_cleanup_task(interval=cleanup_interval)

    return _global_cache


async def close_cache() -> None:
    """关闭全局缓存"""
    global _global_cache

    if _global_cache is not None:
        await _global_cache.stop_cleanup_task()
        await _global_cache.clear()
        _global_cache = None
        logger.info("全局缓存已关闭")
