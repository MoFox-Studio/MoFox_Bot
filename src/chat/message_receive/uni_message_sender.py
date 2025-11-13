import asyncio
import traceback

from rich.traceback import install

from src.chat.message_receive.message import MessageSending
from src.chat.message_receive.storage import MessageStorage
from src.chat.utils.utils import calculate_typing_time, truncate_message
from src.common.logger import get_logger
from src.common.message.api import get_global_api

install(extra_lines=3)

logger = get_logger("sender")


async def send_message(message: MessageSending, show_log=True) -> bool:
    """合并后的消息发送函数，包含WS发送和日志记录"""
    message_preview = truncate_message(message.processed_plain_text, max_length=120)

    try:
        # 直接调用API发送消息
        await get_global_api().send_message(message)
        if show_log:
            logger.info(f"已将消息  '{message_preview}'  发往平台'{message.message_info.platform}'")

        # 触发 AFTER_SEND 事件
        try:
            from src.plugin_system.base.component_types import EventType
            from src.plugin_system.core.event_manager import event_manager

            if message.chat_stream:
                await event_manager.trigger_event(
                    EventType.AFTER_SEND,
                    permission_group="SYSTEM",
                    stream_id=message.chat_stream.stream_id,
                    message=message,
                )
        except Exception as event_error:
            logger.error(f"触发 AFTER_SEND 事件时出错: {event_error}", exc_info=True)

        return True

    except Exception as e:
        logger.error(f"发送消息   '{message_preview}'   发往平台'{message.message_info.platform}' 失败: {e!s}")
        traceback.print_exc()
        raise e  # 重新抛出其他异常


class HeartFCSender:
    """管理消息的注册、即时处理、发送和存储，并跟踪思考状态。"""

    def __init__(self):
        self.storage = MessageStorage()

    async def send_message(
        self, message: MessageSending, typing=False, set_reply=False, storage_message=True, show_log=True
    ):
        """
        处理、发送并存储一条消息。

        参数：
            message: MessageSending 对象，待发送的消息。
            typing: 是否模拟打字等待。

        用法：
            - typing=True 时，发送前会有打字等待。
        """
        if not message.chat_stream:
            logger.error("消息缺少 chat_stream，无法发送")
            raise ValueError("消息缺少 chat_stream，无法发送")
        if not message.message_info or not message.message_info.message_id:
            logger.error("消息缺少 message_info 或 message_id，无法发送")
            raise ValueError("消息缺少 message_info 或 message_id，无法发送")

        chat_id = message.chat_stream.stream_id
        message_id = message.message_info.message_id

        try:
            if set_reply:
                message.build_reply()
                logger.debug(f"[{chat_id}] 选择回复引用消息: {message.processed_plain_text[:20]}...")

            await message.process()

            if typing:
                typing_time = calculate_typing_time(
                    input_string=message.processed_plain_text,
                    thinking_start_time=message.thinking_start_time,
                    is_emoji=message.is_emoji,
                )
                await asyncio.sleep(typing_time)

            sent_msg = await send_message(message, show_log=show_log)
            if not sent_msg:
                return False

            if storage_message:
                await self.storage.store_message(message, message.chat_stream)

                # 修复Send API消息不入流上下文的问题
                # 将Send API发送的消息也添加到流上下文中，确保后续对话可以引用
                try:
                    # 将MessageSending转换为DatabaseMessages
                    db_message = await self._convert_to_database_message(message)
                    if db_message and message.chat_stream.context_manager:
                        message.chat_stream.context_manager.context.history_messages.append(db_message)
                        logger.debug(f"[{chat_id}] Send API消息已添加到流上下文: {message_id}")
                except Exception as context_error:
                    logger.warning(f"[{chat_id}] 将Send API消息添加到流上下文失败: {context_error}")

            return sent_msg

        except Exception as e:
            logger.error(f"[{chat_id}] 处理或存储消息 {message_id} 时出错: {e}")
            raise e

    async def _convert_to_database_message(self, message: MessageSending):
        """将MessageSending对象转换为DatabaseMessages对象

        Args:
            message: MessageSending对象

        Returns:
            DatabaseMessages: 转换后的数据库消息对象，如果转换失败则返回None
        """
        try:
            from src.common.data_models.database_data_model import DatabaseMessages

            # 构建用户信息 - Send API发送的消息，bot是发送者
            # bot_user_info 存储在 message_info.user_info 中，而不是单独的 bot_user_info 属性
            bot_user_info = message.message_info.user_info

            # 构建聊天信息
            chat_info = message.message_info
            chat_stream = message.chat_stream

            # 获取群组信息
            group_id = None
            group_name = None
            if chat_stream and chat_stream.group_info:
                group_id = chat_stream.group_info.group_id
                group_name = chat_stream.group_info.group_name

            # 创建DatabaseMessages对象
            db_message = DatabaseMessages(
                message_id=message.message_info.message_id,
                time=chat_info.time or 0.0,
                user_id=bot_user_info.user_id,
                user_nickname=bot_user_info.user_nickname,
                user_cardname=bot_user_info.user_nickname,  # 使用nickname作为cardname
                user_platform=chat_info.platform or "",
                chat_info_group_id=group_id,
                chat_info_group_name=group_name,
                chat_info_group_platform=chat_info.platform if group_id else None,
                chat_info_platform=chat_info.platform or "",
                processed_plain_text=message.processed_plain_text or "",
                display_message=message.display_message or "",
                is_read=True,  # 新消息标记为已读
                interest_value=0.5,  # 默认兴趣值
                should_reply=False,  # 自己发送的消息不需要回复
                should_act=False,   # 自己发送的消息不需要执行动作
                is_mentioned=False,  # 自己发送的消息默认不提及
            )

            return db_message

        except Exception as e:
            logger.error(f"转换MessageSending到DatabaseMessages失败: {e}", exc_info=True)
            return None
