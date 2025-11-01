"""数据库模块

重构后的数据库模块，提供：
- 核心层：引擎、会话、模型、迁移
- 优化层：缓存、预加载、批处理
- API层：CRUD、查询构建器、业务API
- Utils层：装饰器、监控
- 兼容层：向后兼容的API
"""

# ===== 核心层 =====
from src.common.database.core import (
    Base,
    check_and_migrate_database,
    get_db_session,
    get_engine,
    get_session_factory,
)

# ===== 优化层 =====
from src.common.database.optimization import (
    AdaptiveBatchScheduler,
    DataPreloader,
    MultiLevelCache,
    get_batch_scheduler,
    get_cache,
    get_preloader,
)

# ===== API层 =====
from src.common.database.api import (
    AggregateQuery,
    CRUDBase,
    QueryBuilder,
    # ActionRecords API
    get_recent_actions,
    # ChatStreams API
    get_active_streams,
    # Messages API
    get_chat_history,
    get_message_count,
    # PersonInfo API
    get_or_create_person,
    # LLMUsage API
    get_usage_statistics,
    record_llm_usage,
    # 业务API
    save_message,
    store_action_info,
    update_person_affinity,
)

# ===== Utils层 =====
from src.common.database.utils import (
    cached,
    db_operation,
    get_monitor,
    measure_time,
    print_stats,
    record_cache_hit,
    record_cache_miss,
    record_operation,
    reset_stats,
    retry,
    timeout,
    transactional,
)

# ===== 兼容层（向后兼容旧API）=====
from src.common.database.compatibility import (
    MODEL_MAPPING,
    build_filters,
    db_get,
    db_query,
    db_save,
)

__all__ = [
    # 核心层
    "Base",
    "get_engine",
    "get_session_factory",
    "get_db_session",
    "check_and_migrate_database",
    # 优化层
    "MultiLevelCache",
    "DataPreloader",
    "AdaptiveBatchScheduler",
    "get_cache",
    "get_preloader",
    "get_batch_scheduler",
    # API层 - 基础类
    "CRUDBase",
    "QueryBuilder",
    "AggregateQuery",
    # API层 - 业务API
    "store_action_info",
    "get_recent_actions",
    "get_chat_history",
    "get_message_count",
    "save_message",
    "get_or_create_person",
    "update_person_affinity",
    "get_active_streams",
    "record_llm_usage",
    "get_usage_statistics",
    # Utils层
    "retry",
    "timeout",
    "cached",
    "measure_time",
    "transactional",
    "db_operation",
    "get_monitor",
    "record_operation",
    "record_cache_hit",
    "record_cache_miss",
    "print_stats",
    "reset_stats",
    # 兼容层
    "MODEL_MAPPING",
    "build_filters",
    "db_query",
    "db_save",
    "db_get",
]
