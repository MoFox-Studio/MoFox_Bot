"""SQLAlchemy数据库API模块

提供基于SQLAlchemy的数据库操作，替换Peewee以解决MySQL连接问题
支持自动重连、连接池管理和更好的错误处理
"""

import time
import traceback
from typing import Any

from sqlalchemy import and_, asc, desc, func, select
from sqlalchemy.exc import SQLAlchemyError

from src.common.database.sqlalchemy_models import (
    ActionRecords,
    CacheEntries,
    ChatStreams,
    Emoji,
    Expression,
    GraphEdges,
    GraphNodes,
    ImageDescriptions,
    Images,
    LLMUsage,
    MaiZoneScheduleStatus,
    Memory,
    Messages,
    OnlineTime,
    PersonInfo,
    Schedule,
    ThinkingLog,
    UserRelationships,
    get_db_session,
)
from src.common.logger import get_logger

logger = get_logger("sqlalchemy_database_api")

# 模型映射表，用于通过名称获取模型类
MODEL_MAPPING = {
    "Messages": Messages,
    "ActionRecords": ActionRecords,
    "PersonInfo": PersonInfo,
    "ChatStreams": ChatStreams,
    "LLMUsage": LLMUsage,
    "Emoji": Emoji,
    "Images": Images,
    "ImageDescriptions": ImageDescriptions,
    "OnlineTime": OnlineTime,
    "Memory": Memory,
    "Expression": Expression,
    "ThinkingLog": ThinkingLog,
    "GraphNodes": GraphNodes,
    "GraphEdges": GraphEdges,
    "Schedule": Schedule,
    "MaiZoneScheduleStatus": MaiZoneScheduleStatus,
    "CacheEntries": CacheEntries,
    "UserRelationships": UserRelationships,
}


async def build_filters(model_class, filters: dict[str, Any]):
    """构建查询过滤条件"""
    conditions = []

    for field_name, value in filters.items():
        if not hasattr(model_class, field_name):
            logger.warning(f"模型 {model_class.__name__} 中不存在字段 '{field_name}'")
            continue

        field = getattr(model_class, field_name)

        if isinstance(value, dict):
            # 处理 MongoDB 风格的操作符
            for op, op_value in value.items():
                if op == "$gt":
                    conditions.append(field > op_value)
                elif op == "$lt":
                    conditions.append(field < op_value)
                elif op == "$gte":
                    conditions.append(field >= op_value)
                elif op == "$lte":
                    conditions.append(field <= op_value)
                elif op == "$ne":
                    conditions.append(field != op_value)
                elif op == "$in":
                    conditions.append(field.in_(op_value))
                elif op == "$nin":
                    conditions.append(~field.in_(op_value))
                else:
                    logger.warning(f"未知操作符 '{op}' (字段: '{field_name}')")
        else:
            # 直接相等比较
            conditions.append(field == value)

    return conditions


async def db_query(
    model_class,
    data: dict[str, Any] | None = None,
    query_type: str | None = "get",
    filters: dict[str, Any] | None = None,
    limit: int | None = None,
    order_by: list[str] | None = None,
    single_result: bool | None = False,
) -> list[dict[str, Any]] | dict[str, Any] | None:
    """执行异步数据库查询操作

    Args:
        model_class: SQLAlchemy模型类
        data: 用于创建或更新的数据字典
        query_type: 查询类型 ("get", "create", "update", "delete", "count")
        filters: 过滤条件字典
        limit: 限制结果数量
        order_by: 排序字段，前缀'-'表示降序
        single_result: 是否只返回单个结果

    Returns:
        根据查询类型返回相应结果
    """
    try:
        if query_type not in ["get", "create", "update", "delete", "count"]:
            raise ValueError("query_type must be 'get', 'create', 'update', 'delete' or 'count'")

        async with get_db_session() as session:
            if not session:
                logger.error("[SQLAlchemy] 无法获取数据库会话")
                return None if single_result else []

            if query_type == "get":
                query = select(model_class)

                # 应用过滤条件
                if filters:
                    conditions = await build_filters(model_class, filters)
                    if conditions:
                        query = query.where(and_(*conditions))

                # 应用排序
                if order_by:
                    for field_name in order_by:
                        if field_name.startswith("-"):
                            field_name = field_name[1:]
                            if hasattr(model_class, field_name):
                                query = query.order_by(desc(getattr(model_class, field_name)))
                        else:
                            if hasattr(model_class, field_name):
                                query = query.order_by(asc(getattr(model_class, field_name)))

                # 应用限制
                if limit and limit > 0:
                    query = query.limit(limit)

                # 执行查询
                result = await session.execute(query)
                results = result.scalars().all()

                # 转换为字典格式
                result_dicts = []
                for result_obj in results:
                    result_dict = {}
                    for column in result_obj.__table__.columns:
                        result_dict[column.name] = getattr(result_obj, column.name)
                    result_dicts.append(result_dict)

                if single_result:
                    return result_dicts[0] if result_dicts else None
                return result_dicts

            elif query_type == "create":
                if not data:
                    raise ValueError("创建记录需要提供data参数")

                # 创建新记录
                new_record = model_class(**data)
                session.add(new_record)
                await session.flush()  # 获取自动生成的ID

                # 转换为字典格式返回
                result_dict = {}
                for column in new_record.__table__.columns:
                    result_dict[column.name] = getattr(new_record, column.name)
                return result_dict

            elif query_type == "update":
                if not data:
                    raise ValueError("更新记录需要提供data参数")

                query = select(model_class)

                # 应用过滤条件
                if filters:
                    conditions = await build_filters(model_class, filters)
                    if conditions:
                        query = query.where(and_(*conditions))

                # 首先获取要更新的记录
                result = await session.execute(query)
                records_to_update = result.scalars().all()

                # 更新每个记录
                affected_rows = 0
                for record in records_to_update:
                    for field, value in data.items():
                        if hasattr(record, field):
                            setattr(record, field, value)
                    affected_rows += 1

                return affected_rows

            elif query_type == "delete":
                query = select(model_class)

                # 应用过滤条件
                if filters:
                    conditions = await build_filters(model_class, filters)
                    if conditions:
                        query = query.where(and_(*conditions))

                # 首先获取要删除的记录
                result = await session.execute(query)
                records_to_delete = result.scalars().all()

                # 删除记录
                affected_rows = 0
                for record in records_to_delete:
                    await session.delete(record)
                    affected_rows += 1

                return affected_rows

            elif query_type == "count":
                query = select(func.count(model_class.id))

                # 应用过滤条件
                if filters:
                    conditions = await build_filters(model_class, filters)
                    if conditions:
                        query = query.where(and_(*conditions))

                result = await session.execute(query)
                return result.scalar()

    except SQLAlchemyError as e:
        logger.error(f"[SQLAlchemy] 数据库操作出错: {e}")
        traceback.print_exc()

        # 根据查询类型返回合适的默认值
        if query_type == "get":
            return None if single_result else []
        elif query_type in ["create", "update", "delete", "count"]:
            return None
        return None

    except Exception as e:
        logger.error(f"[SQLAlchemy] 意外错误: {e}")
        traceback.print_exc()

        if query_type == "get":
            return None if single_result else []
        return None


async def db_save(
    model_class, data: dict[str, Any], key_field: str | None = None, key_value: Any | None = None
) -> dict[str, Any] | None:
    """异步保存数据到数据库（创建或更新）

    Args:
        model_class: SQLAlchemy模型类
        data: 要保存的数据字典
        key_field: 用于查找现有记录的字段名
        key_value: 用于查找现有记录的字段值

    Returns:
        保存后的记录数据或None
    """
    try:
        async with get_db_session() as session:
            if not session:
                logger.error("[SQLAlchemy] 无法获取数据库会话")
                return None
            # 如果提供了key_field和key_value，尝试更新现有记录
            if key_field and key_value is not None:
                if hasattr(model_class, key_field):
                    query = select(model_class).where(getattr(model_class, key_field) == key_value)
                    result = await session.execute(query)
                    existing_record = result.scalars().first()

                    if existing_record:
                        # 更新现有记录
                        for field, value in data.items():
                            if hasattr(existing_record, field):
                                setattr(existing_record, field, value)

                        await session.flush()

                        # 转换为字典格式返回
                        result_dict = {}
                        for column in existing_record.__table__.columns:
                            result_dict[column.name] = getattr(existing_record, column.name)
                        return result_dict

            # 创建新记录
            new_record = model_class(**data)
            session.add(new_record)
            await session.flush()

            # 转换为字典格式返回
            result_dict = {}
            for column in new_record.__table__.columns:
                result_dict[column.name] = getattr(new_record, column.name)
            return result_dict

    except SQLAlchemyError as e:
        logger.error(f"[SQLAlchemy] 保存数据库记录出错: {e}")
        traceback.print_exc()
        return None
    except Exception as e:
        logger.error(f"[SQLAlchemy] 保存时意外错误: {e}")
        traceback.print_exc()
        return None


async def db_get(
    model_class,
    filters: dict[str, Any] | None = None,
    limit: int | None = None,
    order_by: str | None = None,
    single_result: bool | None = False,
) -> list[dict[str, Any]] | dict[str, Any] | None:
    """异步从数据库获取记录

    Args:
        model_class: SQLAlchemy模型类
        filters: 过滤条件
        limit: 结果数量限制
        order_by: 排序字段，前缀'-'表示降序
        single_result: 是否只返回单个结果

    Returns:
        记录数据或None
    """
    order_by_list = [order_by] if order_by else None
    return await db_query(
        model_class=model_class,
        query_type="get",
        filters=filters,
        limit=limit,
        order_by=order_by_list,
        single_result=single_result,
    )


async def store_action_info(
    chat_stream=None,
    action_build_into_prompt: bool = False,
    action_prompt_display: str = "",
    action_done: bool = True,
    thinking_id: str = "",
    action_data: dict | None = None,
    action_name: str = "",
) -> dict[str, Any] | None:
    """异步存储动作信息到数据库

    Args:
        chat_stream: 聊天流对象
        action_build_into_prompt: 是否将此动作构建到提示中
        action_prompt_display: 动作的提示显示文本
        action_done: 动作是否完成
        thinking_id: 关联的思考ID
        action_data: 动作数据字典
        action_name: 动作名称

    Returns:
        保存的记录数据或None
    """
    try:
        import orjson

        # 构建动作记录数据
        record_data = {
            "action_id": thinking_id or str(int(time.time() * 1000000)),
            "time": time.time(),
            "action_name": action_name,
            "action_data": orjson.dumps(action_data or {}).decode("utf-8"),
            "action_done": action_done,
            "action_build_into_prompt": action_build_into_prompt,
            "action_prompt_display": action_prompt_display,
        }

        # 从chat_stream获取聊天信息
        if chat_stream:
            record_data.update(
                {
                    "chat_id": getattr(chat_stream, "stream_id", ""),
                    "chat_info_stream_id": getattr(chat_stream, "stream_id", ""),
                    "chat_info_platform": getattr(chat_stream, "platform", ""),
                }
            )
        else:
            record_data.update(
                {
                    "chat_id": "",
                    "chat_info_stream_id": "",
                    "chat_info_platform": "",
                }
            )

        # 保存记录
        saved_record = await db_save(
            ActionRecords, data=record_data, key_field="action_id", key_value=record_data["action_id"]
        )

        if saved_record:
            logger.debug(f"[SQLAlchemy] 成功存储动作信息: {action_name} (ID: {record_data['action_id']})")
        else:
            logger.error(f"[SQLAlchemy] 存储动作信息失败: {action_name}")

        return saved_record

    except Exception as e:
        logger.error(f"[SQLAlchemy] 存储动作信息时发生错误: {e}")
        traceback.print_exc()
        return None
