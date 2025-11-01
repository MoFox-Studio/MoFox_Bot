"""数据库工具层

职责：
- 异常定义
- 装饰器工具
- 性能监控
"""

from .decorators import (
    cached,
    db_operation,
    generate_cache_key,
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
    # 异常
    "DatabaseError",
    "DatabaseInitializationError",
    "DatabaseConnectionError",
    "DatabaseQueryError",
    "DatabaseTransactionError",
    "DatabaseMigrationError",
    "CacheError",
    "BatchSchedulerError",
    "ConnectionPoolError",
    # 装饰器
    "retry",
    "timeout",
    "cached",
    "measure_time",
    "transactional",
    "db_operation",
    # 监控
    "DatabaseMonitor",
    "get_monitor",
    "record_operation",
    "record_cache_hit",
    "record_cache_miss",
    "print_stats",
    "reset_stats",
]
