"""数据库引擎管理

单一职责：创建和管理SQLAlchemy异步引擎
"""

import asyncio
import os
from typing import Optional
from urllib.parse import quote_plus

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.common.logger import get_logger

from ..utils.exceptions import DatabaseInitializationError

logger = get_logger("database.engine")

# 全局引擎实例
_engine: Optional[AsyncEngine] = None
_engine_lock: Optional[asyncio.Lock] = None


async def get_engine() -> AsyncEngine:
    """获取全局数据库引擎（单例模式）
    
    Returns:
        AsyncEngine: SQLAlchemy异步引擎
        
    Raises:
        DatabaseInitializationError: 引擎初始化失败
    """
    global _engine, _engine_lock
    
    # 快速路径：引擎已初始化
    if _engine is not None:
        return _engine
    
    # 延迟创建锁（避免在导入时创建）
    if _engine_lock is None:
        _engine_lock = asyncio.Lock()
    
    # 使用锁保护初始化过程
    async with _engine_lock:
        # 双重检查锁定模式
        if _engine is not None:
            return _engine
        
        try:
            from src.config.config import global_config
            
            config = global_config.database
            db_type = config.database_type
            
            logger.info(f"正在初始化 {db_type.upper()} 数据库引擎...")
            
            # 构建数据库URL和引擎参数
            if db_type == "mysql":
                # MySQL配置
                encoded_user = quote_plus(config.mysql_user)
                encoded_password = quote_plus(config.mysql_password)
                
                if config.mysql_unix_socket:
                    # Unix socket连接
                    encoded_socket = quote_plus(config.mysql_unix_socket)
                    url = (
                        f"mysql+aiomysql://{encoded_user}:{encoded_password}"
                        f"@/{config.mysql_database}"
                        f"?unix_socket={encoded_socket}&charset={config.mysql_charset}"
                    )
                else:
                    # TCP连接
                    url = (
                        f"mysql+aiomysql://{encoded_user}:{encoded_password}"
                        f"@{config.mysql_host}:{config.mysql_port}/{config.mysql_database}"
                        f"?charset={config.mysql_charset}"
                    )
                
                engine_kwargs = {
                    "echo": False,
                    "future": True,
                    "pool_size": config.connection_pool_size,
                    "max_overflow": config.connection_pool_size * 2,
                    "pool_timeout": config.connection_timeout,
                    "pool_recycle": 3600,
                    "pool_pre_ping": True,
                    "connect_args": {
                        "autocommit": config.mysql_autocommit,
                        "charset": config.mysql_charset,
                        "connect_timeout": config.connection_timeout,
                    },
                }
                
                logger.info(
                    f"MySQL配置: {config.mysql_user}@{config.mysql_host}:{config.mysql_port}/{config.mysql_database}"
                )
            
            else:
                # SQLite配置
                if not os.path.isabs(config.sqlite_path):
                    ROOT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
                    db_path = os.path.join(ROOT_PATH, config.sqlite_path)
                else:
                    db_path = config.sqlite_path
                
                # 确保数据库目录存在
                os.makedirs(os.path.dirname(db_path), exist_ok=True)
                
                url = f"sqlite+aiosqlite:///{db_path}"
                
                engine_kwargs = {
                    "echo": False,
                    "future": True,
                    "connect_args": {
                        "check_same_thread": False,
                        "timeout": 60,
                    },
                }
                
                logger.info(f"SQLite配置: {db_path}")
            
            # 创建异步引擎
            _engine = create_async_engine(url, **engine_kwargs)
            
            # SQLite特定优化
            if db_type == "sqlite":
                await _enable_sqlite_optimizations(_engine)
            
            logger.info(f"✅ {db_type.upper()} 数据库引擎初始化成功")
            return _engine
        
        except Exception as e:
            logger.error(f"❌ 数据库引擎初始化失败: {e}", exc_info=True)
            raise DatabaseInitializationError(f"引擎初始化失败: {e}") from e


async def close_engine():
    """关闭数据库引擎
    
    释放所有连接池资源
    """
    global _engine
    
    if _engine is not None:
        logger.info("正在关闭数据库引擎...")
        await _engine.dispose()
        _engine = None
        logger.info("✅ 数据库引擎已关闭")


async def _enable_sqlite_optimizations(engine: AsyncEngine):
    """启用SQLite性能优化
    
    优化项：
    - WAL模式：提高并发性能
    - NORMAL同步：平衡性能和安全性
    - 启用外键约束
    - 设置busy_timeout：避免锁定错误
    
    Args:
        engine: SQLAlchemy异步引擎
    """
    try:
        async with engine.begin() as conn:
            # 启用WAL模式
            await conn.execute(text("PRAGMA journal_mode = WAL"))
            # 设置适中的同步级别
            await conn.execute(text("PRAGMA synchronous = NORMAL"))
            # 启用外键约束
            await conn.execute(text("PRAGMA foreign_keys = ON"))
            # 设置busy_timeout，避免锁定错误
            await conn.execute(text("PRAGMA busy_timeout = 60000"))
            # 设置缓存大小（10MB）
            await conn.execute(text("PRAGMA cache_size = -10000"))
            # 临时存储使用内存
            await conn.execute(text("PRAGMA temp_store = MEMORY"))
        
        logger.info("✅ SQLite性能优化已启用 (WAL模式 + 并发优化)")
    
    except Exception as e:
        logger.warning(f"⚠️ SQLite性能优化失败: {e}，将使用默认配置")


async def get_engine_info() -> dict:
    """获取引擎信息（用于监控和调试）
    
    Returns:
        dict: 引擎信息字典
    """
    try:
        engine = await get_engine()
        
        info = {
            "name": engine.name,
            "driver": engine.driver,
            "url": str(engine.url).replace(str(engine.url.password or ""), "***"),
            "pool_size": getattr(engine.pool, "size", lambda: None)(),
            "pool_checked_out": getattr(engine.pool, "checked_out", lambda: 0)(),
            "pool_overflow": getattr(engine.pool, "overflow", lambda: 0)(),
        }
        
        return info
    
    except Exception as e:
        logger.error(f"获取引擎信息失败: {e}")
        return {}
