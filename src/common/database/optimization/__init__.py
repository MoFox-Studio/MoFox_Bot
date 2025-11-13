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
    Priority,
    close_batch_scheduler,
    get_batch_scheduler,
)
from .cache_manager import (
    CacheEntry,
    CacheStats,
    LRUCache,
    MultiLevelCache,
    close_cache,
    get_cache,
)
from .connection_pool import (
    ConnectionPoolManager,
    get_connection_pool_manager,
    start_connection_pool,
    stop_connection_pool,
)
from .preloader import (
    AccessPattern,
    CommonDataPreloader,
    DataPreloader,
    close_preloader,
    get_preloader,
)

__all__ = [
    "AccessPattern",
    # Batch Scheduler
    "AdaptiveBatchScheduler",
    "BatchOperation",
    "BatchStats",
    "CacheEntry",
    "CacheStats",
    "CommonDataPreloader",
    # Connection Pool
    "ConnectionPoolManager",
    # Preloader
    "DataPreloader",
    "LRUCache",
    # Cache
    "MultiLevelCache",
    "Priority",
    "close_batch_scheduler",
    "close_cache",
    "close_preloader",
    "get_batch_scheduler",
    "get_cache",
    "get_connection_pool_manager",
    "get_preloader",
    "start_connection_pool",
    "stop_connection_pool",
]
