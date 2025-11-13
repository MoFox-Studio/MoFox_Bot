"""数据库核心层

职责：
- 数据库引擎管理
- 会话管理
- 模型定义
- 数据库迁移
"""

from .engine import close_engine, get_engine, get_engine_info
from .migration import check_and_migrate_database, create_all_tables, drop_all_tables
from .models import (
    ActionRecords,
    AntiInjectionStats,
    BanUser,
    Base,
    BotPersonalityInterests,
    CacheEntries,
    ChatStreams,
    Emoji,
    Expression,
    GraphEdges,
    GraphNodes,
    ImageDescriptions,
    Images,
    LLMUsage,
    MaiZoneScheduleStatus,
    Memory,
    Messages,
    MonthlyPlan,
    OnlineTime,
    PermissionNodes,
    PersonInfo,
    Schedule,
    ThinkingLog,
    UserPermissions,
    UserRelationships,
    Videos,
    get_string_field,
)
from .session import get_db_session, get_db_session_direct, get_session_factory, reset_session_factory

__all__ = [
    # Models - Tables (按字母顺序)
    "ActionRecords",
    "AntiInjectionStats",
    "BanUser",
    # Models - Base
    "Base",
    "BotPersonalityInterests",
    "CacheEntries",
    "ChatStreams",
    "Emoji",
    "Expression",
    "GraphEdges",
    "GraphNodes",
    "ImageDescriptions",
    "Images",
    "LLMUsage",
    "MaiZoneScheduleStatus",
    "Memory",
    "Messages",
    "MonthlyPlan",
    "OnlineTime",
    "PermissionNodes",
    "PersonInfo",
    "Schedule",
    "ThinkingLog",
    "UserPermissions",
    "UserRelationships",
    "Videos",
    # Migration
    "check_and_migrate_database",
    "close_engine",
    "create_all_tables",
    "drop_all_tables",
    # Session
    "get_db_session",
    "get_db_session_direct",
    # Engine
    "get_engine",
    "get_engine_info",
    "get_session_factory",
    "get_string_field",
    "reset_session_factory",
]
