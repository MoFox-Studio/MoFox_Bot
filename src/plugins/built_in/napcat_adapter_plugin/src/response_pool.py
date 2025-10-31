import asyncio
import time
from typing import Dict

from src.common.logger import get_logger
from src.plugin_system.apis import config_api

logger = get_logger("napcat_adapter")

response_dict: Dict = {}
response_time_dict: Dict = {}
plugin_config = None


def set_plugin_config(config: dict):
    """设置插件配置"""
    global plugin_config
    plugin_config = config


async def get_response(request_id: str, timeout: int = 10) -> dict:
    response = await asyncio.wait_for(_get_response(request_id), timeout)
    _ = response_time_dict.pop(request_id)
    logger.debug(f"响应信息id: {request_id} 已从响应字典中取出")
    return response


async def _get_response(request_id: str) -> dict:
    """
    内部使用的获取响应函数，主要用于在需要时获取响应
    """
    while request_id not in response_dict:
        await asyncio.sleep(0.2)
    return response_dict.pop(request_id)


async def put_response(response: dict):
    echo_id = response.get("echo")
    now_time = time.time()
    response_dict[echo_id] = response
    response_time_dict[echo_id] = now_time
    logger.debug(f"响应信息id: {echo_id} 已存入响应字典")


async def check_timeout_response() -> None:
    while True:
        cleaned_message_count: int = 0
        now_time = time.time()

        # 获取心跳间隔配置
        heartbeat_interval = 30  # 默认值
        if plugin_config:
            heartbeat_interval = config_api.get_plugin_config(plugin_config, "napcat_server.heartbeat_interval", 30)

        for echo_id, response_time in list(response_time_dict.items()):
            if now_time - response_time > heartbeat_interval:
                cleaned_message_count += 1
                response_dict.pop(echo_id)
                response_time_dict.pop(echo_id)
                logger.warning(f"响应消息 {echo_id} 超时，已删除")
        if cleaned_message_count > 0:
            logger.info(f"已删除 {cleaned_message_count} 条超时响应消息")
        await asyncio.sleep(heartbeat_interval)
