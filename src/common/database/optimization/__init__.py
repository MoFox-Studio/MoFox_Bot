"""数据库优化层

职责：
- 连接池管理
- 批量调度
- 多级缓存
- 数据预加载
"""

from .batch_scheduler import (
    AdaptiveBatchScheduler,
    BatchOperation,
    BatchStats,
    close_batch_scheduler,
    get_batch_scheduler,
    Priority,
)
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
from .preloader import (
    AccessPattern,
    close_preloader,
    CommonDataPreloader,
    DataPreloader,
    get_preloader,
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
    # Preloader
    "DataPreloader",
    "CommonDataPreloader",
    "AccessPattern",
    "get_preloader",
    "close_preloader",
    # Batch Scheduler
    "AdaptiveBatchScheduler",
    "BatchOperation",
    "BatchStats",
    "Priority",
    "get_batch_scheduler",
    "close_batch_scheduler",
]
