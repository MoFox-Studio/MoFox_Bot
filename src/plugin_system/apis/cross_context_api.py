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

    for group in global_config.cross_context.groups:
        # 检查当前聊天的ID和类型是否在组的chat_ids中
        if [current_type, str(current_chat_raw_id)] in group.chat_ids:
            # 排除maizone专用组
            if group.name == "maizone_context_group":
                continue
            # 返回组内其他聊天的 [type, id] 列表
            return [chat_info for chat_info in group.chat_ids if chat_info != [current_type, str(current_chat_raw_id)]]

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


async def get_chat_history_by_group_name(group_name: str) -> str:
    """
    根据互通组名字获取聊天记录
    """
    target_group = None
    for group in global_config.cross_context.groups:
        if group.name == group_name:
            target_group = group
            break

    if not target_group:
        return f"找不到名为 {group_name} 的互通组。"

    if not target_group.chat_ids:
        return f"互通组 {group_name} 中没有配置任何聊天。"

    chat_infos = target_group.chat_ids
    chat_manager = get_chat_manager()

    cross_context_messages = []
    for chat_type, chat_raw_id in chat_infos:
        is_group = chat_type == "group"

        found_stream = None
        for stream in chat_manager.streams.values():
            if is_group:
                if stream.group_info and stream.group_info.group_id == chat_raw_id:
                    found_stream = stream
                    break
            else:  # private
                if stream.user_info and stream.user_info.user_id == chat_raw_id and not stream.group_info:
                    found_stream = stream
                    break

        if not found_stream:
            logger.warning(f"在已加载的聊天流中找不到ID为 {chat_raw_id} 的聊天。")
            continue

        stream_id = found_stream.stream_id

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
        return f"无法从互通组 {group_name} 中获取任何聊天记录。"

    return "# 跨上下文参考\n" + "\n\n".join(cross_context_messages) + "\n"


async def get_intercom_group_context_by_name(
    group_name: str, days: int = 3, limit_per_chat: int = 20, total_limit: int = 100
) -> str | None:
    """
    根据互通组的名称，构建该组的聊天上下文。

    Args:
        group_name: 互通组的名称。
        days: 获取过去多少天的消息。
        limit_per_chat: 每个聊天最多获取的消息条数。
        total_limit: 返回的总消息条数上限。

    Returns:
        如果找到匹配的组，则返回一个包含聊天记录的字符串；否则返回 None。
    """
    cross_context_config = global_config.cross_context
    if not (cross_context_config and cross_context_config.enable):
        return None

    target_group = None
    for group in cross_context_config.groups:
        if group.name == group_name:
            target_group = group
            break

    if not target_group:
        logger.error(f"在 cross_context 配置中未找到名为 '{group_name}' 的组。")
        return None

    chat_manager = get_chat_manager()

    all_messages = []
    end_time = time.time()
    start_time = end_time - (days * 24 * 60 * 60)

    for chat_type, chat_raw_id in target_group.chat_ids:
        is_group = chat_type == "group"

        found_stream = None
        # 采用与 get_chat_history_by_group_name 相同的健壮的 stream 查找方式
        for stream in chat_manager.streams.values():
            if is_group:
                if stream.group_info and stream.group_info.group_id == chat_raw_id:
                    found_stream = stream
                    break
            else:  # private
                if stream.user_info and stream.user_info.user_id == chat_raw_id and not stream.group_info:
                    found_stream = stream
                    break

        if not found_stream:
            logger.warning(f"在已加载的聊天流中找不到ID为 {chat_raw_id} 的聊天。")
            continue

        stream_id = found_stream.stream_id
        messages = await get_raw_msg_by_timestamp_with_chat(
            chat_id=stream_id,
            timestamp_start=start_time,
            timestamp_end=end_time,
            limit=limit_per_chat,
            limit_mode="latest",
        )
        all_messages.extend(messages)

    if not all_messages:
        return None

    # 按时间戳对所有消息进行排序
    all_messages.sort(key=lambda x: x.get("time", 0))

    # 限制总消息数
    if len(all_messages) > total_limit:
        all_messages = all_messages[-total_limit:]

    # build_readable_messages_with_id 返回一个元组 (formatted_string, message_id_list)
    formatted_string, _ = await build_readable_messages_with_id(all_messages)
    return formatted_string
