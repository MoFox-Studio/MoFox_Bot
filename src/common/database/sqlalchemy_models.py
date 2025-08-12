"""SQLAlchemy数据库模型定义

替换Peewee ORM，使用SQLAlchemy提供更好的连接池管理和错误恢复能力
"""

from sqlalchemy import Column, String, Float, Integer, Boolean, Text, Index, create_engine, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool
import os
import datetime
from src.config.config import global_config
from src.common.logger import get_logger
import threading
from contextlib import contextmanager

logger = get_logger("sqlalchemy_models")

# 创建基类
Base = declarative_base()

# MySQL兼容的字段类型辅助函数
def get_string_field(max_length=255, **kwargs):
    """
    根据数据库类型返回合适的字符串字段
    MySQL需要指定长度的VARCHAR用于索引，SQLite可以使用Text
    """
    if global_config.database.database_type == "mysql":
        return String(max_length, **kwargs)
    else:
        return Text(**kwargs)

class SessionProxy:
    """线程安全的Session代理类，自动管理session生命周期"""

    def __init__(self):
        self._local = threading.local()

    def _get_current_session(self):
        """获取当前线程的session，如果没有则创建新的"""
        if not hasattr(self._local, 'session') or self._local.session is None:
            _, SessionLocal = initialize_database()
            self._local.session = SessionLocal()
        return self._local.session

    def _close_current_session(self):
        """关闭当前线程的session"""
        if hasattr(self._local, 'session') and self._local.session is not None:
            try:
                self._local.session.close()
            except:
                pass
            finally:
                self._local.session = None

    def __getattr__(self, name):
        """代理所有session方法"""
        session = self._get_current_session()
        attr = getattr(session, name)

        # 如果是方法，需要特殊处理一些关键方法
        if callable(attr):
            if name in ['commit', 'rollback']:
                def wrapper(*args, **kwargs):
                    try:
                        result = attr(*args, **kwargs)
                        if name == 'commit':
                            # commit后不要清除session，只是刷新状态
                            pass  # 保持session活跃
                        return result
                    except Exception as e:
                        try:
                            if session and hasattr(session, 'rollback'):
                                session.rollback()
                        except:
                            pass
                        # 发生错误时重新创建session
                        self._close_current_session()
                        raise
                return wrapper
            elif name == 'close':
                def wrapper(*args, **kwargs):
                    result = attr(*args, **kwargs)
                    self._close_current_session()
                    return result
                return wrapper
            elif name in ['execute', 'query', 'add', 'delete', 'merge']:
                def wrapper(*args, **kwargs):
                    try:
                        return attr(*args, **kwargs)
                    except Exception as e:
                        # 如果是连接相关错误，重新创建session再试一次
                        if "not bound to a Session" in str(e) or "provisioning a new connection" in str(e):
                            logger.warning(f"Session问题，重新创建session: {e}")
                            self._close_current_session()
                            new_session = self._get_current_session()
                            new_attr = getattr(new_session, name)
                            return new_attr(*args, **kwargs)
                        raise
                return wrapper

        return attr

    def new_session(self):
        """强制创建新的session（关闭当前的，创建新的）"""
        self._close_current_session()
        return self._get_current_session()

    def ensure_fresh_session(self):
        """确保使用新鲜的session（如果当前session有问题则重新创建）"""
        if hasattr(self._local, 'session') and self._local.session is not None:
            try:
                # 测试session是否还可用
                self._local.session.execute("SELECT 1")
            except Exception:
                # session有问题，重新创建
                self._close_current_session()
        return self._get_current_session()

# 创建全局session代理实例
_global_session_proxy = SessionProxy()

def get_session():
    """返回线程安全的session代理，自动管理生命周期"""
    return _global_session_proxy


class ChatStreams(Base):
    """聊天流模型"""
    __tablename__ = 'chat_streams'

    id = Column(Integer, primary_key=True, autoincrement=True)
    stream_id = Column(get_string_field(64), nullable=False, unique=True, index=True)
    create_time = Column(Float, nullable=False)
    group_platform = Column(Text, nullable=True)
    group_id = Column(get_string_field(100), nullable=True, index=True)
    group_name = Column(Text, nullable=True)
    last_active_time = Column(Float, nullable=False)
    platform = Column(Text, nullable=False)
    user_platform = Column(Text, nullable=False)
    user_id = Column(get_string_field(100), nullable=False, index=True)
    user_nickname = Column(Text, nullable=False)
    user_cardname = Column(Text, nullable=True)

    __table_args__ = (
        Index('idx_chatstreams_stream_id', 'stream_id'),
        Index('idx_chatstreams_user_id', 'user_id'),
        Index('idx_chatstreams_group_id', 'group_id'),
    )


class LLMUsage(Base):
    """LLM使用记录模型"""
    __tablename__ = 'llm_usage'

    id = Column(Integer, primary_key=True, autoincrement=True)
    model_name = Column(get_string_field(100), nullable=False, index=True)
    user_id = Column(get_string_field(50), nullable=False, index=True)
    request_type = Column(get_string_field(50), nullable=False, index=True)
    endpoint = Column(Text, nullable=False)
    prompt_tokens = Column(Integer, nullable=False)
    completion_tokens = Column(Integer, nullable=False)
    total_tokens = Column(Integer, nullable=False)
    cost = Column(Float, nullable=False)
    status = Column(Text, nullable=False)
    timestamp = Column(DateTime, nullable=False, index=True, default=datetime.datetime.now)

    __table_args__ = (
        Index('idx_llmusage_model_name', 'model_name'),
        Index('idx_llmusage_user_id', 'user_id'),
        Index('idx_llmusage_request_type', 'request_type'),
        Index('idx_llmusage_timestamp', 'timestamp'),
    )


class Emoji(Base):
    """表情包模型"""
    __tablename__ = 'emoji'

    id = Column(Integer, primary_key=True, autoincrement=True)
    full_path = Column(get_string_field(500), nullable=False, unique=True, index=True)
    format = Column(Text, nullable=False)
    emoji_hash = Column(get_string_field(64), nullable=False, index=True)
    description = Column(Text, nullable=False)
    query_count = Column(Integer, nullable=False, default=0)
    is_registered = Column(Boolean, nullable=False, default=False)
    is_banned = Column(Boolean, nullable=False, default=False)
    emotion = Column(Text, nullable=True)
    record_time = Column(Float, nullable=False)
    register_time = Column(Float, nullable=True)
    usage_count = Column(Integer, nullable=False, default=0)
    last_used_time = Column(Float, nullable=True)

    __table_args__ = (
        Index('idx_emoji_full_path', 'full_path'),
        Index('idx_emoji_hash', 'emoji_hash'),
    )


class Messages(Base):
    """消息模型"""
    __tablename__ = 'messages'

    id = Column(Integer, primary_key=True, autoincrement=True)
    message_id = Column(get_string_field(100), nullable=False, index=True)
    time = Column(Float, nullable=False)
    chat_id = Column(get_string_field(64), nullable=False, index=True)
    reply_to = Column(Text, nullable=True)
    interest_value = Column(Float, nullable=True)
    is_mentioned = Column(Boolean, nullable=True)

    # 从 chat_info 扁平化而来的字段
    chat_info_stream_id = Column(Text, nullable=False)
    chat_info_platform = Column(Text, nullable=False)
    chat_info_user_platform = Column(Text, nullable=False)
    chat_info_user_id = Column(Text, nullable=False)
    chat_info_user_nickname = Column(Text, nullable=False)
    chat_info_user_cardname = Column(Text, nullable=True)
    chat_info_group_platform = Column(Text, nullable=True)
    chat_info_group_id = Column(Text, nullable=True)
    chat_info_group_name = Column(Text, nullable=True)
    chat_info_create_time = Column(Float, nullable=False)
    chat_info_last_active_time = Column(Float, nullable=False)

    # 从顶层 user_info 扁平化而来的字段
    user_platform = Column(Text, nullable=True)
    user_id = Column(get_string_field(100), nullable=True, index=True)
    user_nickname = Column(Text, nullable=True)
    user_cardname = Column(Text, nullable=True)

    processed_plain_text = Column(Text, nullable=True)
    display_message = Column(Text, nullable=True)
    memorized_times = Column(Integer, nullable=False, default=0)
    priority_mode = Column(Text, nullable=True)
    priority_info = Column(Text, nullable=True)
    additional_config = Column(Text, nullable=True)
    is_emoji = Column(Boolean, nullable=False, default=False)
    is_picid = Column(Boolean, nullable=False, default=False)
    is_command = Column(Boolean, nullable=False, default=False)
    is_notify = Column(Boolean, nullable=False, default=False)

    __table_args__ = (
        Index('idx_messages_message_id', 'message_id'),
        Index('idx_messages_chat_id', 'chat_id'),
        Index('idx_messages_time', 'time'),
        Index('idx_messages_user_id', 'user_id'),
    )


class ActionRecords(Base):
    """动作记录模型"""
    __tablename__ = 'action_records'

    id = Column(Integer, primary_key=True, autoincrement=True)
    action_id = Column(get_string_field(100), nullable=False, index=True)
    time = Column(Float, nullable=False)
    action_name = Column(Text, nullable=False)
    action_data = Column(Text, nullable=False)
    action_done = Column(Boolean, nullable=False, default=False)
    action_build_into_prompt = Column(Boolean, nullable=False, default=False)
    action_prompt_display = Column(Text, nullable=False)
    chat_id = Column(get_string_field(64), nullable=False, index=True)
    chat_info_stream_id = Column(Text, nullable=False)
    chat_info_platform = Column(Text, nullable=False)

    __table_args__ = (
        Index('idx_actionrecords_action_id', 'action_id'),
        Index('idx_actionrecords_chat_id', 'chat_id'),
        Index('idx_actionrecords_time', 'time'),
    )


class Images(Base):
    """图像信息模型"""
    __tablename__ = 'images'

    id = Column(Integer, primary_key=True, autoincrement=True)
    image_id = Column(Text, nullable=False, default="")
    emoji_hash = Column(get_string_field(64), nullable=False, index=True)
    description = Column(Text, nullable=True)
    path = Column(get_string_field(500), nullable=False, unique=True)
    count = Column(Integer, nullable=False, default=1)
    timestamp = Column(Float, nullable=False)
    type = Column(Text, nullable=False)
    vlm_processed = Column(Boolean, nullable=False, default=False)

    __table_args__ = (
        Index('idx_images_emoji_hash', 'emoji_hash'),
        Index('idx_images_path', 'path'),
    )


class ImageDescriptions(Base):
    """图像描述信息模型"""
    __tablename__ = 'image_descriptions'

    id = Column(Integer, primary_key=True, autoincrement=True)
    type = Column(Text, nullable=False)
    image_description_hash = Column(get_string_field(64), nullable=False, index=True)
    description = Column(Text, nullable=False)
    timestamp = Column(Float, nullable=False)

    __table_args__ = (
        Index('idx_imagedesc_hash', 'image_description_hash'),
    )


class OnlineTime(Base):
    """在线时长记录模型"""
    __tablename__ = 'online_time'

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(Text, nullable=False, default=str(datetime.datetime.now))
    duration = Column(Integer, nullable=False)
    start_timestamp = Column(DateTime, nullable=False, default=datetime.datetime.now)
    end_timestamp = Column(DateTime, nullable=False, index=True)

    __table_args__ = (
        Index('idx_onlinetime_end_timestamp', 'end_timestamp'),
    )


class PersonInfo(Base):
    """人物信息模型"""
    __tablename__ = 'person_info'

    id = Column(Integer, primary_key=True, autoincrement=True)
    person_id = Column(get_string_field(100), nullable=False, unique=True, index=True)
    person_name = Column(Text, nullable=True)
    name_reason = Column(Text, nullable=True)
    platform = Column(Text, nullable=False)
    user_id = Column(get_string_field(50), nullable=False, index=True)
    nickname = Column(Text, nullable=True)
    impression = Column(Text, nullable=True)
    short_impression = Column(Text, nullable=True)
    points = Column(Text, nullable=True)
    forgotten_points = Column(Text, nullable=True)
    info_list = Column(Text, nullable=True)
    know_times = Column(Float, nullable=True)
    know_since = Column(Float, nullable=True)
    last_know = Column(Float, nullable=True)
    attitude = Column(Integer, nullable=True, default=50)

    __table_args__ = (
        Index('idx_personinfo_person_id', 'person_id'),
        Index('idx_personinfo_user_id', 'user_id'),
    )


class Memory(Base):
    """记忆模型"""
    __tablename__ = 'memory'

    id = Column(Integer, primary_key=True, autoincrement=True)
    memory_id = Column(get_string_field(64), nullable=False, index=True)
    chat_id = Column(Text, nullable=True)
    memory_text = Column(Text, nullable=True)
    keywords = Column(Text, nullable=True)
    create_time = Column(Float, nullable=True)
    last_view_time = Column(Float, nullable=True)

    __table_args__ = (
        Index('idx_memory_memory_id', 'memory_id'),
    )


class Expression(Base):
    """表达风格模型"""
    __tablename__ = 'expression'

    id = Column(Integer, primary_key=True, autoincrement=True)
    situation = Column(Text, nullable=False)
    style = Column(Text, nullable=False)
    count = Column(Float, nullable=False)
    last_active_time = Column(Float, nullable=False)
    chat_id = Column(get_string_field(64), nullable=False, index=True)
    type = Column(Text, nullable=False)
    create_date = Column(Float, nullable=True)

    __table_args__ = (
        Index('idx_expression_chat_id', 'chat_id'),
    )


class ThinkingLog(Base):
    """思考日志模型"""
    __tablename__ = 'thinking_logs'

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(get_string_field(64), nullable=False, index=True)
    trigger_text = Column(Text, nullable=True)
    response_text = Column(Text, nullable=True)
    trigger_info_json = Column(Text, nullable=True)
    response_info_json = Column(Text, nullable=True)
    timing_results_json = Column(Text, nullable=True)
    chat_history_json = Column(Text, nullable=True)
    chat_history_in_thinking_json = Column(Text, nullable=True)
    chat_history_after_response_json = Column(Text, nullable=True)
    heartflow_data_json = Column(Text, nullable=True)
    reasoning_data_json = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.datetime.now)

    __table_args__ = (
        Index('idx_thinkinglog_chat_id', 'chat_id'),
    )


class GraphNodes(Base):
    """记忆图节点模型"""
    __tablename__ = 'graph_nodes'

    id = Column(Integer, primary_key=True, autoincrement=True)
    concept = Column(get_string_field(255), nullable=False, unique=True, index=True)
    memory_items = Column(Text, nullable=False)
    hash = Column(Text, nullable=False)
    created_time = Column(Float, nullable=False)
    last_modified = Column(Float, nullable=False)

    __table_args__ = (
        Index('idx_graphnodes_concept', 'concept'),
    )


class GraphEdges(Base):
    """记忆图边模型"""
    __tablename__ = 'graph_edges'

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(get_string_field(255), nullable=False, index=True)
    target = Column(get_string_field(255), nullable=False, index=True)
    strength = Column(Integer, nullable=False)
    hash = Column(Text, nullable=False)
    created_time = Column(Float, nullable=False)
    last_modified = Column(Float, nullable=False)

    __table_args__ = (
        Index('idx_graphedges_source', 'source'),
        Index('idx_graphedges_target', 'target'),
    )


class Schedule(Base):
    """日程模型"""
    __tablename__ = 'schedule'

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(get_string_field(10), nullable=False, unique=True, index=True)  # YYYY-MM-DD格式
    schedule_data = Column(Text, nullable=False)  # JSON格式的日程数据
    created_at = Column(DateTime, nullable=False, default=datetime.datetime.now)
    updated_at = Column(DateTime, nullable=False, default=datetime.datetime.now, onupdate=datetime.datetime.now)

    __table_args__ = (
        Index('idx_schedule_date', 'date'),
    )


# 数据库引擎和会话管理
_engine = None
_SessionLocal = None


def get_database_url():
    """获取数据库连接URL"""
    config = global_config.database

    if config.database_type == "mysql":
        # 对用户名和密码进行URL编码，处理特殊字符
        from urllib.parse import quote_plus
        encoded_user = quote_plus(config.mysql_user)
        encoded_password = quote_plus(config.mysql_password)
        
        return (
            f"mysql+pymysql://{encoded_user}:{encoded_password}"
            f"@{config.mysql_host}:{config.mysql_port}/{config.mysql_database}"
            f"?charset={config.mysql_charset}"
        )
    else:  # SQLite
        # 如果是相对路径，则相对于项目根目录
        if not os.path.isabs(config.sqlite_path):
            ROOT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
            db_path = os.path.join(ROOT_PATH, config.sqlite_path)
        else:
            db_path = config.sqlite_path

        # 确保数据库目录存在
        os.makedirs(os.path.dirname(db_path), exist_ok=True)

        return f"sqlite:///{db_path}"


def initialize_database():
    """初始化数据库引擎和会话"""
    global _engine, _SessionLocal

    if _engine is not None:
        return _engine, _SessionLocal

    database_url = get_database_url()
    config = global_config.database

    # 配置引擎参数
    engine_kwargs = {
        'echo': False,  # 生产环境关闭SQL日志
        'future': True,
    }

    if config.database_type == "mysql":
        # MySQL连接池配置
        engine_kwargs.update({
            'poolclass': QueuePool,
            'pool_size': config.connection_pool_size,
            'max_overflow': config.connection_pool_size * 2,
            'pool_timeout': config.connection_timeout,
            'pool_recycle': 3600,  # 1小时回收连接
            'pool_pre_ping': True,  # 连接前ping检查
            'connect_args': {
                'autocommit': config.mysql_autocommit,
                'charset': config.mysql_charset,
                'connect_timeout': config.connection_timeout,
                'read_timeout': 30,
                'write_timeout': 30,
            }
        })
    else:
        # SQLite配置 - 添加连接池设置以避免连接耗尽
        engine_kwargs.update({
            'poolclass': QueuePool,
            'pool_size': 20,  # 增加池大小
            'max_overflow': 30,  # 增加溢出连接数
            'pool_timeout': 60,  # 增加超时时间
            'pool_recycle': 3600,  # 1小时回收连接
            'pool_pre_ping': True,  # 连接前ping检查
            'connect_args': {
                'check_same_thread': False,
                'timeout': 30,
            }
        })

    _engine = create_engine(database_url, **engine_kwargs)
    _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)

    # 创建所有表
    Base.metadata.create_all(bind=_engine)

    logger.info(f"SQLAlchemy数据库初始化成功: {config.database_type}")
    return _engine, _SessionLocal


@contextmanager
def get_db_session():
    """数据库会话上下文管理器 - 推荐使用这个而不是get_session()"""
    session = None
    try:
        _, SessionLocal = initialize_database()
        session = SessionLocal()
        yield session
        session.commit()
    except Exception as e:
        if session:
            session.rollback()
        raise
    finally:
        if session:
            session.close()


def get_engine():
    """获取数据库引擎"""
    engine, _ = initialize_database()
    return engine
