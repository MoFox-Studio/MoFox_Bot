"""数据库API模块

提供数据库操作相关功能，采用标准Python包设计模式
使用方式：
    from src.plugin_system.apis import database_api
    records = await database_api.db_query(ActionRecords, query_type="get")
    record = await database_api.db_save(ActionRecords, data={"action_id": "123"})

注意：此模块现在使用SQLAlchemy实现，提供更好的连接管理和错误处理
"""

from src.common.database.compatibility import MODEL_MAPPING, db_get, db_query, db_save, store_action_info

# 保持向后兼容性
__all__ = ["MODEL_MAPPING", "db_get", "db_query", "db_save", "store_action_info"]
