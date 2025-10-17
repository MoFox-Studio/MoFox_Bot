from src.common.logger import get_logger

# from ..hfc_context import HfcContext

logger = get_logger("notification_sender")


class NotificationSender:
    @staticmethod
    async def send_goodnight_notification(context):  # type: ignore
        """发送晚安通知"""
        # try:
        # from ..proactive.events import ProactiveTriggerEvent
        # from ..proactive.proactive_thinker import ProactiveThinker

        # event = ProactiveTriggerEvent(source="sleep_manager", reason="goodnight")
        # proactive_thinker = ProactiveThinker(context, context.chat_instance.cycle_processor)
        # await proactive_thinker.think(event)
        # except Exception as e:
        # logger.error(f"发送晚安通知失败: {e}")

    @staticmethod
    async def send_insomnia_notification(context, reason: str):  # type: ignore
        """发送失眠通知"""
        # try:
        # from ..proactive.events import ProactiveTriggerEvent
        # from ..proactive.proactive_thinker import ProactiveThinker

        # event = ProactiveTriggerEvent(source="sleep_manager", reason=reason)
        # proactive_thinker = ProactiveThinker(context, context.chat_instance.cycle_processor)
        # await proactive_thinker.think(event)
        # except Exception as e:
        # logger.error(f"发送失眠通知失败: {e}")
