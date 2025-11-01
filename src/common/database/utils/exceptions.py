"""数据库异常定义

提供统一的异常体系，便于错误处理和调试
"""


class DatabaseError(Exception):
    """数据库基础异常"""
    pass


class DatabaseInitializationError(DatabaseError):
    """数据库初始化异常"""
    pass


class DatabaseConnectionError(DatabaseError):
    """数据库连接异常"""
    pass


class DatabaseQueryError(DatabaseError):
    """数据库查询异常"""
    pass


class DatabaseTransactionError(DatabaseError):
    """数据库事务异常"""
    pass


class DatabaseMigrationError(DatabaseError):
    """数据库迁移异常"""
    pass


class CacheError(DatabaseError):
    """缓存异常"""
    pass


class BatchSchedulerError(DatabaseError):
    """批量调度器异常"""
    pass


class ConnectionPoolError(DatabaseError):
    """连接池异常"""
    pass
