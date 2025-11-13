"""
情绪API模块

负责提供与机器人情绪状态相关的接口，允许插件查询和控制情绪。

使用方式：
    from src.plugin_system.apis import mood_api

    # 获取当前情绪
    current_mood = mood_api.get_mood(chat_id)

    # 设置新情绪
    mood_api.set_mood(chat_id, "感到很开心")

    # 锁定情绪3分钟
    await mood_api.lock_mood(chat_id, duration=180)

    # 解锁情绪
    await mood_api.unlock_mood(chat_id)

    # 检查情绪是否被锁定
    is_locked = mood_api.is_mood_locked(chat_id)
"""

import asyncio

from src.common.logger import get_logger
from src.mood.mood_manager import mood_manager

logger = get_logger("mood_api")

# 用于存储情绪解锁任务
_unlock_tasks: dict[str, asyncio.Task] = {}


def get_mood(chat_id: str) -> str:
    """获取指定聊天的当前情绪状态

    Args:
        chat_id (str): 聊天ID (通常是 stream_id)

    Returns:
        str: 当前的情绪状态描述
    """
    chat_mood = mood_manager.get_mood_by_chat_id(chat_id)
    logger.debug(f"[{chat_id}] 获取情绪状态: {chat_mood.mood_state}")
    return chat_mood.mood_state


def set_mood(chat_id: str, new_mood: str):
    """强制设定指定聊天的新情绪状态

    Args:
        chat_id (str): 聊天ID
        new_mood (str): 新的情绪状态
    """
    chat_mood = mood_manager.get_mood_by_chat_id(chat_id)
    chat_mood.mood_state = new_mood
    logger.info(f"[{chat_id}] 情绪状态被强制设置为: {new_mood}")


async def lock_mood(chat_id: str, duration: float | None = None):
    """
    锁定指定聊天的情绪，防止其自动更新。

    Args:
        chat_id (str): 聊天ID
        duration (Optional[float]): 锁定时长（秒）。如果为 None，则永久锁定直到手动解锁。
    """
    if chat_id in _unlock_tasks:
        _unlock_tasks[chat_id].cancel()
        del _unlock_tasks[chat_id]

    mood_manager.insomnia_chats.add(chat_id)
    logger.info(f"[{chat_id}] 情绪已锁定。")

    if duration:
        logger.info(f"[{chat_id}] 情绪将于 {duration} 秒后自动解锁。")

        async def _unlock_after():
            await asyncio.sleep(duration)
            if chat_id in mood_manager.insomnia_chats:
                await unlock_mood(chat_id)
                logger.info(f"[{chat_id}] 情绪已自动解锁。")

        task = asyncio.create_task(_unlock_after())
        _unlock_tasks[chat_id] = task


async def unlock_mood(chat_id: str):
    """
    立即解除情绪锁定。
    """
    if chat_id in _unlock_tasks:
        _unlock_tasks[chat_id].cancel()
        del _unlock_tasks[chat_id]

    if chat_id in mood_manager.insomnia_chats:
        mood_manager.insomnia_chats.remove(chat_id)
        logger.info(f"[{chat_id}] 情绪已手动解锁。")


def is_mood_locked(chat_id: str) -> bool:
    """检查指定聊天的情绪是否当前处于锁定状态。

    Returns:
        bool: 如果情绪被锁定，则返回 True，否则返回 False。
    """
    return chat_id in mood_manager.insomnia_chats
