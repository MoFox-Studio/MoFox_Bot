"""数据库会话管理

单一职责：提供数据库会话工厂和上下文管理器
"""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.common.logger import get_logger

from .engine import get_engine

logger = get_logger("database.session")

# 全局会话工厂
_session_factory: Optional[async_sessionmaker] = None
_factory_lock: Optional[asyncio.Lock] = None


async def get_session_factory() -> async_sessionmaker:
    """获取会话工厂（单例模式）
    
    Returns:
        async_sessionmaker: SQLAlchemy异步会话工厂
    """
    global _session_factory, _factory_lock
    
    # 快速路径
    if _session_factory is not None:
        return _session_factory
    
    # 延迟创建锁
    if _factory_lock is None:
        _factory_lock = asyncio.Lock()
    
    async with _factory_lock:
        # 双重检查
        if _session_factory is not None:
            return _session_factory
        
        engine = await get_engine()
        _session_factory = async_sessionmaker(
            bind=engine,
            class_=AsyncSession,
            expire_on_commit=False,  # 避免在commit后访问属性时重新查询
        )
        
        logger.debug("会话工厂已创建")
        return _session_factory


@asynccontextmanager
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """获取数据库会话上下文管理器
    
    这是数据库操作的主要入口点，通过连接池管理器提供透明的连接复用。
    
    使用示例:
        async with get_db_session() as session:
            result = await session.execute(select(User))
            users = result.scalars().all()
    
    Yields:
        AsyncSession: SQLAlchemy异步会话对象
    """
    # 延迟导入避免循环依赖
    from ..optimization.connection_pool import get_connection_pool_manager
    
    session_factory = await get_session_factory()
    pool_manager = get_connection_pool_manager()
    
    # 使用连接池管理器（透明复用连接）
    async with pool_manager.get_session(session_factory) as session:
        # 为SQLite设置特定的PRAGMA
        from src.config.config import global_config
        
        if global_config.database.database_type == "sqlite":
            try:
                await session.execute(text("PRAGMA busy_timeout = 60000"))
                await session.execute(text("PRAGMA foreign_keys = ON"))
            except Exception:
                # 复用连接时PRAGMA可能已设置，忽略错误
                pass
        
        yield session


@asynccontextmanager
async def get_db_session_direct() -> AsyncGenerator[AsyncSession, None]:
    """获取数据库会话（直接模式，不使用连接池）
    
    用于特殊场景，如需要完全独立的连接时。
    一般情况下应使用 get_db_session()。
    
    Yields:
        AsyncSession: SQLAlchemy异步会话对象
    """
    session_factory = await get_session_factory()
    
    async with session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def reset_session_factory():
    """重置会话工厂（用于测试）"""
    global _session_factory
    _session_factory = None
