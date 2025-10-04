import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.security import JWTCredentials, require_scope
from src.config.config import global_config
from src.plugin_system.apis import message_api

router = APIRouter()

@router.get("/messages/recent")
async def get_message_stats(
    days: int = Query(1, ge=1, description="指定查询过去多少天的数据"),
    message_type: Literal["all", "sent", "received"] = Query(
        "all", description="筛选消息类型: 'sent' (BOT发送的), 'received' (BOT接收的), or 'all' (全部)"
    ),
    credentials: JWTCredentials = Depends(require_scope("messages:read")),
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
                "subject": credentials.subject,
            }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
