import asyncio
import time

from src.common.logger import get_logger
from src.plugin_system.apis import config_api

from . import MetaEventType

logger = get_logger("napcat_adapter")


class MetaEventHandler:
    """
    处理Meta事件
    """

    def __init__(self):
        self.interval = 5.0  # 默认值，稍后通过set_plugin_config设置
        self._interval_checking = False
        self.plugin_config = None

    def set_plugin_config(self, plugin_config: dict):
        """设置插件配置"""
        self.plugin_config = plugin_config
        # 更新interval值
        self.interval = (
            config_api.get_plugin_config(self.plugin_config, "napcat_server.heartbeat_interval", 5000) / 1000
        )

    async def handle_meta_event(self, message: dict) -> None:
        event_type = message.get("meta_event_type")
        if event_type == MetaEventType.lifecycle:
            sub_type = message.get("sub_type")
            if sub_type == MetaEventType.Lifecycle.connect:
                self_id = message.get("self_id")
                self.last_heart_beat = time.time()
                logger.info(f"Bot {self_id} 连接成功")
                # 不在连接时立即启动心跳检查，等第一个心跳包到达后再启动
        elif event_type == MetaEventType.heartbeat:
            if message["status"].get("online") and message["status"].get("good"):
                self_id = message.get("self_id")
                if not self._interval_checking and self_id:
                    # 第一次收到心跳包时才启动心跳检查
                    asyncio.create_task(self.check_heartbeat(self_id))
                self.last_heart_beat = time.time()
                interval = message.get("interval")
                if interval:
                    self.interval = interval / 1000
            else:
                self_id = message.get("self_id")
                logger.warning(f"Bot {self_id} Napcat 端异常！")

    async def check_heartbeat(self, id: int) -> None:
        self._interval_checking = True
        while True:
            now_time = time.time()
            if now_time - self.last_heart_beat > self.interval * 2:
                logger.error(f"Bot {id} 可能发生了连接断开，被下线，或者Napcat卡死！")
                break
            else:
                logger.debug("心跳正常")
            await asyncio.sleep(self.interval)


meta_event_handler = MetaEventHandler()
