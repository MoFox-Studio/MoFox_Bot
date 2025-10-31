"""SQLAlchemy数据库模型定义

替换Peewee ORM，使用SQLAlchemy提供更好的连接池管理和错误恢复能力

说明: 部分旧模型仍使用 `Column = Column(Type, ...)` 的经典风格。本文件开始逐步迁移到
SQLAlchemy 2.0 推荐的带类型注解的声明式风格：

    field_name: Mapped[PyType] = mapped_column(Type, ...)

这样 IDE / Pylance 能正确推断实例属性的真实 Python 类型，避免将其视为不可赋值的 Column 对象。
当前仅对产生类型检查问题的模型 (BanUser) 进行了迁移，其余模型保持不变以减少一次性改动范围。
"""

import datetime
import os
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Mapped, mapped_column

from src.common.database.connection_pool_manager import get_connection_pool_manager
from src.common.logger import get_logger

logger = get_logger("sqlalchemy_models")

# 创建基类
Base = declarative_base()

# 全局异步引擎与会话工厂占位（延迟初始化）
_engine: AsyncEngine | None = None
_SessionLocal: async_sessionmaker[AsyncSession] | None = None


async def enable_sqlite_wal_mode(engine):
    """为 SQLite 启用 WAL 模式以提高并发性能"""
    try:
        async with engine.begin() as conn:
            # 启用 WAL 模式
            await conn.execute(text("PRAGMA journal_mode = WAL"))
            # 设置适中的同步级别，平衡性能和安全性
            await conn.execute(text("PRAGMA synchronous = NORMAL"))
            # 启用外键约束
            await conn.execute(text("PRAGMA foreign_keys = ON"))
            # 设置 busy_timeout，避免锁定错误
            await conn.execute(text("PRAGMA busy_timeout = 60000"))  # 60秒

        logger.info("[SQLite] WAL 模式已启用，并发性能已优化")
    except Exception as e:
        logger.warning(f"[SQLite] 启用 WAL 模式失败: {e}，将使用默认配置")


async def maintain_sqlite_database():
    """定期维护 SQLite 数据库性能"""
    try:
        engine, SessionLocal = await initialize_database()
        if not engine:
            return

        async with engine.begin() as conn:
            # 检查并确保 WAL 模式仍然启用
            result = await conn.execute(text("PRAGMA journal_mode"))
            journal_mode = result.scalar()

            if journal_mode != "wal":
                await conn.execute(text("PRAGMA journal_mode = WAL"))
                logger.info("[SQLite] WAL 模式已重新启用")

            # 优化数据库性能
            await conn.execute(text("PRAGMA synchronous = NORMAL"))
            await conn.execute(text("PRAGMA busy_timeout = 60000"))
            await conn.execute(text("PRAGMA foreign_keys = ON"))

            # 定期清理（可选，根据需要启用）
            # await conn.execute(text("PRAGMA optimize"))

        logger.info("[SQLite] 数据库维护完成")
    except Exception as e:
        logger.warning(f"[SQLite] 数据库维护失败: {e}")


def get_sqlite_performance_config():
    """获取 SQLite 性能优化配置"""
    return {
        "journal_mode": "WAL",  # 提高并发性能
        "synchronous": "NORMAL",  # 平衡性能和安全性
        "busy_timeout": 60000,  # 60秒超时
        "foreign_keys": "ON",  # 启用外键约束
        "cache_size": -10000,  # 10MB 缓存
        "temp_store": "MEMORY",  # 临时存储使用内存
        "mmap_size": 268435456,  # 256MB 内存映射
    }


# MySQL兼容的字段类型辅助函数
def get_string_field(max_length=255, **kwargs):
    """
    根据数据库类型返回合适的字符串字段
    MySQL需要指定长度的VARCHAR用于索引，SQLite可以使用Text
    """
    from src.config.config import global_config

    if global_config.database.database_type == "mysql":
        return String(max_length, **kwargs)
    else:
        return Text(**kwargs)


class ChatStreams(Base):
    """聊天流模型"""

    __tablename__ = "chat_streams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stream_id: Mapped[str] = mapped_column(get_string_field(64), nullable=False, unique=True, index=True)
    create_time: Mapped[float] = mapped_column(Float, nullable=False)
    group_platform: Mapped[str | None] = mapped_column(Text, nullable=True)
    group_id: Mapped[str | None] = mapped_column(get_string_field(100), nullable=True, index=True)
    group_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_active_time: Mapped[float] = mapped_column(Float, nullable=False)
    platform: Mapped[str] = mapped_column(Text, nullable=False)
    user_platform: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[str] = mapped_column(get_string_field(100), nullable=False, index=True)
    user_nickname: Mapped[str] = mapped_column(Text, nullable=False)
    user_cardname: Mapped[str | None] = mapped_column(Text, nullable=True)
    energy_value: Mapped[float | None] = mapped_column(Float, nullable=True, default=5.0)
    sleep_pressure: Mapped[float | None] = mapped_column(Float, nullable=True, default=0.0)
    focus_energy: Mapped[float | None] = mapped_column(Float, nullable=True, default=0.5)
    # 动态兴趣度系统字段
    base_interest_energy: Mapped[float | None] = mapped_column(Float, nullable=True, default=0.5)
    message_interest_total: Mapped[float | None] = mapped_column(Float, nullable=True, default=0.0)
    message_count: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)
    action_count: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)
    reply_count: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)
    last_interaction_time: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    consecutive_no_reply: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)
    # 消息打断系统字段
    interruption_count: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)
    # 聊天流印象字段
    stream_impression_text: Mapped[str | None] = mapped_column(Text, nullable=True)  # 对聊天流的主观印象描述
    stream_chat_style: Mapped[str | None] = mapped_column(Text, nullable=True)  # 聊天流的总体风格
    stream_topic_keywords: Mapped[str | None] = mapped_column(Text, nullable=True)  # 话题关键词，逗号分隔
    stream_interest_score: Mapped[float | None] = mapped_column(Float, nullable=True, default=0.5)  # 对聊天流的兴趣程度(0-1)

    __table_args__ = (
        Index("idx_chatstreams_stream_id", "stream_id"),
        Index("idx_chatstreams_user_id", "user_id"),
        Index("idx_chatstreams_group_id", "group_id"),
    )


class LLMUsage(Base):
    """LLM使用记录模型"""

    __tablename__ = "llm_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_name: Mapped[str] = mapped_column(get_string_field(100), nullable=False, index=True)
    model_assign_name: Mapped[str] = mapped_column(get_string_field(100), index=True)
    model_api_provider: Mapped[str] = mapped_column(get_string_field(100), index=True)
    user_id: Mapped[str] = mapped_column(get_string_field(50), nullable=False, index=True)
    request_type: Mapped[str] = mapped_column(get_string_field(50), nullable=False, index=True)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    time_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    cost: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, index=True, default=datetime.datetime.now)

    __table_args__ = (
        Index("idx_llmusage_model_name", "model_name"),
        Index("idx_llmusage_model_assign_name", "model_assign_name"),
        Index("idx_llmusage_model_api_provider", "model_api_provider"),
        Index("idx_llmusage_time_cost", "time_cost"),
        Index("idx_llmusage_user_id", "user_id"),
        Index("idx_llmusage_request_type", "request_type"),
        Index("idx_llmusage_timestamp", "timestamp"),
    )


class Emoji(Base):
    """表情包模型"""

    __tablename__ = "emoji"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    full_path: Mapped[str] = mapped_column(get_string_field(500), nullable=False, unique=True, index=True)
    format: Mapped[str] = mapped_column(Text, nullable=False)
    emoji_hash: Mapped[str] = mapped_column(get_string_field(64), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    query_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_registered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_banned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    emotion: Mapped[str | None] = mapped_column(Text, nullable=True)
    record_time: Mapped[float] = mapped_column(Float, nullable=False)
    register_time: Mapped[float | None] = mapped_column(Float, nullable=True)
    usage_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_used_time: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (
        Index("idx_emoji_full_path", "full_path"),
        Index("idx_emoji_hash", "emoji_hash"),
    )


class Messages(Base):
    """消息模型"""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[str] = mapped_column(get_string_field(100), nullable=False, index=True)
    time: Mapped[float] = mapped_column(Float, nullable=False)
    chat_id: Mapped[str] = mapped_column(get_string_field(64), nullable=False, index=True)
    reply_to: Mapped[str | None] = mapped_column(Text, nullable=True)
    interest_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    key_words: Mapped[str | None] = mapped_column(Text, nullable=True)
    key_words_lite: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_mentioned: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # 从 chat_info 扁平化而来的字段
    chat_info_stream_id: Mapped[str] = mapped_column(Text, nullable=False)
    chat_info_platform: Mapped[str] = mapped_column(Text, nullable=False)
    chat_info_user_platform: Mapped[str] = mapped_column(Text, nullable=False)
    chat_info_user_id: Mapped[str] = mapped_column(Text, nullable=False)
    chat_info_user_nickname: Mapped[str] = mapped_column(Text, nullable=False)
    chat_info_user_cardname: Mapped[str | None] = mapped_column(Text, nullable=True)
    chat_info_group_platform: Mapped[str | None] = mapped_column(Text, nullable=True)
    chat_info_group_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    chat_info_group_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    chat_info_create_time: Mapped[float] = mapped_column(Float, nullable=False)
    chat_info_last_active_time: Mapped[float] = mapped_column(Float, nullable=False)

    # 从顶层 user_info 扁平化而来的字段
    user_platform: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_id: Mapped[str | None] = mapped_column(get_string_field(100), nullable=True, index=True)
    user_nickname: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_cardname: Mapped[str | None] = mapped_column(Text, nullable=True)

    processed_plain_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    memorized_times: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    priority_mode: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority_info: Mapped[str | None] = mapped_column(Text, nullable=True)
    additional_config: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_emoji: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_picid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_command: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_notify: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_public_notice: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notice_type: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # 兴趣度系统字段
    actions: Mapped[str | None] = mapped_column(Text, nullable=True)
    should_reply: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=False)
    should_act: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=False)

    __table_args__ = (
        Index("idx_messages_message_id", "message_id"),
        Index("idx_messages_chat_id", "chat_id"),
        Index("idx_messages_time", "time"),
        Index("idx_messages_user_id", "user_id"),
        Index("idx_messages_should_reply", "should_reply"),
        Index("idx_messages_should_act", "should_act"),
    )


class ActionRecords(Base):
    """动作记录模型"""

    __tablename__ = "action_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action_id: Mapped[str] = mapped_column(get_string_field(100), nullable=False, index=True)
    time: Mapped[float] = mapped_column(Float, nullable=False)
    action_name: Mapped[str] = mapped_column(Text, nullable=False)
    action_data: Mapped[str] = mapped_column(Text, nullable=False)
    action_done: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    action_build_into_prompt: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    action_prompt_display: Mapped[str] = mapped_column(Text, nullable=False)
    chat_id: Mapped[str] = mapped_column(get_string_field(64), nullable=False, index=True)
    chat_info_stream_id: Mapped[str] = mapped_column(Text, nullable=False)
    chat_info_platform: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        Index("idx_actionrecords_action_id", "action_id"),
        Index("idx_actionrecords_chat_id", "chat_id"),
        Index("idx_actionrecords_time", "time"),
    )


class Images(Base):
    """图像信息模型"""

    __tablename__ = "images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    image_id: Mapped[str] = mapped_column(Text, nullable=False, default="")
    emoji_hash: Mapped[str] = mapped_column(get_string_field(64), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    path: Mapped[str] = mapped_column(get_string_field(500), nullable=False, unique=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    timestamp: Mapped[float] = mapped_column(Float, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    vlm_processed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        Index("idx_images_emoji_hash", "emoji_hash"),
        Index("idx_images_path", "path"),
    )


class ImageDescriptions(Base):
    """图像描述信息模型"""

    __tablename__ = "image_descriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    image_description_hash: Mapped[str] = mapped_column(get_string_field(64), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[float] = mapped_column(Float, nullable=False)

    __table_args__ = (Index("idx_imagedesc_hash", "image_description_hash"),)


class Videos(Base):
    """视频信息模型"""

    __tablename__ = "videos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[str] = mapped_column(Text, nullable=False, default="")
    video_hash: Mapped[str] = mapped_column(get_string_field(64), nullable=False, index=True, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    timestamp: Mapped[float] = mapped_column(Float, nullable=False)
    vlm_processed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # 视频特有属性
    duration: Mapped[float | None] = mapped_column(Float, nullable=True)
    frame_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fps: Mapped[float | None] = mapped_column(Float, nullable=True)
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        Index("idx_videos_video_hash", "video_hash"),
        Index("idx_videos_timestamp", "timestamp"),
    )


class OnlineTime(Base):
    """在线时长记录模型"""

    __tablename__ = "online_time"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[str] = mapped_column(Text, nullable=False, default=str(datetime.datetime.now))
    duration: Mapped[int] = mapped_column(Integer, nullable=False)
    start_timestamp: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, default=datetime.datetime.now)
    end_timestamp: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, index=True)

    __table_args__ = (Index("idx_onlinetime_end_timestamp", "end_timestamp"),)


class PersonInfo(Base):
    """人物信息模型"""

    __tablename__ = "person_info"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    person_id: Mapped[str] = mapped_column(get_string_field(100), nullable=False, unique=True, index=True)
    person_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    name_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    platform: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[str] = mapped_column(get_string_field(50), nullable=False, index=True)
    nickname: Mapped[str | None] = mapped_column(Text, nullable=True)
    impression: Mapped[str | None] = mapped_column(Text, nullable=True)
    short_impression: Mapped[str | None] = mapped_column(Text, nullable=True)
    points: Mapped[str | None] = mapped_column(Text, nullable=True)
    forgotten_points: Mapped[str | None] = mapped_column(Text, nullable=True)
    info_list: Mapped[str | None] = mapped_column(Text, nullable=True)
    know_times: Mapped[float | None] = mapped_column(Float, nullable=True)
    know_since: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_know: Mapped[float | None] = mapped_column(Float, nullable=True)
    attitude: Mapped[int | None] = mapped_column(Integer, nullable=True, default=50)

    __table_args__ = (
        Index("idx_personinfo_person_id", "person_id"),
        Index("idx_personinfo_user_id", "user_id"),
    )


class BotPersonalityInterests(Base):
    """机器人人格兴趣标签模型"""

    __tablename__ = "bot_personality_interests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    personality_id: Mapped[str] = mapped_column(get_string_field(100), nullable=False, index=True)
    personality_description: Mapped[str] = mapped_column(Text, nullable=False)
    interest_tags: Mapped[str] = mapped_column(Text, nullable=False)
    embedding_model: Mapped[str] = mapped_column(get_string_field(100), nullable=False, default="text-embedding-ada-002")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_updated: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, default=datetime.datetime.now, index=True)

    __table_args__ = (
        Index("idx_botpersonality_personality_id", "personality_id"),
        Index("idx_botpersonality_version", "version"),
        Index("idx_botpersonality_last_updated", "last_updated"),
    )


class Memory(Base):
    """记忆模型"""

    __tablename__ = "memory"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    memory_id: Mapped[str] = mapped_column(get_string_field(64), nullable=False, index=True)
    chat_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    memory_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    keywords: Mapped[str | None] = mapped_column(Text, nullable=True)
    create_time: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_view_time: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (Index("idx_memory_memory_id", "memory_id"),)


class Expression(Base):
    """表达风格模型"""

    __tablename__ = "expression"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    situation: Mapped[str] = mapped_column(Text, nullable=False)
    style: Mapped[str] = mapped_column(Text, nullable=False)
    count: Mapped[float] = mapped_column(Float, nullable=False)
    last_active_time: Mapped[float] = mapped_column(Float, nullable=False)
    chat_id: Mapped[str] = mapped_column(get_string_field(64), nullable=False, index=True)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    create_date: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (Index("idx_expression_chat_id", "chat_id"),)


class ThinkingLog(Base):
    """思考日志模型"""

    __tablename__ = "thinking_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[str] = mapped_column(get_string_field(64), nullable=False, index=True)
    trigger_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    trigger_info_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_info_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    timing_results_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    chat_history_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    chat_history_in_thinking_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    chat_history_after_response_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    heartflow_data_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    reasoning_data_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, default=datetime.datetime.now)

    __table_args__ = (Index("idx_thinkinglog_chat_id", "chat_id"),)


class GraphNodes(Base):
    """记忆图节点模型"""

    __tablename__ = "graph_nodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    concept: Mapped[str] = mapped_column(get_string_field(255), nullable=False, unique=True, index=True)
    memory_items: Mapped[str] = mapped_column(Text, nullable=False)
    hash: Mapped[str] = mapped_column(Text, nullable=False)
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    created_time: Mapped[float] = mapped_column(Float, nullable=False)
    last_modified: Mapped[float] = mapped_column(Float, nullable=False)

    __table_args__ = (Index("idx_graphnodes_concept", "concept"),)


class GraphEdges(Base):
    """记忆图边模型"""

    __tablename__ = "graph_edges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(get_string_field(255), nullable=False, index=True)
    target: Mapped[str] = mapped_column(get_string_field(255), nullable=False, index=True)
    strength: Mapped[int] = mapped_column(Integer, nullable=False)
    hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_time: Mapped[float] = mapped_column(Float, nullable=False)
    last_modified: Mapped[float] = mapped_column(Float, nullable=False)

    __table_args__ = (
        Index("idx_graphedges_source", "source"),
        Index("idx_graphedges_target", "target"),
    )


class Schedule(Base):
    """日程模型"""

    __tablename__ = "schedule"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[str] = mapped_column(get_string_field(10), nullable=False, unique=True, index=True)
    schedule_data: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, default=datetime.datetime.now)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, default=datetime.datetime.now, onupdate=datetime.datetime.now)

    __table_args__ = (Index("idx_schedule_date", "date"),)


class MaiZoneScheduleStatus(Base):
    """麦麦空间日程处理状态模型"""

    __tablename__ = "maizone_schedule_status"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    datetime_hour: Mapped[str] = mapped_column(get_string_field(13), nullable=False, unique=True, index=True)
    activity: Mapped[str] = mapped_column(Text, nullable=False)
    is_processed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    processed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    story_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    send_success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, default=datetime.datetime.now)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, default=datetime.datetime.now, onupdate=datetime.datetime.now)

    __table_args__ = (
        Index("idx_maizone_datetime_hour", "datetime_hour"),
        Index("idx_maizone_is_processed", "is_processed"),
    )


class BanUser(Base):
    """被禁用用户模型

    使用 SQLAlchemy 2.0 类型标注写法，方便静态类型检查器识别实际字段类型，
    避免在业务代码中对属性赋值时报 `Column[...]` 不可赋值的告警。
    """

    __tablename__ = "ban_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[str] = mapped_column(get_string_field(50), nullable=False, index=True)
    violation_num: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, default=datetime.datetime.now)

    __table_args__ = (
        Index("idx_violation_num", "violation_num"),
        Index("idx_banuser_user_id", "user_id"),
        Index("idx_banuser_platform", "platform"),
        Index("idx_banuser_platform_user_id", "platform", "user_id"),
    )


class AntiInjectionStats(Base):
    """反注入系统统计模型"""

    __tablename__ = "anti_injection_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    total_messages: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    """总处理消息数"""

    detected_injections: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    """检测到的注入攻击数"""

    blocked_messages: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    """被阻止的消息数"""

    shielded_messages: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    """被加盾的消息数"""

    processing_time_total: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    """总处理时间"""

    total_process_time: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    """累计总处理时间"""

    last_process_time: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    """最近一次处理时间"""

    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    """错误计数"""

    start_time: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, default=datetime.datetime.now)
    """统计开始时间"""

    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, default=datetime.datetime.now)
    """记录创建时间"""

    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, default=datetime.datetime.now, onupdate=datetime.datetime.now)
    """记录更新时间"""

    __table_args__ = (
        Index("idx_anti_injection_stats_created_at", "created_at"),
        Index("idx_anti_injection_stats_updated_at", "updated_at"),
    )


class CacheEntries(Base):
    """工具缓存条目模型"""

    __tablename__ = "cache_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cache_key: Mapped[str] = mapped_column(get_string_field(500), nullable=False, unique=True, index=True)
    """缓存键，包含工具名、参数和代码哈希"""

    cache_value: Mapped[str] = mapped_column(Text, nullable=False)
    """缓存的数据，JSON格式"""

    expires_at: Mapped[float] = mapped_column(Float, nullable=False, index=True)
    """过期时间戳"""

    tool_name: Mapped[str] = mapped_column(get_string_field(100), nullable=False, index=True)
    """工具名称"""

    created_at: Mapped[float] = mapped_column(Float, nullable=False, default=lambda: time.time())
    """创建时间戳"""

    last_accessed: Mapped[float] = mapped_column(Float, nullable=False, default=lambda: time.time())
    """最后访问时间戳"""

    access_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    """访问次数"""

    __table_args__ = (
        Index("idx_cache_entries_key", "cache_key"),
        Index("idx_cache_entries_expires_at", "expires_at"),
        Index("idx_cache_entries_tool_name", "tool_name"),
        Index("idx_cache_entries_created_at", "created_at"),
    )


class MonthlyPlan(Base):
    """月度计划模型"""

    __tablename__ = "monthly_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_text: Mapped[str] = mapped_column(Text, nullable=False)
    target_month: Mapped[str] = mapped_column(String(7), nullable=False, index=True)
    status: Mapped[str] = mapped_column(get_string_field(20), nullable=False, default="active", index=True)
    usage_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_used_date: Mapped[str | None] = mapped_column(String(10), nullable=True, index=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, default=datetime.datetime.now)
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)

    __table_args__ = (
        Index("idx_monthlyplan_target_month_status", "target_month", "status"),
        Index("idx_monthlyplan_last_used_date", "last_used_date"),
        Index("idx_monthlyplan_usage_count", "usage_count"),
    )


def get_database_url():
    """获取数据库连接URL"""
    from src.config.config import global_config

    config = global_config.database

    if config.database_type == "mysql":
        # 对用户名和密码进行URL编码，处理特殊字符
        from urllib.parse import quote_plus

        encoded_user = quote_plus(config.mysql_user)
        encoded_password = quote_plus(config.mysql_password)

        # 检查是否配置了Unix socket连接
        if config.mysql_unix_socket:
            # 使用Unix socket连接
            encoded_socket = quote_plus(config.mysql_unix_socket)
            return (
                f"mysql+aiomysql://{encoded_user}:{encoded_password}"
                f"@/{config.mysql_database}"
                f"?unix_socket={encoded_socket}&charset={config.mysql_charset}"
            )
        else:
            # 使用标准TCP连接
            return (
                f"mysql+aiomysql://{encoded_user}:{encoded_password}"
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

        return f"sqlite+aiosqlite:///{db_path}"


_initializing: bool = False  # 防止递归初始化

async def initialize_database() -> tuple["AsyncEngine", async_sessionmaker[AsyncSession]]:
    """初始化异步数据库引擎和会话

    Returns:
        tuple[AsyncEngine, async_sessionmaker[AsyncSession]]: 创建好的异步引擎与会话工厂。

    说明:
        显式的返回类型标注有助于 Pyright/Pylance 正确推断调用处的对象，
        避免后续对返回值再次 `await` 时出现 *"tuple[...] 并非 awaitable"* 的误用。
    """
    global _engine, _SessionLocal, _initializing

    # 已经初始化直接返回
    if _engine is not None and _SessionLocal is not None:
        return _engine, _SessionLocal

    # 正在初始化的并发调用等待主初始化完成，避免递归
    if _initializing:
        import asyncio
        for _ in range(1000):  # 最多等待约10秒
            await asyncio.sleep(0.01)
            if _engine is not None and _SessionLocal is not None:
                return _engine, _SessionLocal
        raise RuntimeError("等待数据库初始化完成超时 (reentrancy guard)")

    _initializing = True
    try:
        database_url = get_database_url()
        from src.config.config import global_config

        config = global_config.database

        # 配置引擎参数
        engine_kwargs: dict[str, Any] = {
            "echo": False,  # 生产环境关闭SQL日志
            "future": True,
        }

        if config.database_type == "mysql":
            engine_kwargs.update(
                {
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
            )
        else:
            engine_kwargs.update(
                {
                    "connect_args": {
                        "check_same_thread": False,
                        "timeout": 60,
                    },
                }
            )

        _engine = create_async_engine(database_url, **engine_kwargs)
        _SessionLocal = async_sessionmaker(bind=_engine, class_=AsyncSession, expire_on_commit=False)

        # 迁移
        from src.common.database.db_migration import check_and_migrate_database
        await check_and_migrate_database(existing_engine=_engine)

        if config.database_type == "sqlite":
            await enable_sqlite_wal_mode(_engine)

        logger.info(f"SQLAlchemy异步数据库初始化成功: {config.database_type}")
        return _engine, _SessionLocal
    finally:
        _initializing = False


@asynccontextmanager
async def get_db_session() -> AsyncGenerator[AsyncSession]:
    """
    异步数据库会话上下文管理器。
    在初始化失败时会yield None，调用方需要检查会话是否为None。

    现在使用透明的连接池管理器来复用现有连接，提高并发性能。
    """
    SessionLocal = None
    try:
        _, SessionLocal = await initialize_database()
        if not SessionLocal:
            raise RuntimeError("数据库会话工厂 (_SessionLocal) 未初始化。")
    except Exception as e:
        logger.error(f"数据库初始化失败，无法创建会话: {e}")
        raise

    # 使用连接池管理器获取会话
    pool_manager = get_connection_pool_manager()

    async with pool_manager.get_session(SessionLocal) as session:
        # 对于 SQLite，在会话开始时设置 PRAGMA（仅对新连接）
        from src.config.config import global_config

        if global_config.database.database_type == "sqlite":
            try:
                await session.execute(text("PRAGMA busy_timeout = 60000"))
                await session.execute(text("PRAGMA foreign_keys = ON"))
            except Exception as e:
                logger.debug(f"设置 SQLite PRAGMA 时出错（可能是复用连接）: {e}")

        yield session


async def get_engine():
    """获取异步数据库引擎"""
    engine, _ = await initialize_database()
    return engine


class PermissionNodes(Base):
    """权限节点模型"""

    __tablename__ = "permission_nodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    node_name: Mapped[str] = mapped_column(get_string_field(255), nullable=False, unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    plugin_name: Mapped[str] = mapped_column(get_string_field(100), nullable=False, index=True)
    default_granted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("idx_permission_plugin", "plugin_name"),
        Index("idx_permission_node", "node_name"),
    )


class UserPermissions(Base):
    """用户权限模型"""

    __tablename__ = "user_permissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(get_string_field(50), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(get_string_field(100), nullable=False, index=True)
    permission_node: Mapped[str] = mapped_column(get_string_field(255), nullable=False, index=True)
    granted: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    granted_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    granted_by: Mapped[str | None] = mapped_column(get_string_field(100), nullable=True)

    __table_args__ = (
        Index("idx_user_platform_id", "platform", "user_id"),
        Index("idx_user_permission", "platform", "user_id", "permission_node"),
        Index("idx_permission_granted", "permission_node", "granted"),
    )


class UserRelationships(Base):
    """用户关系模型 - 存储用户与bot的关系数据"""

    __tablename__ = "user_relationships"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(get_string_field(100), nullable=False, unique=True, index=True)
    user_name: Mapped[str | None] = mapped_column(get_string_field(100), nullable=True)
    user_aliases: Mapped[str | None] = mapped_column(Text, nullable=True)  # 用户别名，逗号分隔
    relationship_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    preference_keywords: Mapped[str | None] = mapped_column(Text, nullable=True)  # 用户偏好关键词，逗号分隔
    relationship_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.3)  # 关系分数(0-1)
    last_updated: Mapped[float] = mapped_column(Float, nullable=False, default=time.time)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("idx_user_relationship_id", "user_id"),
        Index("idx_relationship_score", "relationship_score"),
        Index("idx_relationship_updated", "last_updated"),
    )
