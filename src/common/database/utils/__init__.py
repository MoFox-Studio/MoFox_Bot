"""数据库工具层

职责：
- 异常定义
- 装饰器工具
- 性能监控
"""

from .decorators import (
    cached,
    db_operation,
    measure_time,
    retry,
    timeout,
    transactional,
)
from .exceptions import (
    BatchSchedulerError,
    CacheError,
    ConnectionPoolError,
    DatabaseConnectionError,
    DatabaseError,
    DatabaseInitializationError,
    DatabaseMigrationError,
    DatabaseQueryError,
    DatabaseTransactionError,
)
from .monitoring import (
    DatabaseMonitor,
    get_monitor,
    print_stats,
    record_cache_hit,
    record_cache_miss,
    record_operation,
    reset_stats,
)

__all__ = [
    "BatchSchedulerError",
    "CacheError",
    "ConnectionPoolError",
    "DatabaseConnectionError",
    # 异常
    "DatabaseError",
    "DatabaseInitializationError",
    "DatabaseMigrationError",
    # 监控
    "DatabaseMonitor",
    "DatabaseQueryError",
    "DatabaseTransactionError",
    "cached",
    "db_operation",
    "get_monitor",
    "measure_time",
    "print_stats",
    "record_cache_hit",
    "record_cache_miss",
    "record_operation",
    "reset_stats",
    # 装饰器
    "retry",
    "timeout",
    "transactional",
]
