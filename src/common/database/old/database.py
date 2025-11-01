import os

from rich.traceback import install

from src.common.database.connection_pool_manager import start_connection_pool, stop_connection_pool

# 数据库批量调度器和连接池
from src.common.database.db_batch_scheduler import get_db_batch_scheduler

# SQLAlchemy相关导入
from src.common.database.sqlalchemy_init import initialize_database_compat
from src.common.database.sqlalchemy_models import get_engine
from src.common.logger import get_logger

install(extra_lines=3)

_sql_engine = None

logger = get_logger("database")


# 兼容性：为了不破坏现有代码，保留db变量但指向SQLAlchemy
class DatabaseProxy:
    """数据库代理类"""

    def __init__(self):
        self._engine = None
        self._session = None

    @staticmethod
    async def initialize(*args, **kwargs):
        """初始化数据库连接"""
        result = await initialize_database_compat()

        # 启动数据库优化系统
        try:
            # 启动数据库批量调度器
            batch_scheduler = get_db_batch_scheduler()
            await batch_scheduler.start()
            logger.info("🚀 数据库批量调度器启动成功")

            # 启动连接池管理器
            await start_connection_pool()
            logger.info("🚀 连接池管理器启动成功")
        except Exception as e:
            logger.error(f"启动数据库优化系统失败: {e}")

        return result


# 创建全局数据库代理实例
db = DatabaseProxy()


async def initialize_sql_database(database_config):
    """
    根据配置初始化SQL数据库连接（SQLAlchemy版本）

    Args:
        database_config: DatabaseConfig对象
    """
    global _sql_engine

    try:
        logger.info("使用SQLAlchemy初始化SQL数据库...")

        # 记录数据库配置信息
        if database_config.database_type == "mysql":
            connection_info = f"{database_config.mysql_user}@{database_config.mysql_host}:{database_config.mysql_port}/{database_config.mysql_database}"
            logger.info("MySQL数据库连接配置:")
            logger.info(f"  连接信息: {connection_info}")
            logger.info(f"  字符集: {database_config.mysql_charset}")
        else:
            ROOT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
            if not os.path.isabs(database_config.sqlite_path):
                db_path = os.path.join(ROOT_PATH, database_config.sqlite_path)
            else:
                db_path = database_config.sqlite_path
            logger.info("SQLite数据库连接配置:")
            logger.info(f"  数据库文件: {db_path}")

        # 使用SQLAlchemy初始化
        success = await initialize_database_compat()
        if success:
            _sql_engine = await get_engine()
            logger.info("SQLAlchemy数据库初始化成功")
        else:
            logger.error("SQLAlchemy数据库初始化失败")

        return _sql_engine

    except Exception as e:
        logger.error(f"初始化SQL数据库失败: {e}")
        return None


async def stop_database():
    """停止数据库相关服务"""
    try:
        # 停止连接池管理器
        await stop_connection_pool()
        logger.info("🛑 连接池管理器已停止")

        # 停止数据库批量调度器
        batch_scheduler = get_db_batch_scheduler()
        await batch_scheduler.stop()
        logger.info("🛑 数据库批量调度器已停止")
    except Exception as e:
        logger.error(f"停止数据库优化系统时出错: {e}")
