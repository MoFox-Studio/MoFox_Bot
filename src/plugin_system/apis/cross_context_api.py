"""
跨群聊上下文API
"""

import time
from typing import Any

from src.chat.message_receive.chat_stream import ChatStream, get_chat_manager
from src.chat.utils.chat_message_builder import (
    build_readable_messages_with_id,
    get_raw_msg_before_timestamp_with_chat,
    get_raw_msg_by_timestamp_with_chat,
)
from src.common.logger import get_logger
from src.config.config import global_config

logger = get_logger("cross_context_api")


async def get_context_groups(chat_id: str) -> list[list[str]] | None:
    """
    获取当前聊天所在的共享组的其他聊天ID
    """
    current_stream = await get_chat_manager().get_stream(chat_id)
    if not current_stream:
        return None

    is_group = current_stream.group_info is not None
    if is_group:
        assert current_stream.group_info is not None
        current_chat_raw_id = current_stream.group_info.group_id
    else:
        current_chat_raw_id = current_stream.user_info.user_id
    current_type = "group" if is_group else "private"

    # This feature is deprecated
    # for group in global_config.cross_context.groups:
    #     # 检查当前聊天的ID和类型是否在组的chat_ids中
    #     if [current_type, str(current_chat_raw_id)] in group.chat_ids:
    #         # 排除maizone专用组
    #         if group.name == "maizone_context_group":
    #             continue
    #         # 返回组内其他聊天的 [type, id] 列表
    #         return [chat_info for chat_info in group.chat_ids if chat_info != [current_type, str(current_chat_raw_id)]]

    return None


async def build_cross_context_normal(chat_stream: ChatStream, other_chat_infos: list[list[str]]) -> str:
    """
    构建跨群聊/私聊上下文 (Normal模式)
    """
    cross_context_messages = []
    for chat_type, chat_raw_id in other_chat_infos:
        is_group = chat_type == "group"
        stream_id = get_chat_manager().get_stream_id(chat_stream.platform, chat_raw_id, is_group=is_group)
        if not stream_id:
            continue

        try:
            messages = await get_raw_msg_before_timestamp_with_chat(
                chat_id=stream_id,
                timestamp=time.time(),
                limit=5,  # 可配置
            )
            if messages:
                chat_name = await get_chat_manager().get_stream_name(stream_id) or chat_raw_id
                formatted_messages, _ = await build_readable_messages_with_id(messages, timestamp_mode="relative")
                cross_context_messages.append(f'[以下是来自"{chat_name}"的近期消息]\n{formatted_messages}')
        except Exception as e:
            logger.error(f"获取聊天 {chat_raw_id} 的消息失败: {e}")
            continue

    if not cross_context_messages:
        return ""

    return "# 跨上下文参考\n" + "\n\n".join(cross_context_messages) + "\n"


async def build_cross_context_s4u(
    chat_stream: ChatStream,
    other_chat_infos: list[list[str]],
    target_user_info: dict[str, Any] | None,
) -> str:
    """
    构建跨群聊/私聊上下文 (S4U模式)
    """
    cross_context_messages = []
    if target_user_info:
        user_id = target_user_info.get("user_id")

        if user_id:
            for chat_type, chat_raw_id in other_chat_infos:
                is_group = chat_type == "group"
                stream_id = get_chat_manager().get_stream_id(chat_stream.platform, chat_raw_id, is_group=is_group)
                if not stream_id:
                    continue

                try:
                    messages = await get_raw_msg_before_timestamp_with_chat(
                        chat_id=stream_id,
                        timestamp=time.time(),
                        limit=20,  # 获取更多消息以供筛选
                    )
                    user_messages = [msg for msg in messages if msg.get("user_id") == user_id][-5:]

                    if user_messages:
                        chat_name = await get_chat_manager().get_stream_name(stream_id) or chat_raw_id
                        user_name = (
                            target_user_info.get("person_name") or target_user_info.get("user_nickname") or user_id
                        )
                        formatted_messages, _ = await build_readable_messages_with_id(
                            user_messages, timestamp_mode="relative"
                        )
                        cross_context_messages.append(
                            f'[以下是"{user_name}"在"{chat_name}"的近期发言]\n{formatted_messages}'
                        )
                except Exception as e:
                    logger.error(f"获取用户 {user_id} 在聊天 {chat_raw_id} 的消息失败: {e}")
                    continue

    if not cross_context_messages:
        return ""

    return "### 其他群聊中的聊天记录\n" + "\n\n".join(cross_context_messages) + "\n"



async def get_user_centric_context(
    user_id: str, platform: str, limit: int, exclude_chat_id: str | None = None
) -> str | None:
    """
    获取以用户为中心的全域聊天记录。

    Args:
        user_id: 目标用户的ID。
        platform: 用户所在的平台。
        limit: 每个聊天中获取的最大消息数量。
        exclude_chat_id: 需要排除的当前聊天ID。

    Returns:
        构建好的上下文信息字符串，如果没有找到则返回None。
    """
    chat_manager = get_chat_manager()
    user_messages_map = {}

    # 遍历所有相关的聊天流
    streams_to_search = []
    private_stream = None
    group_streams = []

    for stream in chat_manager.streams.values():
        if stream.stream_id == exclude_chat_id:
            continue

        is_group = stream.group_info is not None
        if is_group:
            # 对于群聊，检查用户是否是成员之一 (通过消息记录判断)
            group_streams.append(stream)
        else:
            # 对于私聊，检查是否是与目标用户的私聊
            if stream.user_info and stream.user_info.user_id == user_id:
                private_stream = stream

    # 优先添加私聊流
    if private_stream:
        streams_to_search.append(private_stream)

    # 按最近活跃时间对群聊流进行排序
    group_streams.sort(key=lambda s: s.last_active_time, reverse=True)
    streams_to_search.extend(group_streams)

    # 应用聊天流数量限制
    stream_limit = global_config.cross_context.user_centric_retrieval_stream_limit
    if stream_limit > 0:
        streams_to_search = streams_to_search[:stream_limit]

    for stream in streams_to_search:
        try:
            messages = await get_raw_msg_before_timestamp_with_chat(
                chat_id=stream.stream_id,
                timestamp=time.time(),
                limit=limit * 5,  # 获取更多消息以供筛选
            )
            user_messages = [msg for msg in messages if msg.get("user_id") == user_id][-limit:]

            if user_messages:
                chat_name = await chat_manager.get_stream_name(stream.stream_id) or stream.stream_id
                if chat_name not in user_messages_map:
                    user_messages_map[chat_name] = []
                user_messages_map[chat_name].extend(user_messages)
        except Exception as e:
            logger.error(f"获取用户 {user_id} 在聊天 {stream.stream_id} 的消息失败: {e}")
            continue

    if not user_messages_map:
        return None

    # 构建最终的上下文字符串
    cross_context_parts = []
    for chat_name, messages in user_messages_map.items():
        # 按时间戳对消息进行排序
        messages.sort(key=lambda x: x.get("time", 0))
        formatted_messages, _ = await build_readable_messages_with_id(messages, timestamp_mode="relative")
        cross_context_parts.append(f'[以下是该用户在"{chat_name}"的近期发言]\n{formatted_messages}')

    if not cross_context_parts:
        return None

    return "### 该用户在其他地方的聊天记录\n" + "\n\n".join(cross_context_parts) + "\n"
