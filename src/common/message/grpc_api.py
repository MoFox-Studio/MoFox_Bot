from src.common.server import get_global_server
import os
import importlib.metadata
from src.common.logger import get_logger
from src.config.config import global_config
from src.common.message.grpc_server import get_global_grpc_server, GrpcServer


class MessageHandler:
    """消息处理器包装类，用于兼容性"""
    
    def __init__(self):
        self.grpc_server = get_global_grpc_server()
        
    def register_message_handler(self, handler):
        """注册消息处理器"""
        self.grpc_server.register_message_handler(handler)
        
    async def run(self):
        """启动服务器"""
        await self.grpc_server.start()
        
    async def send_message(self, target_platform: str, message_data: dict, require_ack: bool = False) -> bool:
        """发送消息"""
        return await self.grpc_server.send_message(target_platform, message_data, require_ack)
        
    async def broadcast_message(self, message_data: dict):
        """广播消息"""
        await self.grpc_server.broadcast_message(message_data)


# 为了保持兼容性，创建一个兼容的 MessageServer 类
class MessageServer(MessageHandler):
    """兼容的 MessageServer 类"""
    
    async def run(self):
        """启动 gRPC 服务器"""
        await self.grpc_server.start()


global_api = None


def get_global_api() -> MessageServer:
    """获取全局 gRPC 服务器实例"""
    global global_api
    if global_api is None:
        global_api = MessageServer()
    return global_api
