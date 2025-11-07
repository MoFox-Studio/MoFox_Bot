"""消息处理工具模块
将原 MessageRecv 的消息处理逻辑提取为独立函数，
直接从适配器消息字典生成 DatabaseMessages
"""
import base64
import time
from typing import Any

import orjson
from maim_message import BaseMessageInfo, Seg

from src.chat.utils.self_voice_cache import consume_self_voice_text
from src.chat.utils.utils_image import get_image_manager
from src.chat.utils.utils_video import get_video_analyzer, is_video_analysis_available
from src.chat.utils.utils_voice import get_voice_text
from src.common.data_models.database_data_model import DatabaseMessages
from src.common.logger import get_logger
from src.config.config import global_config

logger = get_logger("message_processor")


async def process_message_from_dict(message_dict: dict[str, Any], stream_id: str, platform: str) -> DatabaseMessages:
    """从适配器消息字典处理并生成 DatabaseMessages

    这个函数整合了原 MessageRecv 的所有处理逻辑：
    1. 解析 message_segment 并异步处理内容（图片、语音、视频等）
    2. 提取所有消息元数据
    3. 直接构造 DatabaseMessages 对象

    Args:
        message_dict: MessageCQ序列化后的字典
        stream_id: 聊天流ID
        platform: 平台标识

    Returns:
        DatabaseMessages: 处理完成的数据库消息对象
    """
    # 解析基础信息
    message_info = BaseMessageInfo.from_dict(message_dict.get("message_info", {}))
    message_segment = Seg.from_dict(message_dict.get("message_segment", {}))

    # 初始化处理状态
    processing_state = {
        "is_emoji": False,
        "has_emoji": False,
        "is_picid": False,
        "has_picid": False,
        "is_voice": False,
        "is_video": False,
        "is_mentioned": None,
        "is_at": False,
        "priority_mode": "interest",
        "priority_info": None,
    }

    # 异步处理消息段，生成纯文本
    processed_plain_text = await _process_message_segments(message_segment, processing_state, message_info)

    # 解析 notice 信息
    is_notify = False
    is_public_notice = False
    notice_type = None
    if message_info.additional_config and isinstance(message_info.additional_config, dict):
        is_notify = message_info.additional_config.get("is_notice", False)
        is_public_notice = message_info.additional_config.get("is_public_notice", False)
        notice_type = message_info.additional_config.get("notice_type")

    # 提取用户信息
    user_info = message_info.user_info
    user_id = str(user_info.user_id) if user_info and user_info.user_id else ""
    user_nickname = (user_info.user_nickname or "") if user_info else ""
    user_cardname = user_info.user_cardname if user_info else None
    user_platform = (user_info.platform or "") if user_info else ""

    # 提取群组信息
    group_info = message_info.group_info
    group_id = group_info.group_id if group_info else None
    group_name = group_info.group_name if group_info else None
    group_platform = group_info.platform if group_info else None

    # chat_id 应该直接使用 stream_id（与数据库存储格式一致）
    # stream_id 是通过 platform + user_id/group_id 的 SHA-256 哈希生成的
    chat_id = stream_id

    # 准备 additional_config
    additional_config_str = _prepare_additional_config(message_info, is_notify, is_public_notice, notice_type)

    # 提取 reply_to
    reply_to = _extract_reply_from_segment(message_segment)

    # 构造 DatabaseMessages
    message_time = message_info.time if hasattr(message_info, "time") and message_info.time is not None else time.time()
    message_id = message_info.message_id or ""

    # 处理 is_mentioned
    is_mentioned = None
    mentioned_value = processing_state.get("is_mentioned")
    if isinstance(mentioned_value, bool):
        is_mentioned = mentioned_value
    elif isinstance(mentioned_value, int | float):
        is_mentioned = mentioned_value != 0

    db_message = DatabaseMessages(
        message_id=message_id,
        time=float(message_time),
        chat_id=chat_id,
        reply_to=reply_to,
        processed_plain_text=processed_plain_text,
        display_message=processed_plain_text,
        is_mentioned=is_mentioned,
        is_at=bool(processing_state.get("is_at", False)),
        is_emoji=bool(processing_state.get("is_emoji", False)),
        is_picid=bool(processing_state.get("is_picid", False)),
        is_command=False,  # 将在后续处理中设置
        is_notify=bool(is_notify),
        is_public_notice=bool(is_public_notice),
        notice_type=notice_type,
        additional_config=additional_config_str,
        user_id=user_id,
        user_nickname=user_nickname,
        user_cardname=user_cardname,
        user_platform=user_platform,
        chat_info_stream_id=stream_id,
        chat_info_platform=platform,
        chat_info_create_time=0.0,  # 将由 ChatStream 填充
        chat_info_last_active_time=0.0,  # 将由 ChatStream 填充
        chat_info_user_id=user_id,
        chat_info_user_nickname=user_nickname,
        chat_info_user_cardname=user_cardname,
        chat_info_user_platform=user_platform,
        chat_info_group_id=group_id,
        chat_info_group_name=group_name,
        chat_info_group_platform=group_platform,
    )

    # 设置优先级信息
    if processing_state.get("priority_mode"):
        setattr(db_message, "priority_mode", processing_state["priority_mode"])
    if processing_state.get("priority_info"):
        setattr(db_message, "priority_info", processing_state["priority_info"])

    # 设置其他运行时属性
    setattr(db_message, "is_voice", bool(processing_state.get("is_voice", False)))
    setattr(db_message, "is_video", bool(processing_state.get("is_video", False)))
    setattr(db_message, "has_emoji", bool(processing_state.get("has_emoji", False)))
    setattr(db_message, "has_picid", bool(processing_state.get("has_picid", False)))

    return db_message


async def _process_message_segments(segment: Seg, state: dict, message_info: BaseMessageInfo) -> str:
    """递归处理消息段，转换为文字描述

    Args:
        segment: 要处理的消息段
        state: 处理状态字典（用于记录消息类型标记）
        message_info: 消息基础信息（用于某些处理逻辑）

    Returns:
        str: 处理后的文本
    """
    if segment.type == "seglist":
        # 处理消息段列表
        segments_text = []
        for seg in segment.data:
            processed = await _process_message_segments(seg, state, message_info)
            if processed:
                segments_text.append(processed)
        return " ".join(segments_text)
    else:
        # 处理单个消息段
        return await _process_single_segment(segment, state, message_info)


async def _process_single_segment(segment: Seg, state: dict, message_info: BaseMessageInfo) -> str:
    """处理单个消息段

    Args:
        segment: 消息段
        state: 处理状态字典
        message_info: 消息基础信息

    Returns:
        str: 处理后的文本
    """
    try:
        if segment.type == "text":
            state["is_picid"] = False
            state["is_emoji"] = False
            state["is_video"] = False
            return segment.data

        elif segment.type == "at":
            state["is_picid"] = False
            state["is_emoji"] = False
            state["is_video"] = False
            state["is_at"] = True
            # 处理at消息，格式为"@<昵称:QQ号>"
            if isinstance(segment.data, str):
                if ":" in segment.data:
                    # 标准格式: "昵称:QQ号"
                    nickname, qq_id = segment.data.split(":", 1)
                    result = f"@<{nickname}:{qq_id}>"
                    return result
                else:
                    logger.warning(f"[at处理] 无法解析格式: '{segment.data}'")
                    return f"@{segment.data}"
            logger.warning(f"[at处理] 数据类型异常: {type(segment.data)}")
            return f"@{segment.data}" if isinstance(segment.data, str) else "@未知用户"

        elif segment.type == "image":
            # 如果是base64图片数据
            if isinstance(segment.data, str):
                state["has_picid"] = True
                state["is_picid"] = True
                state["is_emoji"] = False
                state["is_video"] = False
                image_manager = get_image_manager()
                _, processed_text = await image_manager.process_image(segment.data)
                return processed_text
            return "[发了一张图片，网卡了加载不出来]"

        elif segment.type == "emoji":
            state["has_emoji"] = True
            state["is_emoji"] = True
            state["is_picid"] = False
            state["is_voice"] = False
            state["is_video"] = False
            if isinstance(segment.data, str):
                return await get_image_manager().get_emoji_description(segment.data)
            return "[发了一个表情包，网卡了加载不出来]"

        elif segment.type == "voice":
            state["is_picid"] = False
            state["is_emoji"] = False
            state["is_voice"] = True
            state["is_video"] = False

            # 检查消息是否由机器人自己发送
            if message_info and message_info.user_info and str(message_info.user_info.user_id) == str(global_config.bot.qq_account):
                logger.info(f"检测到机器人自身发送的语音消息 (User ID: {message_info.user_info.user_id})，尝试从缓存获取文本。")
                if isinstance(segment.data, str):
                    cached_text = consume_self_voice_text(segment.data)
                    if cached_text:
                        logger.info(f"成功从缓存中获取语音文本: '{cached_text[:70]}...'")
                        return f"[语音：{cached_text}]"
                    else:
                        logger.warning("机器人自身语音消息缓存未命中，将回退到标准语音识别。")

            # 标准语音识别流程
            if isinstance(segment.data, str):
                return await get_voice_text(segment.data)
            return "[发了一段语音，网卡了加载不出来]"

        elif segment.type == "mention_bot":
            state["is_picid"] = False
            state["is_emoji"] = False
            state["is_voice"] = False
            state["is_video"] = False
            state["is_mentioned"] = float(segment.data)
            return ""

        elif segment.type == "priority_info":
            state["is_picid"] = False
            state["is_emoji"] = False
            state["is_voice"] = False
            if isinstance(segment.data, dict):
                # 处理优先级信息
                state["priority_mode"] = "priority"
                state["priority_info"] = segment.data
            return ""

        elif segment.type == "file":
            if isinstance(segment.data, dict):
                file_name = segment.data.get("name", "未知文件")
                file_size = segment.data.get("size", "未知大小")
                return f"[文件：{file_name} ({file_size}字节)]"
            return "[收到一个文件]"

        elif segment.type == "video":
            state["is_picid"] = False
            state["is_emoji"] = False
            state["is_voice"] = False
            state["is_video"] = True
            logger.info(f"接收到视频消息，数据类型: {type(segment.data)}")

            # 检查视频分析功能是否可用
            if not is_video_analysis_available():
                logger.warning("⚠️ Rust视频处理模块不可用，跳过视频分析")
                return "[视频]"

            if global_config.video_analysis.enable:
                logger.info("已启用视频识别,开始识别")
                if isinstance(segment.data, dict):
                    try:
                        # 从Adapter接收的视频数据
                        video_base64 = segment.data.get("base64")
                        filename = segment.data.get("filename", "video.mp4")

                        logger.info(f"视频文件名: {filename}")
                        logger.info(f"Base64数据长度: {len(video_base64) if video_base64 else 0}")

                        if video_base64:
                            # 解码base64视频数据
                            video_bytes = base64.b64decode(video_base64)
                            logger.info(f"解码后视频大小: {len(video_bytes)} 字节")

                            # 使用video analyzer分析视频
                            video_analyzer = get_video_analyzer()
                            result = await video_analyzer.analyze_video_from_bytes(
                                video_bytes, filename, prompt=global_config.video_analysis.batch_analysis_prompt
                            )

                            logger.info(f"视频分析结果: {result}")

                            # 返回视频分析结果
                            summary = result.get("summary", "")
                            if summary:
                                return f"[视频内容] {summary}"
                            else:
                                return "[已收到视频，但分析失败]"
                        else:
                            logger.warning("视频消息中没有base64数据")
                            return "[收到视频消息，但数据异常]"
                    except Exception as e:
                        logger.error(f"视频处理失败: {e!s}")
                        import traceback
                        logger.error(f"错误详情: {traceback.format_exc()}")
                        return "[收到视频，但处理时出现错误]"
                else:
                    logger.warning(f"视频消息数据不是字典格式: {type(segment.data)}")
                return "[发了一个视频，但格式不支持]"
            else:
                return ""
        else:
            logger.warning(f"未知的消息段类型: {segment.type}")
            return f"[{segment.type} 消息]"

    except Exception as e:
        logger.error(f"处理消息段失败: {e!s}, 类型: {segment.type}, 数据: {segment.data}")
        return f"[处理失败的{segment.type}消息]"


def _prepare_additional_config(message_info: BaseMessageInfo, is_notify: bool, is_public_notice: bool, notice_type: str | None) -> str | None:
    """准备 additional_config，包含 format_info 和 notice 信息

    Args:
        message_info: 消息基础信息
        is_notify: 是否为notice消息
        is_public_notice: 是否为公共notice
        notice_type: notice类型

    Returns:
        str | None: JSON 字符串格式的 additional_config，如果为空则返回 None
    """
    try:
        additional_config_data = {}

        # 首先获取adapter传递的additional_config
        if hasattr(message_info, "additional_config") and message_info.additional_config:
            if isinstance(message_info.additional_config, dict):
                additional_config_data = message_info.additional_config.copy()
            elif isinstance(message_info.additional_config, str):
                try:
                    additional_config_data = orjson.loads(message_info.additional_config)
                except Exception as e:
                    logger.warning(f"无法解析 additional_config JSON: {e}")
                    additional_config_data = {}

        # 添加notice相关标志
        if is_notify:
            additional_config_data["is_notice"] = True
            additional_config_data["notice_type"] = notice_type or "unknown"
            additional_config_data["is_public_notice"] = bool(is_public_notice)

        # 添加format_info到additional_config中
        if hasattr(message_info, "format_info") and message_info.format_info:
            try:
                format_info_dict = message_info.format_info.to_dict()
                additional_config_data["format_info"] = format_info_dict
                logger.debug(f"[message_processor] 嵌入 format_info 到 additional_config: {format_info_dict}")
            except Exception as e:
                logger.warning(f"将 format_info 转换为字典失败: {e}")

        # 序列化为JSON字符串
        if additional_config_data:
            return orjson.dumps(additional_config_data).decode("utf-8")
    except Exception as e:
        logger.error(f"准备 additional_config 失败: {e}")

    return None


def _extract_reply_from_segment(segment: Seg) -> str | None:
    """从消息段中提取reply_to信息

    Args:
        segment: 消息段

    Returns:
        str | None: 回复的消息ID，如果没有则返回None
    """
    try:
        if hasattr(segment, "type") and segment.type == "seglist":
            # 递归搜索seglist中的reply段
            if hasattr(segment, "data") and segment.data:
                for seg in segment.data:
                    reply_id = _extract_reply_from_segment(seg)
                    if reply_id:
                        return reply_id
        elif hasattr(segment, "type") and segment.type == "reply":
            # 找到reply段，返回message_id
            return str(segment.data) if segment.data else None
    except Exception as e:
        logger.warning(f"提取reply_to信息失败: {e}")
    return None


# =============================================================================
# DatabaseMessages 扩展工具函数
# =============================================================================

def get_message_info_from_db_message(db_message: DatabaseMessages) -> BaseMessageInfo:
    """从 DatabaseMessages 重建 BaseMessageInfo（用于需要 message_info 的遗留代码）

    Args:
        db_message: DatabaseMessages 对象

    Returns:
        BaseMessageInfo: 重建的消息信息对象
    """
    from maim_message import GroupInfo, UserInfo

    # 从 DatabaseMessages 的 user_info 转换为 maim_message.UserInfo
    user_info = UserInfo(
        platform=db_message.user_info.platform,
        user_id=db_message.user_info.user_id,
        user_nickname=db_message.user_info.user_nickname,
        user_cardname=db_message.user_info.user_cardname or ""
    )

    # 从 DatabaseMessages 的 group_info 转换为 maim_message.GroupInfo（如果存在）
    group_info = None
    if db_message.group_info:
        group_info = GroupInfo(
            platform=db_message.group_info.group_platform or "",
            group_id=db_message.group_info.group_id,
            group_name=db_message.group_info.group_name
        )

    # 解析 additional_config（从 JSON 字符串到字典）
    additional_config = None
    if db_message.additional_config:
        try:
            additional_config = orjson.loads(db_message.additional_config)
        except Exception:
            # 如果解析失败，保持为字符串
            pass

    # 创建 BaseMessageInfo
    message_info = BaseMessageInfo(
        platform=db_message.chat_info.platform,
        message_id=db_message.message_id,
        time=db_message.time,
        user_info=user_info,
        group_info=group_info,
        additional_config=additional_config  # type: ignore
    )

    return message_info


def set_db_message_runtime_attr(db_message: DatabaseMessages, attr_name: str, value: Any) -> None:
    """安全地为 DatabaseMessages 设置运行时属性

    Args:
        db_message: DatabaseMessages 对象
        attr_name: 属性名
        value: 属性值
    """
    setattr(db_message, attr_name, value)


def get_db_message_runtime_attr(db_message: DatabaseMessages, attr_name: str, default: Any = None) -> Any:
    """安全地获取 DatabaseMessages 的运行时属性

    Args:
        db_message: DatabaseMessages 对象
        attr_name: 属性名
        default: 默认值

    Returns:
        属性值或默认值
    """
    return getattr(db_message, attr_name, default)
