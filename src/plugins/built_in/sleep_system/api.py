import asyncio
from . import sleep_logic_instance

def on_message_received():
    """
    当接收到用户消息时调用此函数，用于处理睡眠中断。
    """
    if sleep_logic_instance:
        asyncio.create_task(sleep_logic_instance.handle_external_event())
