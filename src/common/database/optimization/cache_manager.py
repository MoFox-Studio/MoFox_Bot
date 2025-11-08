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
from typing import Any, Dict, Generic, List, Optional, Set, TypeVar, Union

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
        ttl: Optional[float] = None,
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
        ttl: Optional[float] = None,
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

    async def get_stats(self) -> Dict[str, Any]:
        """获取所有缓存层的统计信息（修复版：避免锁嵌套，使用超时）"""
        # 🔧 修复：并行获取统计信息，避免锁嵌套
        l1_stats_task = asyncio.create_task(self._get_cache_stats_safe(self.l1_cache, "L1"))
        l2_stats_task = asyncio.create_task(self._get_cache_stats_safe(self.l2_cache, "L2"))

        # 使用超时避免死锁
        try:
            l1_stats, l2_stats = await asyncio.gather(
                asyncio.wait_for(l1_stats_task, timeout=1.0),
                asyncio.wait_for(l2_stats_task, timeout=1.0),
                return_exceptions=True
            )
        except asyncio.TimeoutError:
            logger.warning("缓存统计获取超时，使用基本统计")
            l1_stats = await self.l1_cache.get_stats()
            l2_stats = await self.l2_cache.get_stats()

        # 处理异常情况
        if isinstance(l1_stats, Exception):
            logger.error(f"L1统计获取失败: {l1_stats}")
            l1_stats = CacheStats()
        if isinstance(l2_stats, Exception):
            logger.error(f"L2统计获取失败: {l2_stats}")
            l2_stats = CacheStats()

        # 🔧 修复：并行获取键集合，避免锁嵌套
        l1_keys_task = asyncio.create_task(self._get_cache_keys_safe(self.l1_cache))
        l2_keys_task = asyncio.create_task(self._get_cache_keys_safe(self.l2_cache))

        try:
            l1_keys, l2_keys = await asyncio.gather(
                asyncio.wait_for(l1_keys_task, timeout=1.0),
                asyncio.wait_for(l2_keys_task, timeout=1.0),
                return_exceptions=True
            )
        except asyncio.TimeoutError:
            logger.warning("缓存键获取超时，使用默认值")
            l1_keys, l2_keys = set(), set()

        # 处理异常情况
        if isinstance(l1_keys, Exception):
            logger.warning(f"L1键获取失败: {l1_keys}")
            l1_keys = set()
        if isinstance(l2_keys, Exception):
            logger.warning(f"L2键获取失败: {l2_keys}")
            l2_keys = set()

        # 计算共享键和独占键
        shared_keys = l1_keys & l2_keys
        l1_only_keys = l1_keys - l2_keys
        l2_only_keys = l2_keys - l1_keys

        # 🔧 修复：并行计算内存使用，避免锁嵌套
        l1_size_task = asyncio.create_task(self._calculate_memory_usage_safe(self.l1_cache, l1_keys))
        l2_size_task = asyncio.create_task(self._calculate_memory_usage_safe(self.l2_cache, l2_keys))

        try:
            l1_size, l2_size = await asyncio.gather(
                asyncio.wait_for(l1_size_task, timeout=1.0),
                asyncio.wait_for(l2_size_task, timeout=1.0),
                return_exceptions=True
            )
        except asyncio.TimeoutError:
            logger.warning("内存计算超时，使用统计值")
            l1_size, l2_size = l1_stats.total_size, l2_stats.total_size

        # 处理异常情况
        if isinstance(l1_size, Exception):
            logger.warning(f"L1内存计算失败: {l1_size}")
            l1_size = l1_stats.total_size
        if isinstance(l2_size, Exception):
            logger.warning(f"L2内存计算失败: {l2_size}")
            l2_size = l2_stats.total_size

        # 计算实际总内存（避免重复计数）
        actual_total_size = l1_size + l2_size - min(l1_stats.total_size, l2_stats.total_size)

        return {
            "l1": l1_stats,
            "l2": l2_stats,
            "total_memory_mb": actual_total_size / (1024 * 1024),
            "l1_only_mb": l1_size / (1024 * 1024),
            "l2_only_mb": l2_size / (1024 * 1024),
            "shared_mb": min(l1_stats.total_size, l2_stats.total_size) / (1024 * 1024),
            "shared_keys_count": len(shared_keys),
            "dedup_savings_mb": (l1_stats.total_size + l2_stats.total_size - actual_total_size) / (1024 * 1024),
            "max_memory_mb": self.max_memory_bytes / (1024 * 1024),
            "memory_usage_percent": (actual_total_size / self.max_memory_bytes * 100) if self.max_memory_bytes > 0 else 0,
        }

    async def _get_cache_stats_safe(self, cache, cache_name: str) -> CacheStats:
        """安全获取缓存统计信息（带超时）"""
        try:
            return await asyncio.wait_for(cache.get_stats(), timeout=0.5)
        except asyncio.TimeoutError:
            logger.warning(f"{cache_name}统计获取超时")
            return CacheStats()
        except Exception as e:
            logger.error(f"{cache_name}统计获取异常: {e}")
            return CacheStats()

    async def _get_cache_keys_safe(self, cache) -> Set[str]:
        """安全获取缓存键集合（带超时）"""
        try:
            # 快速获取键集合，使用超时避免死锁
            return await asyncio.wait_for(
                self._extract_keys_with_lock(cache),
                timeout=0.5
            )
        except asyncio.TimeoutError:
            logger.warning(f"缓存键获取超时: {cache.name}")
            return set()
        except Exception as e:
            logger.error(f"缓存键获取异常: {e}")
            return set()

    async def _extract_keys_with_lock(self, cache) -> Set[str]:
        """在锁保护下提取键集合"""
        async with cache._lock:
            return set(cache._cache.keys())

    async def _calculate_memory_usage_safe(self, cache, keys: Set[str]) -> int:
        """安全计算内存使用（带超时）"""
        if not keys:
            return 0

        try:
            return await asyncio.wait_for(
                self._calc_memory_with_lock(cache, keys),
                timeout=0.5
            )
        except asyncio.TimeoutError:
            logger.warning(f"内存计算超时: {cache.name}")
            return 0
        except Exception as e:
            logger.error(f"内存计算异常: {e}")
            return 0

    async def _calc_memory_with_lock(self, cache, keys: Set[str]) -> int:
        """在锁保护下计算内存使用"""
        total_size = 0
        async with cache._lock:
            for key in keys:
                entry = cache._cache.get(key)
                if entry:
                    total_size += entry.size
        return total_size

    async def check_memory_limit(self) -> None:
        """检查并强制清理超出内存限制的缓存（修复版：避免嵌套锁）"""
        try:
            # 🔧 修复：使用超时获取统计，避免死锁
            stats = await asyncio.wait_for(self.get_stats(), timeout=2.0)
            total_size = stats["total_memory_mb"] * (1024 * 1024)  # 转换回字节

            if total_size > self.max_memory_bytes:
                memory_mb = total_size / (1024 * 1024)
                max_mb = self.max_memory_bytes / (1024 * 1024)
                logger.warning(
                    f"缓存内存超限: {memory_mb:.2f}MB / {max_mb:.2f}MB "
                    f"({stats['memory_usage_percent']:.1f}%)，开始分阶段清理"
                )

                # 🔧 修复：分阶段清理，每阶段都有超时保护
                cleanup_success = False

                # 阶段1: 清理过期条目
                try:
                    await asyncio.wait_for(self._clean_expired_entries(), timeout=3.0)

                    # 重新检查内存使用
                    stats_after_clean = await asyncio.wait_for(self.get_stats(), timeout=1.0)
                    total_after_clean = stats_after_clean["total_memory_mb"] * (1024 * 1024)

                    if total_after_clean <= self.max_memory_bytes:
                        logger.info("清理过期条目后内存使用正常")
                        cleanup_success = True
                except asyncio.TimeoutError:
                    logger.warning("清理过期条目超时，跳到强制清理")

                # 阶段2: 如果过期清理不够，清理L2缓存
                if not cleanup_success:
                    try:
                        logger.info("开始清理L2缓存")
                        await asyncio.wait_for(self.l2_cache.clear(), timeout=2.0)
                        logger.info("L2缓存清理完成")

                        # 检查L1缓存是否还需要清理
                        stats_after_l2 = await asyncio.wait_for(self.get_stats(), timeout=1.0)
                        total_after_l2 = stats_after_l2["total_memory_mb"] * (1024 * 1024)

                        if total_after_l2 > self.max_memory_bytes:
                            logger.warning("清理L2后仍超限，继续清理L1缓存")
                            await asyncio.wait_for(self.l1_cache.clear(), timeout=2.0)
                            logger.info("L1缓存清理完成")

                    except asyncio.TimeoutError:
                        logger.error("强制清理超时，内存可能仍有问题")
                    except Exception as e:
                        logger.error(f"强制清理失败: {e}")

                logger.info("缓存内存限制检查完成")

        except asyncio.TimeoutError:
            logger.warning("内存限制检查超时，跳过本次检查")
        except Exception as e:
            logger.error(f"内存限制检查失败: {e}", exc_info=True)

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
        """清理过期的缓存条目（修复版：并行清理，避免锁嵌套）"""
        try:
            current_time = time.time()

            # 🔧 修复：并行清理 L1 和 L2，使用超时避免死锁
            async def clean_l1_expired():
                """清理L1过期条目"""
                try:
                    # 使用超时避免长时间持锁
                    await asyncio.wait_for(
                        self._clean_cache_layer_expired(self.l1_cache, current_time, "L1"),
                        timeout=2.0
                    )
                except asyncio.TimeoutError:
                    logger.warning("L1缓存清理超时，跳过本次清理")
                except Exception as e:
                    logger.error(f"L1缓存清理异常: {e}")

            async def clean_l2_expired():
                """清理L2过期条目"""
                try:
                    # 使用超时避免长时间持锁
                    await asyncio.wait_for(
                        self._clean_cache_layer_expired(self.l2_cache, current_time, "L2"),
                        timeout=2.0
                    )
                except asyncio.TimeoutError:
                    logger.warning("L2缓存清理超时，跳过本次清理")
                except Exception as e:
                    logger.error(f"L2缓存清理异常: {e}")

            # 🔧 关键修复：并行执行清理，避免串行等待
            l1_task = asyncio.create_task(clean_l1_expired())
            l2_task = asyncio.create_task(clean_l2_expired())

            # 等待两个清理任务完成（使用return_exceptions避免一个失败影响另一个）
            results = await asyncio.gather(l1_task, l2_task, return_exceptions=True)

            # 检查清理结果
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error(f"缓存清理任务 {'L1' if i == 0 else 'L2'} 失败: {result}")
                else:
                    logger.debug(f"缓存清理任务 {'L1' if i == 0 else 'L2'} 完成")

        except Exception as e:
            logger.error(f"清理过期条目失败: {e}", exc_info=True)

    async def _clean_cache_layer_expired(self, cache_layer, current_time: float, layer_name: str) -> int:
        """清理单个缓存层的过期条目（避免锁嵌套）"""
        expired_keys = []
        cleaned_count = 0

        try:
            # 快速扫描过期键（短暂持锁）
            async with cache_layer._lock:
                expired_keys = [
                    key for key, entry in cache_layer._cache.items()
                    if current_time - entry.created_at > cache_layer.ttl
                ]

            # 分批删除过期键，避免长时间持锁
            batch_size = 50  # 每批处理50个键
            for i in range(0, len(expired_keys), batch_size):
                batch = expired_keys[i:i + batch_size]

                async with cache_layer._lock:
                    for key in batch:
                        entry = cache_layer._cache.pop(key, None)
                        if entry:
                            cache_layer._stats.evictions += 1
                            cache_layer._stats.item_count -= 1
                            cache_layer._stats.total_size -= entry.size
                            cleaned_count += 1

                # 在批次之间短暂让出控制权，避免长时间阻塞
                if i + batch_size < len(expired_keys):
                    await asyncio.sleep(0.001)  # 1ms

            if cleaned_count > 0:
                logger.debug(f"{layer_name}缓存清理完成: {cleaned_count} 个过期条目")

        except Exception as e:
            logger.error(f"{layer_name}缓存层清理失败: {e}")
            raise

        return cleaned_count


# 全局缓存实例
_global_cache: Optional[MultiLevelCache] = None
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
