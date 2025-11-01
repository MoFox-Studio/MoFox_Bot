"""数据库模块

重构后的数据库模块，提供：
- 核心层：引擎、会话、模型、迁移
- 优化层：缓存、预加载、批处理
- API层：CRUD、查询构建器、业务API
- Utils层：装饰器、监控
- 兼容层：向后兼容的API
"""

# ===== 核心层 =====
# ===== API层 =====
from src.common.database.api import (
    AggregateQuery,
    CRUDBase,
    QueryBuilder,
    # ChatStreams API
    get_active_streams,
    # Messages API
    get_chat_history,
    get_message_count,
    # PersonInfo API
    get_or_create_person,
    # ActionRecords API
    get_recent_actions,
    # LLMUsage API
    get_usage_statistics,
    record_llm_usage,
    # 业务API
    save_message,
    store_action_info,
    update_person_affinity,
)

# ===== 兼容层（向后兼容旧API）=====
from src.common.database.compatibility import (
    MODEL_MAPPING,
    build_filters,
    db_get,
    db_query,
    db_save,
)
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

__all__ = [
    # 兼容层
    "MODEL_MAPPING",
    "AdaptiveBatchScheduler",
    "AggregateQuery",
    # 核心层
    "Base",
    # API层 - 基础类
    "CRUDBase",
    "DataPreloader",
    # 优化层
    "MultiLevelCache",
    "QueryBuilder",
    "build_filters",
    "cached",
    "check_and_migrate_database",
    "db_get",
    "db_operation",
    "db_query",
    "db_save",
    "get_active_streams",
    "get_batch_scheduler",
    "get_cache",
    "get_chat_history",
    "get_db_session",
    "get_engine",
    "get_message_count",
    "get_monitor",
    "get_or_create_person",
    "get_preloader",
    "get_recent_actions",
    "get_session_factory",
    "get_usage_statistics",
    "measure_time",
    "print_stats",
    "record_cache_hit",
    "record_cache_miss",
    "record_llm_usage",
    "record_operation",
    "reset_stats",
    # Utils层
    "retry",
    "save_message",
    # API层 - 业务API
    "store_action_info",
    "timeout",
    "transactional",
    "update_person_affinity",
]
