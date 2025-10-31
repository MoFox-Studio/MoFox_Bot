import asyncio
from typing import Any, Callable, Optional

import websockets as Server

from src.common.logger import get_logger
from src.plugin_system.apis import config_api

logger = get_logger("napcat_adapter")


class WebSocketManager:
    """WebSocket 连接管理器，支持正向和反向连接"""

    def __init__(self):
        self.connection: Optional[Server.ServerConnection] = None
        self.server: Optional[Server.WebSocketServer] = None
        self.is_running = False
        self.reconnect_interval = 5  # 重连间隔（秒）
        self.max_reconnect_attempts = 10  # 最大重连次数
        self.plugin_config = None

    async def start_connection(
        self, message_handler: Callable[[Server.ServerConnection], Any], plugin_config: dict
    ) -> None:
        """根据配置启动 WebSocket 连接"""
        self.plugin_config = plugin_config
        mode = config_api.get_plugin_config(plugin_config, "napcat_server.mode")

        if mode == "reverse":
            await self._start_reverse_connection(message_handler)
        elif mode == "forward":
            await self._start_forward_connection(message_handler)
        else:
            raise ValueError(f"不支持的连接模式: {mode}")

    async def _start_reverse_connection(self, message_handler: Callable[[Server.ServerConnection], Any]) -> None:
        """启动反向连接（作为服务器）"""
        host = config_api.get_plugin_config(self.plugin_config, "napcat_server.host")
        port = config_api.get_plugin_config(self.plugin_config, "napcat_server.port")

        logger.info(f"正在启动反向连接模式，监听地址: ws://{host}:{port}")

        async def handle_client(websocket, path=None):
            self.connection = websocket
            logger.info(f"Napcat 客户端已连接: {websocket.remote_address}")
            try:
                await message_handler(websocket)
            except Exception as e:
                logger.error(f"处理客户端连接时出错: {e}")
            finally:
                self.connection = None
                logger.info("Napcat 客户端已断开连接")

        self.server = await Server.serve(handle_client, host, port, max_size=2**26)
        self.is_running = True
        logger.info(f"反向连接服务器已启动，监听地址: ws://{host}:{port}")

        # 保持服务器运行
        await self.server.serve_forever()

    async def _start_forward_connection(self, message_handler: Callable[[Server.ServerConnection], Any]) -> None:
        """启动正向连接（作为客户端）"""
        url = self._get_forward_url()
        logger.info(f"正在启动正向连接模式，目标地址: {url}")

        reconnect_count = 0

        while reconnect_count < self.max_reconnect_attempts:
            try:
                logger.info(f"尝试连接到 Napcat 服务器: {url}")

                # 准备连接参数
                connect_kwargs = {"max_size": 2**26}

                # 如果配置了访问令牌，添加到请求头
                access_token = config_api.get_plugin_config(self.plugin_config, "napcat_server.access_token")
                if access_token:
                    connect_kwargs["additional_headers"] = {"Authorization": f"Bearer {access_token}"}
                    logger.info("已添加访问令牌到连接请求头")

                async with Server.connect(url, **connect_kwargs) as websocket:
                    self.connection = websocket
                    self.is_running = True
                    reconnect_count = 0  # 重置重连计数

                    logger.info(f"成功连接到 Napcat 服务器: {url}")

                    try:
                        await message_handler(websocket)
                    except Server.exceptions.ConnectionClosed:
                        logger.warning("与 Napcat 服务器的连接已断开")
                    except Exception as e:
                        logger.error(f"处理正向连接时出错: {e}")
                    finally:
                        self.connection = None
                        self.is_running = False

            except (
                Server.exceptions.ConnectionClosed,
                Server.exceptions.InvalidMessage,
                OSError,
                ConnectionRefusedError,
            ) as e:
                reconnect_count += 1
                logger.warning(f"连接失败 ({reconnect_count}/{self.max_reconnect_attempts}): {e}")

                if reconnect_count < self.max_reconnect_attempts:
                    logger.info(f"将在 {self.reconnect_interval} 秒后重试连接...")
                    await asyncio.sleep(self.reconnect_interval)
                else:
                    logger.error("已达到最大重连次数，停止重连")
                    raise
            except Exception as e:
                logger.error(f"正向连接时发生未知错误: {e}")
                raise

    def _get_forward_url(self) -> str:
        """获取正向连接的 URL"""
        # 如果配置了完整的 URL，直接使用
        url = config_api.get_plugin_config(self.plugin_config, "napcat_server.url")
        if url:
            return url

        # 否则根据 host 和 port 构建 URL
        host = config_api.get_plugin_config(self.plugin_config, "napcat_server.host")
        port = config_api.get_plugin_config(self.plugin_config, "napcat_server.port")
        return f"ws://{host}:{port}"

    async def stop_connection(self) -> None:
        """停止 WebSocket 连接"""
        self.is_running = False

        if self.connection:
            try:
                await self.connection.close()
                logger.info("WebSocket 连接已关闭")
            except Exception as e:
                logger.error(f"关闭 WebSocket 连接时出错: {e}")
            finally:
                self.connection = None

        if self.server:
            try:
                self.server.close()
                await self.server.wait_closed()
                logger.info("WebSocket 服务器已关闭")
            except Exception as e:
                logger.error(f"关闭 WebSocket 服务器时出错: {e}")
            finally:
                self.server = None

    def get_connection(self) -> Optional[Server.ServerConnection]:
        """获取当前的 WebSocket 连接"""
        return self.connection

    def is_connected(self) -> bool:
        """检查是否已连接"""
        return self.connection is not None and self.is_running


# 全局 WebSocket 管理器实例
websocket_manager = WebSocketManager()
