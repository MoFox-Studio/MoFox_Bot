"""SQLAlchemy数据库初始化模块

替换Peewee的数据库初始化逻辑
提供统一的异步数据库初始化接口
"""

from sqlalchemy.exc import SQLAlchemyError

from src.common.database.sqlalchemy_models import Base, get_engine, initialize_database
from src.common.logger import get_logger

logger = get_logger("sqlalchemy_init")


async def initialize_sqlalchemy_database() -> bool:
    """
    初始化SQLAlchemy异步数据库
    创建所有表结构

    Returns:
        bool: 初始化是否成功
    """
    try:
        logger.info("开始初始化SQLAlchemy异步数据库...")

        # 初始化数据库引擎和会话
        engine, session_local = await initialize_database()

        if engine is None:
            logger.error("数据库引擎初始化失败")
            return False

        logger.info("SQLAlchemy异步数据库初始化成功")
        return True

    except SQLAlchemyError as e:
        logger.error(f"SQLAlchemy数据库初始化失败: {e}")
        return False
    except Exception as e:
        logger.error(f"数据库初始化过程中发生未知错误: {e}")
        return False


async def create_all_tables() -> bool:
    """
    异步创建所有数据库表

    Returns:
        bool: 创建是否成功
    """
    try:
        logger.info("开始创建数据库表...")

        engine = await get_engine()
        if engine is None:
            logger.error("无法获取数据库引擎")
            return False

        # 异步创建所有表
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        logger.info("数据库表创建成功")
        return True

    except SQLAlchemyError as e:
        logger.error(f"创建数据库表失败: {e}")
        return False
    except Exception as e:
        logger.error(f"创建数据库表过程中发生未知错误: {e}")
        return False


async def get_database_info() -> dict | None:
    """
    异步获取数据库信息

    Returns:
        dict: 数据库信息字典，包含引擎信息等
    """
    try:
        engine = await get_engine()
        if engine is None:
            return None

        info = {
            "engine_name": engine.name,
            "driver": engine.driver,
            "url": str(engine.url).replace(engine.url.password or "", "***"),  # 隐藏密码
            "pool_size": getattr(engine.pool, "size", None),
            "max_overflow": getattr(engine.pool, "max_overflow", None),
        }

        return info

    except Exception as e:
        logger.error(f"获取数据库信息失败: {e}")
        return None


_database_initialized = False


async def initialize_database_compat() -> bool:
    """
    兼容性异步数据库初始化函数
    用于替换原有的Peewee初始化代码

    Returns:
        bool: 初始化是否成功
    """
    global _database_initialized

    if _database_initialized:
        return True

    success = await initialize_sqlalchemy_database()
    if success:
        success = await create_all_tables()

    if success:
        _database_initialized = True

    return success
