"""数据库配置管理

统一管理数据库连接配置
"""

import os
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import quote_plus

from src.common.logger import get_logger

logger = get_logger("database_config")


@dataclass
class DatabaseConfig:
    """数据库配置"""
    
    # 基础配置
    db_type: str  # "sqlite" 或 "mysql"
    url: str  # 数据库连接URL
    
    # 引擎配置
    engine_kwargs: dict[str, Any]
    
    # SQLite特定配置
    sqlite_path: Optional[str] = None
    
    # MySQL特定配置
    mysql_host: Optional[str] = None
    mysql_port: Optional[int] = None
    mysql_user: Optional[str] = None
    mysql_password: Optional[str] = None
    mysql_database: Optional[str] = None
    mysql_charset: str = "utf8mb4"
    mysql_unix_socket: Optional[str] = None


_database_config: Optional[DatabaseConfig] = None


def get_database_config() -> DatabaseConfig:
    """获取数据库配置
    
    从全局配置中读取数据库设置并构建配置对象
    """
    global _database_config
    
    if _database_config is not None:
        return _database_config
    
    from src.config.config import global_config
    
    config = global_config.database
    
    # 构建数据库URL
    if config.database_type == "mysql":
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
        
        _database_config = DatabaseConfig(
            db_type="mysql",
            url=url,
            engine_kwargs=engine_kwargs,
            mysql_host=config.mysql_host,
            mysql_port=config.mysql_port,
            mysql_user=config.mysql_user,
            mysql_password=config.mysql_password,
            mysql_database=config.mysql_database,
            mysql_charset=config.mysql_charset,
            mysql_unix_socket=config.mysql_unix_socket,
        )
        
        logger.info(
            f"MySQL配置已加载: "
            f"{config.mysql_user}@{config.mysql_host}:{config.mysql_port}/{config.mysql_database}"
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
        
        _database_config = DatabaseConfig(
            db_type="sqlite",
            url=url,
            engine_kwargs=engine_kwargs,
            sqlite_path=db_path,
        )
        
        logger.info(f"SQLite配置已加载: {db_path}")
    
    return _database_config


def reset_database_config():
    """重置数据库配置（用于测试）"""
    global _database_config
    _database_config = None
