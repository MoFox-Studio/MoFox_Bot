"""数据库性能监控

提供数据库操作的性能监控和统计功能
"""

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Optional

from src.common.logger import get_logger

logger = get_logger("database.monitoring")


@dataclass
class OperationMetrics:
    """操作指标"""

    count: int = 0
    total_time: float = 0.0
    min_time: float = float("inf")
    max_time: float = 0.0
    error_count: int = 0
    last_execution_time: Optional[float] = None

    @property
    def avg_time(self) -> float:
        """平均执行时间"""
        return self.total_time / self.count if self.count > 0 else 0.0

    def record_success(self, execution_time: float):
        """记录成功执行"""
        self.count += 1
        self.total_time += execution_time
        self.min_time = min(self.min_time, execution_time)
        self.max_time = max(self.max_time, execution_time)
        self.last_execution_time = time.time()

    def record_error(self):
        """记录错误"""
        self.error_count += 1


@dataclass
class DatabaseMetrics:
    """数据库指标"""

    # 操作统计
    operations: dict[str, OperationMetrics] = field(default_factory=dict)

    # 连接池统计
    connection_acquired: int = 0
    connection_released: int = 0
    connection_errors: int = 0

    # 缓存统计
    cache_hits: int = 0
    cache_misses: int = 0
    cache_sets: int = 0
    cache_invalidations: int = 0

    # 批处理统计
    batch_operations: int = 0
    batch_items_total: int = 0
    batch_avg_size: float = 0.0

    # 预加载统计
    preload_operations: int = 0
    preload_hits: int = 0

    @property
    def cache_hit_rate(self) -> float:
        """缓存命中率"""
        total = self.cache_hits + self.cache_misses
        return self.cache_hits / total if total > 0 else 0.0

    @property
    def error_rate(self) -> float:
        """错误率"""
        total_ops = sum(m.count for m in self.operations.values())
        total_errors = sum(m.error_count for m in self.operations.values())
        return total_errors / total_ops if total_ops > 0 else 0.0

    def get_operation_metrics(self, operation_name: str) -> OperationMetrics:
        """获取操作指标"""
        if operation_name not in self.operations:
            self.operations[operation_name] = OperationMetrics()
        return self.operations[operation_name]


class DatabaseMonitor:
    """数据库监控器
    
    单例模式，收集和报告数据库性能指标
    """

    _instance: Optional["DatabaseMonitor"] = None
    _metrics: DatabaseMetrics

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._metrics = DatabaseMetrics()
        return cls._instance

    def record_operation(
        self,
        operation_name: str,
        execution_time: float,
        success: bool = True,
    ):
        """记录操作"""
        metrics = self._metrics.get_operation_metrics(operation_name)
        if success:
            metrics.record_success(execution_time)
        else:
            metrics.record_error()

    def record_connection_acquired(self):
        """记录连接获取"""
        self._metrics.connection_acquired += 1

    def record_connection_released(self):
        """记录连接释放"""
        self._metrics.connection_released += 1

    def record_connection_error(self):
        """记录连接错误"""
        self._metrics.connection_errors += 1

    def record_cache_hit(self):
        """记录缓存命中"""
        self._metrics.cache_hits += 1

    def record_cache_miss(self):
        """记录缓存未命中"""
        self._metrics.cache_misses += 1

    def record_cache_set(self):
        """记录缓存设置"""
        self._metrics.cache_sets += 1

    def record_cache_invalidation(self):
        """记录缓存失效"""
        self._metrics.cache_invalidations += 1

    def record_batch_operation(self, batch_size: int):
        """记录批处理操作"""
        self._metrics.batch_operations += 1
        self._metrics.batch_items_total += batch_size
        self._metrics.batch_avg_size = (
            self._metrics.batch_items_total / self._metrics.batch_operations
        )

    def record_preload_operation(self, hit: bool = False):
        """记录预加载操作"""
        self._metrics.preload_operations += 1
        if hit:
            self._metrics.preload_hits += 1

    def get_metrics(self) -> DatabaseMetrics:
        """获取指标"""
        return self._metrics

    def get_summary(self) -> dict[str, Any]:
        """获取统计摘要"""
        metrics = self._metrics

        operation_summary = {}
        for op_name, op_metrics in metrics.operations.items():
            operation_summary[op_name] = {
                "count": op_metrics.count,
                "avg_time": f"{op_metrics.avg_time:.3f}s",
                "min_time": f"{op_metrics.min_time:.3f}s",
                "max_time": f"{op_metrics.max_time:.3f}s",
                "error_count": op_metrics.error_count,
            }

        return {
            "operations": operation_summary,
            "connections": {
                "acquired": metrics.connection_acquired,
                "released": metrics.connection_released,
                "errors": metrics.connection_errors,
                "active": metrics.connection_acquired - metrics.connection_released,
            },
            "cache": {
                "hits": metrics.cache_hits,
                "misses": metrics.cache_misses,
                "sets": metrics.cache_sets,
                "invalidations": metrics.cache_invalidations,
                "hit_rate": f"{metrics.cache_hit_rate:.2%}",
            },
            "batch": {
                "operations": metrics.batch_operations,
                "total_items": metrics.batch_items_total,
                "avg_size": f"{metrics.batch_avg_size:.1f}",
            },
            "preload": {
                "operations": metrics.preload_operations,
                "hits": metrics.preload_hits,
                "hit_rate": (
                    f"{metrics.preload_hits / metrics.preload_operations:.2%}"
                    if metrics.preload_operations > 0
                    else "N/A"
                ),
            },
            "overall": {
                "error_rate": f"{metrics.error_rate:.2%}",
            },
        }

    def print_summary(self):
        """打印统计摘要"""
        summary = self.get_summary()

        logger.info("=" * 60)
        logger.info("数据库性能统计")
        logger.info("=" * 60)

        # 操作统计
        if summary["operations"]:
            logger.info("\n操作统计:")
            for op_name, stats in summary["operations"].items():
                logger.info(
                    f"  {op_name}: "
                    f"次数={stats['count']}, "
                    f"平均={stats['avg_time']}, "
                    f"最小={stats['min_time']}, "
                    f"最大={stats['max_time']}, "
                    f"错误={stats['error_count']}"
                )

        # 连接池统计
        logger.info("\n连接池:")
        conn = summary["connections"]
        logger.info(
            f"  获取={conn['acquired']}, "
            f"释放={conn['released']}, "
            f"活跃={conn['active']}, "
            f"错误={conn['errors']}"
        )

        # 缓存统计
        logger.info("\n缓存:")
        cache = summary["cache"]
        logger.info(
            f"  命中={cache['hits']}, "
            f"未命中={cache['misses']}, "
            f"设置={cache['sets']}, "
            f"失效={cache['invalidations']}, "
            f"命中率={cache['hit_rate']}"
        )

        # 批处理统计
        logger.info("\n批处理:")
        batch = summary["batch"]
        logger.info(
            f"  操作={batch['operations']}, "
            f"总项目={batch['total_items']}, "
            f"平均大小={batch['avg_size']}"
        )

        # 预加载统计
        logger.info("\n预加载:")
        preload = summary["preload"]
        logger.info(
            f"  操作={preload['operations']}, "
            f"命中={preload['hits']}, "
            f"命中率={preload['hit_rate']}"
        )

        # 整体统计
        logger.info("\n整体:")
        overall = summary["overall"]
        logger.info(f"  错误率={overall['error_rate']}")

        logger.info("=" * 60)

    def reset(self):
        """重置统计"""
        self._metrics = DatabaseMetrics()
        logger.info("数据库监控统计已重置")


# 全局监控器实例
_monitor: Optional[DatabaseMonitor] = None


def get_monitor() -> DatabaseMonitor:
    """获取监控器实例"""
    global _monitor
    if _monitor is None:
        _monitor = DatabaseMonitor()
    return _monitor


# 便捷函数
def record_operation(operation_name: str, execution_time: float, success: bool = True):
    """记录操作"""
    get_monitor().record_operation(operation_name, execution_time, success)


def record_cache_hit():
    """记录缓存命中"""
    get_monitor().record_cache_hit()


def record_cache_miss():
    """记录缓存未命中"""
    get_monitor().record_cache_miss()


def print_stats():
    """打印统计信息"""
    get_monitor().print_summary()


def reset_stats():
    """重置统计"""
    get_monitor().reset()
