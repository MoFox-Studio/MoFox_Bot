"""兼容层

提供向后兼容的数据库API
"""

from ..core import get_db_session, get_engine
from .adapter import (
    MODEL_MAPPING,
    build_filters,
    db_get,
    db_query,
    db_save,
    store_action_info,
)

__all__ = [
    # 从 core 重新导出的函数
    "get_db_session",
    "get_engine",
    # 兼容层适配器
    "MODEL_MAPPING",
    "build_filters",
    "db_query",
    "db_save",
    "db_get",
    "store_action_info",
]
