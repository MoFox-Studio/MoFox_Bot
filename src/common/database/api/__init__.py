"""数据库API层

提供统一的数据库访问接口
"""

# CRUD基础操作
from src.common.database.api.crud import CRUDBase

# 查询构建器
from src.common.database.api.query import AggregateQuery, QueryBuilder

# 业务特定API
from src.common.database.api.specialized import (
    # ActionRecords
    get_recent_actions,
    store_action_info,
    # ChatStreams
    get_active_streams,
    get_or_create_chat_stream,
    # LLMUsage
    get_usage_statistics,
    record_llm_usage,
    # Messages
    get_chat_history,
    get_message_count,
    save_message,
    # PersonInfo
    get_or_create_person,
    update_person_affinity,
    # UserRelationships
    get_user_relationship,
    update_relationship_affinity,
)

__all__ = [
    # 基础类
    "CRUDBase",
    "QueryBuilder",
    "AggregateQuery",
    # ActionRecords API
    "store_action_info",
    "get_recent_actions",
    # Messages API
    "get_chat_history",
    "get_message_count",
    "save_message",
    # PersonInfo API
    "get_or_create_person",
    "update_person_affinity",
    # ChatStreams API
    "get_or_create_chat_stream",
    "get_active_streams",
    # LLMUsage API
    "record_llm_usage",
    "get_usage_statistics",
    # UserRelationships API
    "get_user_relationship",
    "update_relationship_affinity",
]
