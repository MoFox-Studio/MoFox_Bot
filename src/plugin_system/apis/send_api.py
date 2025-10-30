"""
发送API模块

专门负责发送各种类型的消息，采用标准Python包设计模式

使用方式：
    from src.plugin_system.apis import send_api

    # 方式1：直接使用stream_id（推荐）
    await send_api.text_to_stream("hello", stream_id)
    await send_api.emoji_to_stream(emoji_base64, stream_id)
    await send_api.custom_to_stream("video", video_data, stream_id)

    # 方式2：使用群聊/私聊指定函数
    await send_api.text_to_group("hello", "123456")
    await send_api.text_to_user("hello", "987654")

    # 方式3：使用通用custom_message函数
    await send_api.custom_message("video", video_data, "123456", True)

    # 方式4：向适配器发送命令并获取返回值
    response = await send_api.adapter_command_to_stream(
        "get_group_list", {}, stream_id
    )
    if response["status"] == "ok":
        group_list = response.get("data", [])


"""
from pathlib import Path


async def file_to_stream(
   file_path: str,
   stream_id: str,
   file_name: str | None = None,
   storage_message: bool = True,
   set_reply: bool = True
) -> bool:
   """向指定流发送文件

   Args:
       file_path: 文件的本地路径
       stream_id: 聊天流ID
       file_name: 发送到对方时显示的文件名，如果为 None 则使用原始文件名
       storage_message: 是否存储消息到数据库

   Returns:
       bool: 是否发送成功
   """
   target_stream = await get_chat_manager().get_stream(stream_id)
   if not target_stream:
       logger.error(f"[SendAPI] 未找到聊天流: {stream_id}")
       return False

   if not file_name:
       file_name = Path(file_path).name
   
   params = {
       "file": file_path,
       "name": file_name,
   }

   action = ""
   if target_stream.group_info:
       action = "upload_group_file"
       params["group_id"] = target_stream.group_info.group_id
   else:
       action = "upload_private_file"
       params["user_id"] = target_stream.user_info.user_id
   
   response = await adapter_command_to_stream(
       action=action,
       params=params,
       stream_id=stream_id,
       timeout=300.0  # 文件上传可能需要更长时间
   )

   if response.get("status") == "ok":
       logger.info(f"文件 {file_name} 已成功发送到 {stream_id}")
       return True
   else:
       logger.error(f"文件 {file_name} 发送到 {stream_id} 失败: {response.get('message')}")
       return False

import asyncio
import time
import traceback
from typing import Any

from maim_message import Seg, UserInfo

# 导入依赖
from src.chat.message_receive.chat_stream import ChatStream, get_chat_manager
from src.chat.message_receive.message import MessageRecv, MessageSending
from src.chat.message_receive.uni_message_sender import HeartFCSender
from src.common.logger import get_logger
from src.config.config import global_config

# 日志记录器
logger = get_logger("send_api")

# 适配器命令响应等待池
_adapter_response_pool: dict[str, asyncio.Future] = {}


def message_dict_to_message_recv(message_dict: dict[str, Any]) -> MessageRecv | None:
    """查找要回复的消息

    Args:
        message_dict: 消息字典或 DatabaseMessages 对象

    Returns:
        Optional[MessageRecv]: 找到的消息，如果没找到则返回None
    """
    # 兼容 DatabaseMessages 对象和字典
    if isinstance(message_dict, dict):
        user_platform = message_dict.get("user_platform", "")
        user_id = message_dict.get("user_id", "")
        user_nickname = message_dict.get("user_nickname", "")
        user_cardname = message_dict.get("user_cardname", "")
        chat_info_group_id = message_dict.get("chat_info_group_id")
        chat_info_group_platform = message_dict.get("chat_info_group_platform", "")
        chat_info_group_name = message_dict.get("chat_info_group_name", "")
        chat_info_platform = message_dict.get("chat_info_platform", "")
        message_id = message_dict.get("message_id") or message_dict.get("chat_info_message_id") or message_dict.get("id")
        time_val = message_dict.get("time")
        additional_config = message_dict.get("additional_config")
        processed_plain_text = message_dict.get("processed_plain_text")
    else:
        # DatabaseMessages 对象
        user_platform = getattr(message_dict, "user_platform", "")
        user_id = getattr(message_dict, "user_id", "")
        user_nickname = getattr(message_dict, "user_nickname", "")
        user_cardname = getattr(message_dict, "user_cardname", "")
        chat_info_group_id = getattr(message_dict, "chat_info_group_id", None)
        chat_info_group_platform = getattr(message_dict, "chat_info_group_platform", "")
        chat_info_group_name = getattr(message_dict, "chat_info_group_name", "")
        chat_info_platform = getattr(message_dict, "chat_info_platform", "")
        message_id = getattr(message_dict, "message_id", None)
        time_val = getattr(message_dict, "time", None)
        additional_config = getattr(message_dict, "additional_config", None)
        processed_plain_text = getattr(message_dict, "processed_plain_text", "")
    
    # 构建MessageRecv对象
    user_info = {
        "platform": user_platform,
        "user_id": user_id,
        "user_nickname": user_nickname,
        "user_cardname": user_cardname,
    }

    group_info = {}
    if chat_info_group_id:
        group_info = {
            "platform": chat_info_group_platform,
            "group_id": chat_info_group_id,
            "group_name": chat_info_group_name,
        }

    format_info = {"content_format": "", "accept_format": ""}
    template_info = {"template_items": {}}

    message_info = {
        "platform": chat_info_platform,
        "message_id": message_id,
        "time": time_val,
        "group_info": group_info,
        "user_info": user_info,
        "additional_config": additional_config,
        "format_info": format_info,
        "template_info": template_info,
    }

    new_message_dict = {
        "message_info": message_info,
        "raw_message": processed_plain_text,
        "processed_plain_text": processed_plain_text,
    }

    message_recv = MessageRecv(new_message_dict)

    logger.info(f"[SendAPI] 找到匹配的回复消息，发送者: {user_nickname}")
    return message_recv


def put_adapter_response(request_id: str, response_data: dict) -> None:
    """将适配器响应放入响应池"""
    if request_id in _adapter_response_pool:
        future = _adapter_response_pool.pop(request_id)
        if not future.done():
            future.set_result(response_data)


async def wait_adapter_response(request_id: str, timeout: float = 30.0) -> dict:
    """等待适配器响应"""
    future = asyncio.Future()
    _adapter_response_pool[request_id] = future

    try:
        response = await asyncio.wait_for(future, timeout=timeout)
        return response
    except asyncio.TimeoutError:
        await _adapter_response_pool.pop(request_id, None)
        return {"status": "error", "message": "timeout"}
    except Exception as e:
        await _adapter_response_pool.pop(request_id, None)
        return {"status": "error", "message": str(e)}


# =============================================================================
# 内部实现函数（不暴露给外部）
# =============================================================================


async def _send_to_target(
    message_type: str,
    content: str | dict,
    stream_id: str,
    display_message: str = "",
    typing: bool = False,
    reply_to: str = "",
    set_reply: bool = False,
    reply_to_message: dict[str, Any] | None = None,
    storage_message: bool = True,
    show_log: bool = True,
) -> bool:
    """向指定目标发送消息的内部实现

    Args:
        message_type: 消息类型，如"text"、"image"、"emoji"等
        content: 消息内容
        stream_id: 目标流ID
        display_message: 显示消息
        typing: 是否模拟打字等待。
        reply_to: 回复消息，格式为"发送者:消息内容"
        storage_message: 是否存储消息到数据库
        show_log: 发送是否显示日志

    Returns:
        bool: 是否发送成功
    """
    try:
        if reply_to:
            logger.warning("[SendAPI] 在0.10.0, reply_to 参数已弃用，请使用 reply_to_message 参数")

        if show_log:
            logger.debug(f"[SendAPI] 发送{message_type}消息到 {stream_id}")

        # 查找目标聊天流
        target_stream = await get_chat_manager().get_stream(stream_id)
        if not target_stream:
            logger.error(f"[SendAPI] 未找到聊天流: {stream_id}")
            return False

        # 创建发送器
        heart_fc_sender = HeartFCSender()

        # 生成消息ID
        current_time = time.time()
        message_id = f"send_api_{int(current_time * 1000)}"

        # 构建机器人用户信息
        bot_user_info = UserInfo(
            user_id=global_config.bot.qq_account,
            user_nickname=global_config.bot.nickname,
            platform=target_stream.platform,
        )

        # 创建消息段
        message_segment = Seg(type=message_type, data=content)  # type: ignore

        # 处理回复消息
        if reply_to:
            # 优先使用 reply_to 字符串构建 anchor_message
            # 解析 "发送者(ID)" 格式
            import re
            match = re.match(r"(.+)\((\d+)\)", reply_to)
            if match:
                sender_name, sender_id = match.groups()
                temp_message_dict = {
                    "user_nickname": sender_name,
                    "user_id": sender_id,
                    "chat_info_platform": target_stream.platform,
                    "message_id": "temp_reply_id", # 临时ID
                    "time": time.time()
                }
                anchor_message = message_dict_to_message_recv(message_dict=temp_message_dict)
            else:
                 anchor_message = None
            reply_to_platform_id = f"{target_stream.platform}:{sender_id}" if anchor_message else None

        elif reply_to_message:
            anchor_message = message_dict_to_message_recv(message_dict=reply_to_message)
            if anchor_message:
                anchor_message.update_chat_stream(target_stream)
                reply_to_platform_id = (
                    f"{anchor_message.message_info.platform}:{anchor_message.message_info.user_info.user_id}"
                )
            else:
                reply_to_platform_id = None
        else:
            anchor_message = None
            reply_to_platform_id = None

        # 构建发送消息对象
        bot_message = MessageSending(
            message_id=message_id,
            chat_stream=target_stream,
            bot_user_info=bot_user_info,
            sender_info=target_stream.user_info,
            message_segment=message_segment,
            display_message=display_message,
            reply=anchor_message,
            is_head=True,
            is_emoji=(message_type == "emoji"),
            thinking_start_time=current_time,
            reply_to=reply_to_platform_id,
        )

        # 发送消息
        sent_msg = await heart_fc_sender.send_message(
            bot_message,
            typing=typing,
            set_reply=set_reply,
            storage_message=storage_message,
            show_log=show_log,
        )

        if sent_msg:
            logger.debug(f"[SendAPI] 成功发送消息到 {stream_id}")
            return True
        else:
            logger.error("[SendAPI] 发送消息失败")
            return False

    except Exception as e:
        logger.error(f"[SendAPI] 发送消息时出错: {e}")
        traceback.print_exc()
        return False


# =============================================================================
# 公共API函数 - 预定义类型的发送函数
# =============================================================================


async def text_to_stream(
    text: str,
    stream_id: str,
    typing: bool = False,
    reply_to: str = "",
    reply_to_message: dict[str, Any] | None = None,
    set_reply: bool = True,
    storage_message: bool = True,
) -> bool:
    """向指定流发送文本消息

    Args:
        text: 要发送的文本内容
        stream_id: 聊天流ID
        typing: 是否显示正在输入
        reply_to: 回复消息，格式为"发送者:消息内容"
        storage_message: 是否存储消息到数据库

    Returns:
        bool: 是否发送成功
    """
    return await _send_to_target(
        "text",
        text,
        stream_id,
        "",
        typing,
        reply_to,
        set_reply=set_reply,
        reply_to_message=reply_to_message,
        storage_message=storage_message,
    )


async def emoji_to_stream(
    emoji_base64: str, stream_id: str, storage_message: bool = True, set_reply: bool = True
) -> bool:
    """向指定流发送表情包

    Args:
        emoji_base64: 表情包的base64编码
        stream_id: 聊天流ID
        storage_message: 是否存储消息到数据库

    Returns:
        bool: 是否发送成功
    """
    return await _send_to_target(
        "emoji", emoji_base64, stream_id, "", typing=False, storage_message=storage_message, set_reply=set_reply
    )


async def image_to_stream(
    image_base64: str, stream_id: str, storage_message: bool = True, set_reply: bool = True
) -> bool:
    """向指定流发送图片

    Args:
        image_base64: 图片的base64编码
        stream_id: 聊天流ID
        storage_message: 是否存储消息到数据库

    Returns:
        bool: 是否发送成功
    """
    return await _send_to_target(
        "image", image_base64, stream_id, "", typing=False, storage_message=storage_message, set_reply=set_reply
    )


async def command_to_stream(
    command: str | dict,
    stream_id: str,
    storage_message: bool = True,
    display_message: str = "",
    set_reply: bool = True,
) -> bool:
    """向指定流发送命令

    Args:
        command: 命令
        stream_id: 聊天流ID
        storage_message: 是否存储消息到数据库

    Returns:
        bool: 是否发送成功
    """
    return await _send_to_target(
        "command", command, stream_id, display_message, typing=False, storage_message=storage_message
    )


async def custom_to_stream(
    message_type: str,
    content: str | dict,
    stream_id: str,
    display_message: str = "",
    typing: bool = False,
    reply_to: str = "",
    reply_to_message: dict[str, Any] | None = None,
    set_reply: bool = True,
    storage_message: bool = True,
    show_log: bool = True,
) -> bool:
    """向指定流发送自定义类型消息

    Args:
        message_type: 消息类型，如"text"、"image"、"emoji"、"video"、"file"等
        content: 消息内容（通常是base64编码或文本）
        stream_id: 聊天流ID
        display_message: 显示消息
        typing: 是否显示正在输入
        reply_to: 回复消息，格式为"发送者:消息内容"
        storage_message: 是否存储消息到数据库
        show_log: 是否显示日志
    Returns:
        bool: 是否发送成功
    """
    return await _send_to_target(
        message_type=message_type,
        content=content,
        stream_id=stream_id,
        display_message=display_message,
        typing=typing,
        reply_to=reply_to,
        reply_to_message=reply_to_message,
        set_reply=set_reply,
        storage_message=storage_message,
        show_log=show_log,
    )


async def adapter_command_to_stream(
    action: str,
    params: dict,
    platform: str | None = "qq",
    stream_id: str | None = None,
    timeout: float = 30.0,
    storage_message: bool = False,
) -> dict:
    """向适配器发送命令并获取返回值

       雅诺狐的耳朵特别软

    Args:
        action (str): 适配器命令动作，如"get_group_list"、"get_friend_list"等
        params (dict): 命令参数字典，包含命令所需的参数
        platform (Optional[str]): 目标平台标识，可选，用于多平台支持
        stream_id (Optional[str]): 聊天流ID，可选，如果不提供则自动生成临时ID
        timeout (float): 超时时间（秒），默认30.0秒
        storage_message (bool): 是否存储消息到数据库，默认False

    Returns:
        dict: 适配器返回的响应，包含以下可能的状态：
            - 成功: {"status": "ok", "data": {...}, "message": "..."}
            - 失败: {"status": "failed", "message": "错误信息"}
            - 错误: {"status": "error", "message": "错误信息"}

    Raises:
        ValueError: 当stream_id和platform都未提供时抛出
    """
    if not stream_id and not platform:
        raise ValueError("必须提供stream_id或platform参数")

    try:
        logger.debug(f"[SendAPI] 向适配器发送命令: {action}")

        # 如果没有提供stream_id，则生成一个临时的
        if stream_id is None:
            import uuid

            stream_id = f"adapter_temp_{uuid.uuid4().hex[:8]}"
            logger.debug(f"[SendAPI] 自动生成临时stream_id: {stream_id}")

        # 查找目标聊天流
        target_stream = await get_chat_manager().get_stream(stream_id)
        if not target_stream:
            # 如果是自动生成的stream_id且找不到聊天流，创建一个临时的虚拟流
            if stream_id.startswith("adapter_temp_"):
                logger.debug(f"[SendAPI] 创建临时虚拟聊天流: {stream_id}")

                # 创建临时的用户信息和聊天流

                temp_user_info = UserInfo(user_id="system", user_nickname="System", platform=platform)

                temp_chat_stream = ChatStream(
                    stream_id=stream_id, platform=platform, user_info=temp_user_info, group_info=None
                )

                target_stream = temp_chat_stream
            else:
                logger.error(f"[SendAPI] 未找到聊天流: {stream_id}")
                return {"status": "error", "message": f"未找到聊天流: {stream_id}"}

        # 创建发送器
        heart_fc_sender = HeartFCSender()

        # 生成消息ID
        current_time = time.time()
        message_id = f"adapter_cmd_{int(current_time * 1000)}"

        # 构建机器人用户信息
        bot_user_info = UserInfo(
            user_id=global_config.bot.qq_account,
            user_nickname=global_config.bot.nickname,
            platform=target_stream.platform,
        )

        # 构建适配器命令数据
        adapter_command_data = {
            "action": action,
            "params": params,
            "timeout": timeout,
            "request_id": message_id,
        }

        # 创建消息段
        message_segment = Seg(type="adapter_command", data=adapter_command_data)  # type: ignore

        # 构建发送消息对象
        bot_message = MessageSending(
            message_id=message_id,
            chat_stream=target_stream,
            bot_user_info=bot_user_info,
            sender_info=target_stream.user_info,
            message_segment=message_segment,
            display_message=f"适配器命令: {action}",
            reply=None,
            is_head=True,
            is_emoji=False,
            thinking_start_time=current_time,
            reply_to=None,
        )

        # 发送消息
        sent_msg = await heart_fc_sender.send_message(
            bot_message, typing=False, set_reply=False, storage_message=storage_message
        )

        if not sent_msg:
            logger.error("[SendAPI] 发送适配器命令失败")
            return {"status": "error", "message": "发送适配器命令失败"}

        logger.debug("[SendAPI] 已发送适配器命令，等待响应...")

        # 等待适配器响应
        response = await wait_adapter_response(message_id, timeout)

        logger.debug(f"[SendAPI] 收到适配器响应: {response}")

        return response

    except Exception as e:
        logger.error(f"[SendAPI] 发送适配器命令时出错: {e}")
        traceback.print_exc()
        return {"status": "error", "message": f"发送适配器命令时出错: {e!s}"}
