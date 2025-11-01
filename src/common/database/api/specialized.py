"""业务特定API

提供特定业务场景的数据库操作函数
"""

import time
from typing import Any, Optional

import orjson

from src.common.database.api.crud import CRUDBase
from src.common.database.api.query import QueryBuilder
from src.common.database.core.models import (
    ActionRecords,
    ChatStreams,
    LLMUsage,
    Messages,
    PersonInfo,
    UserRelationships,
)
from src.common.database.core.session import get_db_session
from src.common.logger import get_logger

logger = get_logger("database.specialized")


# CRUD实例
_action_records_crud = CRUDBase(ActionRecords)
_chat_streams_crud = CRUDBase(ChatStreams)
_llm_usage_crud = CRUDBase(LLMUsage)
_messages_crud = CRUDBase(Messages)
_person_info_crud = CRUDBase(PersonInfo)
_user_relationships_crud = CRUDBase(UserRelationships)


# ===== ActionRecords 业务API =====
async def store_action_info(
    chat_stream=None,
    action_build_into_prompt: bool = False,
    action_prompt_display: str = "",
    action_done: bool = True,
    thinking_id: str = "",
    action_data: Optional[dict] = None,
    action_name: str = "",
) -> Optional[dict[str, Any]]:
    """存储动作信息到数据库
    
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
        # 构建动作记录数据
        action_id = thinking_id or str(int(time.time() * 1000000))
        record_data = {
            "action_id": action_id,
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
        
        # 使用get_or_create保存记录
        saved_record = await _action_records_crud.get_or_create(
            defaults=record_data,
            action_id=action_id,
        )
        
        if saved_record:
            logger.debug(f"成功存储动作信息: {action_name} (ID: {action_id})")
            return {col.name: getattr(saved_record, col.name) for col in saved_record.__table__.columns}
        else:
            logger.error(f"存储动作信息失败: {action_name}")
            return None
            
    except Exception as e:
        logger.error(f"存储动作信息时发生错误: {e}", exc_info=True)
        return None


async def get_recent_actions(
    chat_id: str,
    limit: int = 10,
) -> list[ActionRecords]:
    """获取最近的动作记录
    
    Args:
        chat_id: 聊天ID
        limit: 限制数量
        
    Returns:
        动作记录列表
    """
    query = QueryBuilder(ActionRecords)
    return await query.filter(chat_id=chat_id).order_by("-time").limit(limit).all()


# ===== Messages 业务API =====
async def get_chat_history(
    stream_id: str,
    limit: int = 50,
    offset: int = 0,
) -> list[Messages]:
    """获取聊天历史
    
    Args:
        stream_id: 流ID
        limit: 限制数量
        offset: 偏移量
        
    Returns:
        消息列表
    """
    query = QueryBuilder(Messages)
    return await (
        query.filter(chat_info_stream_id=stream_id)
        .order_by("-time")
        .limit(limit)
        .offset(offset)
        .all()
    )


async def get_message_count(stream_id: str) -> int:
    """获取消息数量
    
    Args:
        stream_id: 流ID
        
    Returns:
        消息数量
    """
    query = QueryBuilder(Messages)
    return await query.filter(chat_info_stream_id=stream_id).count()


async def save_message(
    message_data: dict[str, Any],
    use_batch: bool = True,
) -> Optional[Messages]:
    """保存消息
    
    Args:
        message_data: 消息数据
        use_batch: 是否使用批处理
        
    Returns:
        保存的消息实例
    """
    return await _messages_crud.create(message_data, use_batch=use_batch)


# ===== PersonInfo 业务API =====
async def get_or_create_person(
    platform: str,
    person_id: str,
    defaults: Optional[dict[str, Any]] = None,
) -> Optional[PersonInfo]:
    """获取或创建人员信息
    
    Args:
        platform: 平台
        person_id: 人员ID
        defaults: 默认值
        
    Returns:
        人员信息实例
    """
    return await _person_info_crud.get_or_create(
        defaults=defaults or {},
        platform=platform,
        person_id=person_id,
    )


async def update_person_affinity(
    platform: str,
    person_id: str,
    affinity_delta: float,
) -> bool:
    """更新人员好感度
    
    Args:
        platform: 平台
        person_id: 人员ID
        affinity_delta: 好感度变化值
        
    Returns:
        是否成功
    """
    try:
        # 获取现有人员
        person = await _person_info_crud.get_by(
            platform=platform,
            person_id=person_id,
        )
        
        if not person:
            logger.warning(f"人员不存在: {platform}/{person_id}")
            return False
        
        # 更新好感度
        new_affinity = (person.affinity or 0.0) + affinity_delta
        await _person_info_crud.update(
            person.id,
            {"affinity": new_affinity},
        )
        
        logger.debug(f"更新好感度: {platform}/{person_id} {affinity_delta:+.2f} -> {new_affinity:.2f}")
        return True
        
    except Exception as e:
        logger.error(f"更新好感度失败: {e}", exc_info=True)
        return False


# ===== ChatStreams 业务API =====
async def get_or_create_chat_stream(
    stream_id: str,
    platform: str,
    defaults: Optional[dict[str, Any]] = None,
) -> Optional[ChatStreams]:
    """获取或创建聊天流
    
    Args:
        stream_id: 流ID
        platform: 平台
        defaults: 默认值
        
    Returns:
        聊天流实例
    """
    return await _chat_streams_crud.get_or_create(
        defaults=defaults or {},
        stream_id=stream_id,
        platform=platform,
    )


async def get_active_streams(
    platform: Optional[str] = None,
    limit: int = 100,
) -> list[ChatStreams]:
    """获取活跃的聊天流
    
    Args:
        platform: 平台（可选）
        limit: 限制数量
        
    Returns:
        聊天流列表
    """
    query = QueryBuilder(ChatStreams)
    
    if platform:
        query = query.filter(platform=platform)
    
    return await query.order_by("-last_message_time").limit(limit).all()


# ===== LLMUsage 业务API =====
async def record_llm_usage(
    model_name: str,
    input_tokens: int,
    output_tokens: int,
    stream_id: Optional[str] = None,
    platform: Optional[str] = None,
    user_id: str = "system",
    request_type: str = "chat",
    model_assign_name: Optional[str] = None,
    model_api_provider: Optional[str] = None,
    endpoint: str = "/v1/chat/completions",
    cost: float = 0.0,
    status: str = "success",
    time_cost: Optional[float] = None,
    use_batch: bool = True,
) -> Optional[LLMUsage]:
    """记录LLM使用情况
    
    Args:
        model_name: 模型名称
        input_tokens: 输入token数
        output_tokens: 输出token数
        stream_id: 流ID (兼容参数，实际不存储)
        platform: 平台 (兼容参数，实际不存储)
        user_id: 用户ID
        request_type: 请求类型
        model_assign_name: 模型分配名称
        model_api_provider: 模型API提供商
        endpoint: API端点
        cost: 成本
        status: 状态
        time_cost: 时间成本
        use_batch: 是否使用批处理
        
    Returns:
        LLM使用记录实例
    """
    usage_data = {
        "model_name": model_name,
        "prompt_tokens": input_tokens,  # 使用正确的字段名
        "completion_tokens": output_tokens,  # 使用正确的字段名
        "total_tokens": input_tokens + output_tokens,
        "user_id": user_id,
        "request_type": request_type,
        "endpoint": endpoint,
        "cost": cost,
        "status": status,
        "model_assign_name": model_assign_name or model_name,
        "model_api_provider": model_api_provider or "unknown",
    }
    
    if time_cost is not None:
        usage_data["time_cost"] = time_cost
    
    return await _llm_usage_crud.create(usage_data, use_batch=use_batch)


async def get_usage_statistics(
    start_time: Optional[float] = None,
    end_time: Optional[float] = None,
    model_name: Optional[str] = None,
) -> dict[str, Any]:
    """获取使用统计
    
    Args:
        start_time: 开始时间戳
        end_time: 结束时间戳
        model_name: 模型名称
        
    Returns:
        统计数据字典
    """
    from src.common.database.api.query import AggregateQuery
    
    query = AggregateQuery(LLMUsage)
    
    # 添加时间过滤
    if start_time:
        async with get_db_session() as session:
            from sqlalchemy import and_
            
            conditions = []
            if start_time:
                conditions.append(LLMUsage.timestamp >= start_time)
            if end_time:
                conditions.append(LLMUsage.timestamp <= end_time)
            if model_name:
                conditions.append(LLMUsage.model_name == model_name)
            
            if conditions:
                query._conditions = conditions
    
    # 聚合统计
    total_input = await query.sum("input_tokens")
    total_output = await query.sum("output_tokens")
    total_count = await query.filter().count() if hasattr(query, "count") else 0
    
    return {
        "total_input_tokens": int(total_input),
        "total_output_tokens": int(total_output),
        "total_tokens": int(total_input + total_output),
        "request_count": total_count,
    }


# ===== UserRelationships 业务API =====
async def get_user_relationship(
    platform: str,
    user_id: str,
    target_id: str,
) -> Optional[UserRelationships]:
    """获取用户关系
    
    Args:
        platform: 平台
        user_id: 用户ID
        target_id: 目标用户ID
        
    Returns:
        用户关系实例
    """
    return await _user_relationships_crud.get_by(
        platform=platform,
        user_id=user_id,
        target_id=target_id,
    )


async def update_relationship_affinity(
    platform: str,
    user_id: str,
    target_id: str,
    affinity_delta: float,
) -> bool:
    """更新关系好感度
    
    Args:
        platform: 平台
        user_id: 用户ID
        target_id: 目标用户ID
        affinity_delta: 好感度变化值
        
    Returns:
        是否成功
    """
    try:
        # 获取或创建关系
        relationship = await _user_relationships_crud.get_or_create(
            defaults={"affinity": 0.0, "interaction_count": 0},
            platform=platform,
            user_id=user_id,
            target_id=target_id,
        )
        
        if not relationship:
            logger.error(f"无法创建关系: {platform}/{user_id}->{target_id}")
            return False
        
        # 更新好感度和互动次数
        new_affinity = (relationship.affinity or 0.0) + affinity_delta
        new_count = (relationship.interaction_count or 0) + 1
        
        await _user_relationships_crud.update(
            relationship.id,
            {
                "affinity": new_affinity,
                "interaction_count": new_count,
                "last_interaction_time": time.time(),
            },
        )
        
        logger.debug(
            f"更新关系: {platform}/{user_id}->{target_id} "
            f"好感度{affinity_delta:+.2f}->{new_affinity:.2f} "
            f"互动{new_count}次"
        )
        return True
        
    except Exception as e:
        logger.error(f"更新关系好感度失败: {e}", exc_info=True)
        return False
