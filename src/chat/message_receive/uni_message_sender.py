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
                logger.info(f"[发送完成] 准备触发 AFTER_SEND 事件，stream_id={message.chat_stream.stream_id}")

                # 使用 asyncio.create_task 来异步触发事件，避免阻塞
                async def trigger_event_async():
                    try:
                        logger.info("[事件触发] 开始异步触发 AFTER_SEND 事件")
                        await event_manager.trigger_event(
                            EventType.AFTER_SEND,
                            permission_group="SYSTEM",
                            stream_id=message.chat_stream.stream_id,
                            message=message,
                        )
                        logger.info("[事件触发] AFTER_SEND 事件触发完成")
                    except Exception as e:
                        logger.error(f"[事件触发] 异步触发事件失败: {e}", exc_info=True)

                # 创建异步任务，不等待完成
                asyncio.create_task(trigger_event_async())
                logger.info("[发送完成] AFTER_SEND 事件已提交到异步任务")
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

            return sent_msg

        except Exception as e:
            logger.error(f"[{chat_id}] 处理或存储消息 {message_id} 时出错: {e}")
            raise e
