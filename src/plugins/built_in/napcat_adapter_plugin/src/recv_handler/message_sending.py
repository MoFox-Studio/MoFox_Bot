import asyncio

from maim_message import MessageBase, Router

from src.common.logger import get_logger
from src.plugin_system.apis import config_api

from ..message_chunker import chunker

logger = get_logger("napcat_adapter")


class MessageSending:
    """
    负责把消息发送到麦麦
    """

    maibot_router: Router = None
    plugin_config = None
    _connection_retries = 0
    _max_retries = 3

    def __init__(self):
        pass

    def set_plugin_config(self, plugin_config: dict):
        """设置插件配置"""
        self.plugin_config = plugin_config

    async def _attempt_reconnect(self):
        """尝试重新连接MoFox-Bot router"""
        if self._connection_retries < self._max_retries:
            self._connection_retries += 1
            logger.warning(f"尝试重新连接MoFox-Bot router (第{self._connection_retries}次)")
            try:
                # 重新导入router
                from ..mmc_com_layer import router

                self.maibot_router = router
                if self.maibot_router is not None:
                    logger.info("MoFox-Bot router重连成功")
                    self._connection_retries = 0  # 重置重试计数
                    return True
            except Exception as e:
                logger.error(f"重连失败: {e}")
        else:
            logger.error(f"已达到最大重连次数({self._max_retries})，停止重试")
        return False

    async def message_send(self, message_base: MessageBase) -> bool:
        """
        发送消息（Ada -> MMC 方向，需要实现切片）
        Parameters:
            message_base: MessageBase: 消息基类，包含发送目标和消息内容等信息
        """
        try:
            # 检查maibot_router是否已初始化
            if self.maibot_router is None:
                logger.warning("MoFox-Bot router未初始化，尝试重新连接")
                if not await self._attempt_reconnect():
                    logger.error("MoFox-Bot router重连失败，无法发送消息")
                    logger.error("请检查与MoFox-Bot之间的连接")
                    return False
            # 检查是否需要切片发送
            message_dict = message_base.to_dict()

            if chunker.should_chunk_message(message_dict):
                logger.info("消息过大，进行切片发送到 MoFox-Bot")

                # 切片消息
                chunks = chunker.chunk_message(message_dict)

                # 逐个发送切片
                for i, chunk in enumerate(chunks):
                    logger.debug(f"发送切片 {i + 1}/{len(chunks)} 到 MoFox-Bot")

                    # 获取对应的客户端并发送切片
                    platform = message_base.message_info.platform

                    # 再次检查router状态（防止运行时被重置）
                    if self.maibot_router is None or not hasattr(self.maibot_router, "clients"):
                        logger.warning("MoFox-Bot router连接已断开，尝试重新连接")
                        if not await self._attempt_reconnect():
                            logger.error("MoFox-Bot router重连失败，切片发送中止")
                            return False

                    if platform not in self.maibot_router.clients:
                        logger.error(f"平台 {platform} 未连接")
                        return False

                    client = self.maibot_router.clients[platform]
                    send_status = await client.send_message(chunk)

                    if not send_status:
                        logger.error(f"发送切片 {i + 1}/{len(chunks)} 失败")
                        return False

                    # 使用配置中的延迟时间
                    if i < len(chunks) - 1 and self.plugin_config:
                        delay_ms = config_api.get_plugin_config(self.plugin_config, "slicing.delay_ms", 10)
                        delay_seconds = delay_ms / 1000.0
                        logger.debug(f"切片发送延迟: {delay_ms}毫秒")
                        await asyncio.sleep(delay_seconds)

                logger.debug("所有切片发送完成")
                return True
            else:
                # 直接发送小消息
                send_status = await self.maibot_router.send_message(message_base)
                if not send_status:
                    raise RuntimeError("可能是路由未正确配置或连接异常")
                return send_status

        except Exception as e:
            logger.error(f"发送消息失败: {str(e)}")
            logger.error("请检查与MoFox-Bot之间的连接")
            return False


message_send_instance = MessageSending()
