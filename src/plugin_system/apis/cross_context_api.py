"""
跨群聊上下文API
"""

import time
from typing import Any

from src.chat.message_receive.chat_stream import ChatStream, get_chat_manager
from src.chat.utils.chat_message_builder import (
    build_readable_messages_with_id,
    get_raw_msg_before_timestamp_with_chat,
)
from src.common.logger import get_logger
from src.config.config import global_config
from src.config.official_configs import ContextGroup

logger = get_logger("cross_context_api")


async def get_context_group(chat_id: str) -> ContextGroup | None:
    """
    获取当前聊天所在的共享组
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
        for chat_info in group.chat_ids:
            if len(chat_info) >= 2:
                chat_type, chat_raw_id = chat_info[0], chat_info[1]
                if chat_type == current_type and str(chat_raw_id) == str(current_chat_raw_id):
                    # 排除maizone专用组
                    if group.name == "maizone_context_group":
                        continue
                    return group

    return None


async def build_cross_context_normal(chat_stream: ChatStream, context_group: ContextGroup) -> str:
    """
    构建跨群聊/私聊上下文 (Normal模式)。

    根据共享组的配置（白名单或黑名单模式），获取相关聊天的近期消息，并格式化为字符串。

    Args:
        chat_stream: 当前的聊天流对象。
        context_group: 当前聊天所在的上下文共享组配置。

    Returns:
        一个包含格式化后的跨上下文消息的字符串，如果无消息则为空字符串。
    """
    cross_context_messages = []
    chat_manager = get_chat_manager()

    chat_infos_to_fetch = []
    if context_group.mode == "blacklist":
        # 黑名单模式：获取所有聊天，并排除在 chat_ids 中定义过的聊天
        blacklisted_ids = {tuple(info[:2]) for info in context_group.chat_ids}
        for stream_id, stream in chat_manager.streams.items():
            is_group = stream.group_info is not None
            chat_type = "group" if is_group else "private"

            # 安全地获取 raw_id
            if is_group and stream.group_info:
                raw_id = stream.group_info.group_id
            elif not is_group and stream.user_info:
                raw_id = stream.user_info.user_id
            else:
                continue  # 如果缺少关键信息则跳过

            # 如果当前聊天不在黑名单中，则添加到待获取列表
            if (chat_type, str(raw_id)) not in blacklisted_ids:
                chat_infos_to_fetch.append([chat_type, str(raw_id), str(context_group.default_limit)])
    else:
        # 白名单模式：直接使用配置中定义的 chat_ids
        chat_infos_to_fetch = context_group.chat_ids

    # 遍历待获取列表，抓取并格式化消息
    for chat_info in chat_infos_to_fetch:
        chat_type, chat_raw_id, limit_str = (
            chat_info[0],
            chat_info[1],
            chat_info[2] if len(chat_info) > 2 else str(context_group.default_limit),
        )
        limit = int(limit_str)
        is_group = chat_type == "group"
        stream_id = chat_manager.get_stream_id(chat_stream.platform, chat_raw_id, is_group=is_group)
        if not stream_id or stream_id == chat_stream.stream_id:
            continue

        try:
            messages = await get_raw_msg_before_timestamp_with_chat(
                chat_id=stream_id,
                timestamp=time.time(),
                limit=limit,
            )
            if messages:
                chat_name = await chat_manager.get_stream_name(stream_id) or chat_raw_id
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
    target_user_info: dict[str, Any] | None,
) -> str:
    """
    构建跨群聊/私聊上下文 (S4U模式)。

    基于全局S4U配置，检索目标用户在其他聊天中的发言。
    """
    cross_context_config = global_config.cross_context
    if not target_user_info or not (user_id := target_user_info.get("user_id")):
        return ""

    chat_manager = get_chat_manager()
    all_user_messages = []

    # 1. 根据全局S4U配置确定要扫描的聊天范围
    streams_to_scan = []
    if cross_context_config.s4u_mode == "whitelist":
        for chat_str in cross_context_config.s4u_whitelist_chats:
            try:
                platform, chat_type, chat_raw_id = chat_str.split(":")
                is_group = chat_type == "group"
                stream_id = chat_manager.get_stream_id(platform, chat_raw_id, is_group=is_group)
                if stream_id and stream_id != chat_stream.stream_id:
                    streams_to_scan.append(stream_id)
            except ValueError:
                logger.warning(f"无效的S4U白名单格式: {chat_str}")
    else:  # blacklist mode
        blacklisted_streams = set()
        for chat_str in cross_context_config.s4u_blacklist_chats:
            try:
                platform, chat_type, chat_raw_id = chat_str.split(":")
                is_group = chat_type == "group"
                stream_id = chat_manager.get_stream_id(platform, chat_raw_id, is_group=is_group)
                if stream_id:
                    blacklisted_streams.add(stream_id)
            except ValueError:
                logger.warning(f"无效的S4U黑名单格式: {chat_str}")

        for stream_id in chat_manager.streams:
            if stream_id != chat_stream.stream_id and stream_id not in blacklisted_streams:
                streams_to_scan.append(stream_id)

    # 2. 从筛选出的聊天流中获取目标用户的消息
    limit = cross_context_config.s4u_limit
    for stream_id in streams_to_scan:
        try:
            # 获取稍多一些消息以确保能筛选出用户消息
            messages = await get_raw_msg_before_timestamp_with_chat(
                chat_id=stream_id, timestamp=time.time(), limit=limit * 5
            )
            user_messages = [msg for msg in messages if msg.get("user_id") == user_id][-limit:]

            if user_messages:
                # 记录消息来源的 stream_id 和最新消息的时间戳
                latest_timestamp = max(msg.get("time", 0) for msg in user_messages)
                all_user_messages.append(
                    {"stream_id": stream_id, "messages": user_messages, "latest_timestamp": latest_timestamp}
                )
        except Exception as e:
            logger.error(f"S4U模式下获取聊天 {stream_id} 的消息失败: {e}")

    # 3. 按最新消息时间排序，并根据 stream_limit 截断
    all_user_messages.sort(key=lambda x: x["latest_timestamp"], reverse=True)
    limited_messages = all_user_messages[: cross_context_config.s4u_stream_limit]

    # 4. 格式化最终的消息文本
    cross_context_blocks = []
    for item in limited_messages:
        stream_id = item["stream_id"]
        messages = item["messages"]
        try:
            chat_name = await chat_manager.get_stream_name(stream_id) or "未知聊天"
            user_name = target_user_info.get("person_name") or target_user_info.get("user_nickname") or user_id
            formatted, _ = await build_readable_messages_with_id(messages, timestamp_mode="relative")
            cross_context_blocks.append(f'[以下是"{user_name}"在"{chat_name}"的近期发言]\n{formatted}')
        except Exception as e:
            logger.error(f"S4U模式下格式化消息失败 (stream: {stream_id}): {e}")

    if not cross_context_blocks:
        return ""

    return "### 其他群聊中的聊天记录\n" + "\n\n".join(cross_context_blocks) + "\n"


async def get_intercom_group_context(group_name: str, limit_per_chat: int = 20, total_limit: int = 100) -> str | None:
    """
    根据互通组的名称，构建该组的聊天上下文。
    支持黑白名单模式，并以分块形式返回每个聊天的消息。

    Args:
        group_name: 互通组的名称。
        limit_per_chat: 每个聊天最多获取的消息条数。
        total_limit: 返回的总消息条数上限。

    Returns:
        如果找到匹配的组并获取到消息，则返回一个包含聊天记录的字符串；否则返回 None。
    """
    cross_context_config = global_config.cross_context
    if not (cross_context_config and cross_context_config.enable):
        return None

    target_group = next((g for g in cross_context_config.groups if g.name == group_name), None)

    if not target_group:
        logger.error(f"在 cross_context 配置中未找到名为 '{group_name}' 的组。")
        return None

    chat_manager = get_chat_manager()

    # 1. 根据黑白名单模式确定要处理的聊天列表
    chat_infos_to_fetch = []
    if target_group.mode == "blacklist":
        blacklisted_ids = {tuple(info[:2]) for info in target_group.chat_ids}
        for stream in chat_manager.streams.values():
            is_group = stream.group_info is not None
            chat_type = "group" if is_group else "private"

            if is_group and stream.group_info:
                raw_id = stream.group_info.group_id
            elif not is_group and stream.user_info:
                raw_id = stream.user_info.user_id
            else:
                continue

            if (chat_type, str(raw_id)) not in blacklisted_ids:
                chat_infos_to_fetch.append([chat_type, str(raw_id)])
    else:  # whitelist mode
        chat_infos_to_fetch = target_group.chat_ids

    # 2. 获取所有相关消息
    all_messages = []
    for chat_info in chat_infos_to_fetch:
        chat_type, chat_raw_id = chat_info[0], chat_info[1]
        is_group = chat_type == "group"

        # 查找 stream
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
                limit=limit_per_chat,
            )
            if messages:
                # 为每条消息附加 stream_id 以便后续分组
                for msg in messages:
                    msg["_stream_id"] = stream_id
                all_messages.extend(messages)
        except Exception as e:
            logger.error(f"获取聊天 {chat_raw_id} 的消息失败: {e}")

    if not all_messages:
        return None

    # 3. 应用总数限制
    all_messages.sort(key=lambda x: x.get("time", 0))
    if len(all_messages) > total_limit:
        all_messages = all_messages[-total_limit:]

    # 4. 按聊天分组并格式化
    messages_by_stream = {}
    for msg in all_messages:
        stream_id = msg.get("_stream_id")
        if stream_id not in messages_by_stream:
            messages_by_stream[stream_id] = []
        messages_by_stream[stream_id].append(msg)

    cross_context_messages = []
    for stream_id, messages in messages_by_stream.items():
        if messages:
            chat_name = await chat_manager.get_stream_name(stream_id) or "未知聊天"
            formatted_messages, _ = await build_readable_messages_with_id(messages, timestamp_mode="relative")
            cross_context_messages.append(f'[以下是来自"{chat_name}"的近期消息]\n{formatted_messages}')

    if not cross_context_messages:
        return None

    return "# 跨上下文参考\n" + "\n\n".join(cross_context_messages) + "\n"
