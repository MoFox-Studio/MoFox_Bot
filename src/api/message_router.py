import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from src.chat.message_receive.chat_stream import get_chat_manager
from src.common.logger import get_logger
from src.common.security import get_api_key
from src.config.config import global_config
from src.plugin_system.apis import message_api, person_api

logger = get_logger("HTTP消息API")

router = APIRouter(dependencies=[Depends(get_api_key)])


@router.get("/messages/recent")
async def get_message_stats(
    days: int = Query(1, ge=1, description="指定查询过去多少天的数据"),
    message_type: Literal["all", "sent", "received"] = Query(
        "all", description="筛选消息类型: 'sent' (BOT发送的), 'received' (BOT接收的), or 'all' (全部)"
    ),
):
    """
    获取BOT在指定天数内的消息统计数据。
    """
    try:
        end_time = time.time()
        start_time = end_time - (days * 24 * 3600)

        messages = await message_api.get_messages_by_time(start_time, end_time)

        sent_count = 0
        received_count = 0
        bot_qq = str(global_config.bot.qq_account)

        for msg in messages:
            if msg.get("user_id") == bot_qq:
                sent_count += 1
            else:
                received_count += 1
        if message_type == "sent":
            return {"days": days, "message_type": message_type, "count": sent_count}
        elif message_type == "received":
            return {"days": days, "message_type": message_type, "count": received_count}
        else:
            return {
                "days": days,
                "message_type": message_type,
                "sent_count": sent_count,
                "received_count": received_count,
                "total_count": len(messages),
            }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/messages/stats_by_chat")
async def get_message_stats_by_chat(
    days: int = Query(1, ge=1, description="指定查询过去多少天的数据"),
    source: Literal["user", "bot"] = Query("user", description="筛选消息来源: 'user' (用户发送的), 'bot' (BOT发送的)"),
    group_by_user: bool = Query(False, description="是否按用户进行分组统计 (仅当 source='user' 时有效)"),
    format: bool = Query(False, description="是否格式化输出，包含群聊和用户信息"),
):
    """
    获取在指定天数内，按聊天会话统计的消息数据。
    可根据消息来源 (用户或BOT) 进行筛选。
    """
    try:
        # --- 1. 数据准备 ---
        # 计算查询的时间范围
        end_time = time.time()
        start_time = end_time - (days * 24 * 3600)
        # 从数据库获取指定时间范围内的所有消息
        messages = await message_api.get_messages_by_time(start_time, end_time)
        bot_qq = str(global_config.bot.qq_account)

        # --- 2. 消息筛选 ---
        # 根据 source 参数筛选消息来源
        if source == "user":
            # 筛选出用户发送的消息（即非机器人发送的消息）
            messages = [msg for msg in messages if msg.get("user_id") != bot_qq]
        else:  # source == "bot"
            # 筛选出机器人发送的消息
            messages = [msg for msg in messages if msg.get("user_id") == bot_qq]

        # --- 3. 数据统计 ---
        stats = {}
        # 如果统计来源是用户
        if source == "user":
            # 遍历用户消息进行统计
            for msg in messages:
                chat_id = msg.get("chat_id", "unknown")
                user_id = msg.get("user_id")
                # 初始化聊天会话的统计结构
                if chat_id not in stats:
                    stats[chat_id] = {"total_stats": {"total": 0}, "user_stats": {}}
                # 累加总消息数
                stats[chat_id]["total_stats"]["total"] += 1
                # 如果需要按用户分组，则进一步统计每个用户的消息数
                if group_by_user:
                    if user_id not in stats[chat_id]["user_stats"]:
                        stats[chat_id]["user_stats"][user_id] = 0
                    stats[chat_id]["user_stats"][user_id] += 1
            # 如果不按用户分组，则简化统计结果，只保留总数
            if not group_by_user:
                stats = {chat_id: data["total_stats"] for chat_id, data in stats.items()}
        # 如果统计来源是机器人
        else:
            # 遍历机器人消息进行统计
            for msg in messages:
                chat_id = msg.get("chat_id", "unknown")
                # 初始化聊天会话的统计结构
                if chat_id not in stats:
                    stats[chat_id] = 0
                # 累加机器人发送的消息数
                stats[chat_id] += 1

        # --- 4. 格式化输出 ---
        # 如果 format 参数为 False，直接返回原始统计数据
        if not format:
            return stats

        # 获取聊天管理器以查询会话信息
        chat_manager = get_chat_manager()
        formatted_stats = {}
        # 遍历统计结果进行格式化
        for chat_id, data in stats.items():
            stream = chat_manager.streams.get(chat_id)
            chat_name = f"未知会话 ({chat_id})"
            # 尝试获取更友好的会话名称（群名或用户名）
            if stream:
                if stream.group_info and stream.group_info.group_name:
                    chat_name = stream.group_info.group_name
                elif stream.user_info and stream.user_info.user_nickname:
                    chat_name = stream.user_info.user_nickname

            # 如果是机器人消息统计，直接格式化
            if source == "bot":
                formatted_stats[chat_id] = {"chat_name": chat_name, "count": data}
                continue

            # 如果是用户消息统计，进行更复杂的格式化
            formatted_data = {
                "chat_name": chat_name,
                "total_stats": data if not group_by_user else data["total_stats"],
            }
            # 如果按用户分组，则添加用户信息
            if group_by_user and "user_stats" in data:
                formatted_data["user_stats"] = {}
                for user_id, count in data["user_stats"].items():
                    person_id = person_api.get_person_id("qq", user_id)
                    person_info = await person_api.get_person_info(person_id)
                    nickname = person_info.get("nickname", "未知用户")
                    formatted_data["user_stats"][user_id] = {"nickname": nickname, "count": count}
            formatted_stats[chat_id] = formatted_data

        return formatted_stats

    except Exception as e:
        # 统一异常处理
        logger.error(f"获取消息统计时发生错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))
