"""数据库优化层

职责：
- 连接池管理
- 批量调度
- 多级缓存
- 数据预加载
"""

from .cache_manager import (
    CacheEntry,
    CacheStats,
    close_cache,
    get_cache,
    LRUCache,
    MultiLevelCache,
)
from .connection_pool import (
    ConnectionPoolManager,
    get_connection_pool_manager,
    start_connection_pool,
    stop_connection_pool,
)

__all__ = [
    # Connection Pool
    "ConnectionPoolManager",
    "get_connection_pool_manager",
    "start_connection_pool",
    "stop_connection_pool",
    # Cache
    "MultiLevelCache",
    "LRUCache",
    "CacheEntry",
    "CacheStats",
    "get_cache",
    "close_cache",
]
