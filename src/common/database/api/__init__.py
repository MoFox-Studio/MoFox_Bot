"""数据库API层

提供统一的数据库访问接口
"""

# CRUD基础操作
from src.common.database.api.crud import CRUDBase

# 查询构建器
from src.common.database.api.query import AggregateQuery, QueryBuilder

# 业务特定API
from src.common.database.api.specialized import (
    # ChatStreams
    get_active_streams,
    # Messages
    get_chat_history,
    get_message_count,
    get_or_create_chat_stream,
    # PersonInfo
    get_or_create_person,
    # ActionRecords
    get_recent_actions,
    # LLMUsage
    get_usage_statistics,
    # UserRelationships
    get_user_relationship,
    record_llm_usage,
    save_message,
    store_action_info,
    update_person_affinity,
    update_relationship_affinity,
)

__all__ = [
    "AggregateQuery",
    # 基础类
    "CRUDBase",
    "QueryBuilder",
    "get_active_streams",
    # Messages API
    "get_chat_history",
    "get_message_count",
    # ChatStreams API
    "get_or_create_chat_stream",
    # PersonInfo API
    "get_or_create_person",
    "get_recent_actions",
    "get_usage_statistics",
    # UserRelationships API
    "get_user_relationship",
    # LLMUsage API
    "record_llm_usage",
    "save_message",
    # ActionRecords API
    "store_action_info",
    "update_person_affinity",
    "update_relationship_affinity",
]
