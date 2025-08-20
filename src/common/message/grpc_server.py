import asyncio
import grpc
import json
import uuid
from typing import Dict, Optional
from datetime import datetime
import traceback

from proto.mmc_message_pb2 import Message, MessageType, ResponseStatus
from proto.mmc_message_pb2_grpc import MessageServiceServicer, add_MessageServiceServicer_to_server

from src.common.logger import get_logger
from src.config.config import global_config

logger = get_logger("grpc_server")


class MessageServiceImpl(MessageServiceServicer):
    """gRPC 消息服务实现"""
    
    def __init__(self):
        self.connected_clients: Dict[str, asyncio.Queue] = {}  # client_id -> queue
        self.platform_clients: Dict[str, str] = {}  # platform_name -> client_id
        self.message_handler = None
        
    def register_message_handler(self, handler):
        """注册消息处理器"""
        self.message_handler = handler
        
    async def MessageStream(self, request_iterator, context):
        """处理双向流式消息"""
        client_id = str(uuid.uuid4())
        logger.info(f"新的 gRPC 客户端连接: {client_id}")
        
        # 创建响应队列
        response_queue = asyncio.Queue()
        self.connected_clients[client_id] = response_queue
        
        async def handle_requests():
            """处理请求的协程"""
            try:
                async for message in request_iterator:
                    await self._handle_received_message(message, client_id, response_queue)
            except Exception as e:
                logger.error(f"处理请求时出错: {e}")
            finally:
                # 发送停止信号
                await response_queue.put(None)
        
        # 启动请求处理任务
        request_task = asyncio.create_task(handle_requests())
        
        try:
            # 生成响应流
            while True:
                response = await response_queue.get()
                if response is None:  # 停止信号
                    break
                yield response
        except Exception as e:
            logger.error(f"发送响应时出错: {e}")
        finally:
            # 清理客户端连接
            if client_id in self.connected_clients:
                del self.connected_clients[client_id]
            
            # 清理平台映射
            platform_to_remove = None
            for platform, cid in self.platform_clients.items():
                if cid == client_id:
                    platform_to_remove = platform
                    break
            if platform_to_remove:
                del self.platform_clients[platform_to_remove]
                
            if not request_task.done():
                request_task.cancel()
                try:
                    await request_task
                except asyncio.CancelledError:
                    pass
            logger.info(f"客户端 {client_id} 已断开连接")
    
    async def _handle_received_message(self, message: Message, client_id: str, response_queue: asyncio.Queue):
        """处理接收到的消息"""
        try:
            logger.debug(f"收到客户端 {client_id} 的消息: {message.message_id}, 类型: {message.type}")
            
            # 处理握手消息
            if message.type == MessageType.HANDSHAKE:
                await self._handle_handshake(message, response_queue, client_id)
                return
            
            # 处理心跳消息
            if message.type == MessageType.HEARTBEAT:
                await self._handle_heartbeat(message, response_queue)
                return
            
            # 解析消息负载
            try:
                payload = json.loads(message.payload) if message.payload else {}
            except json.JSONDecodeError:
                logger.error(f"无法解析消息负载: {message.payload}")
                await self._send_error_response(message, "Invalid JSON payload", response_queue)
                return
            
            # 如果有消息处理器，调用它
            if self.message_handler:
                try:
                    await self.message_handler(payload)
                except Exception as e:
                    logger.error(f"消息处理器处理消息时出错: {e}")
                    await self._send_error_response(message, f"Handler error: {str(e)}", response_queue)
                    return
            
            # 如果需要确认，发送确认消息
            if message.require_ack:
                ack_message = Message(
                    message_id=message.message_id,
                    type=MessageType.MESSAGE_ACK,
                    sender_platform="maibot",
                    target_platform=message.sender_platform,
                    payload=json.dumps({"status": "received"}),
                    timestamp=int(datetime.now().timestamp()),
                    status=ResponseStatus.SUCCESS
                )
                await response_queue.put(ack_message)
                
        except Exception as e:
            logger.error(f"处理消息时出错: {e}")
            logger.error(traceback.format_exc())
            await self._send_error_response(message, f"Processing error: {str(e)}", response_queue)
    
    async def _handle_handshake(self, message: Message, response_queue: asyncio.Queue, client_id: str):
        """处理握手消息"""
        platform_name = message.sender_platform
        logger.info(f"处理来自 {platform_name} 的握手请求，客户端ID: {client_id}")
        
        # 记录平台映射
        self.platform_clients[platform_name] = client_id
        
        response = Message(
            message_id=message.message_id,
            type=MessageType.HANDSHAKE,
            sender_platform="maibot",
            target_platform=message.sender_platform,
            payload=json.dumps({"status": "connected", "server": "maibot"}),
            timestamp=int(datetime.now().timestamp()),
            status=ResponseStatus.SUCCESS
        )
        
        await response_queue.put(response)
    
    async def _handle_heartbeat(self, message: Message, response_queue: asyncio.Queue):
        """处理心跳消息"""
        logger.debug(f"收到来自 {message.sender_platform} 的心跳")
        
        if message.require_ack:
            response = Message(
                message_id=message.message_id,
                type=MessageType.HEARTBEAT,
                sender_platform="maibot",
                target_platform=message.sender_platform,
                payload=json.dumps({"timestamp": int(datetime.now().timestamp())}),
                timestamp=int(datetime.now().timestamp()),
                status=ResponseStatus.SUCCESS
            )
            await response_queue.put(response)
    
    async def _send_error_response(self, original_message: Message, error_msg: str, response_queue: asyncio.Queue):
        """发送错误响应"""
        if original_message.require_ack:
            error_response = Message(
                message_id=original_message.message_id,
                type=MessageType.MESSAGE_ACK,
                sender_platform="maibot",
                target_platform=original_message.sender_platform,
                payload=json.dumps({"error": error_msg}),
                timestamp=int(datetime.now().timestamp()),
                status=ResponseStatus.FAILED,
                error_message=error_msg
            )
            await response_queue.put(error_response)
    
    async def broadcast_message(self, message_data: dict):
        """向所有连接的客户端广播消息"""
        if not self.connected_clients:
            logger.warning("没有连接的客户端，无法广播消息")
            return
        
        message = Message(
            message_id=str(uuid.uuid4()),
            type=MessageType.NORMAL_MESSAGE,
            sender_platform="maibot",
            payload=json.dumps(message_data),
            timestamp=int(datetime.now().timestamp()),
            require_ack=False
        )
        
        # 向所有客户端发送消息
        for client_id, queue in self.connected_clients.items():
            try:
                await queue.put(message)
            except Exception as e:
                logger.error(f"向客户端 {client_id} 发送消息失败: {e}")
    
    async def send_message_to_client(self, target_platform: str, message_data: dict, require_ack: bool = False) -> bool:
        """向指定平台的客户端发送消息"""
        # 查找对应平台的客户端ID
        client_id = self.platform_clients.get(target_platform)
        if not client_id:
            logger.warning(f"未找到目标平台: {target_platform}")
            return False
        
        # 检查客户端是否仍然连接
        if client_id not in self.connected_clients:
            logger.warning(f"目标平台 {target_platform} 的客户端 {client_id} 已断开连接")
            # 清理过期的平台映射
            del self.platform_clients[target_platform]
            return False
        
        message = Message(
            message_id=str(uuid.uuid4()),
            type=MessageType.NORMAL_MESSAGE,
            sender_platform="maibot",
            target_platform=target_platform,
            payload=json.dumps(message_data),
            timestamp=int(datetime.now().timestamp()),
            require_ack=require_ack
        )
        
        # 向指定客户端发送消息
        try:
            queue = self.connected_clients[client_id]
            await queue.put(message)
            logger.info(f"成功发送消息到平台 {target_platform} (客户端: {client_id})")
            return True
        except Exception as e:
            logger.error(f"向平台 {target_platform} 的客户端 {client_id} 发送消息失败: {e}")
            return False
    
    async def broadcast_message(self, message_data: dict):
        """广播消息到所有连接的客户端"""
        message = Message(
            message_id=str(uuid.uuid4()),
            type=MessageType.NORMAL_MESSAGE,
            sender_platform="maibot",
            target_platform="",
            payload=json.dumps(message_data),
            timestamp=int(datetime.now().timestamp()),
            require_ack=False
        )
        
        for client_id, queue in self.connected_clients.items():
            try:
                await queue.put(message)
            except Exception as e:
                logger.error(f"向客户端 {client_id} 广播消息失败: {e}")


class GrpcServer:
    """gRPC 服务器管理器"""
    
    def __init__(self):
        self.server: Optional[grpc.aio.Server] = None
        self.service_impl = MessageServiceImpl()
        self.is_running = False
        
    def register_message_handler(self, handler):
        """注册消息处理器"""
        self.service_impl.register_message_handler(handler)
        
    async def start(self):
        """启动 gRPC 服务器"""
        if self.is_running:
            logger.warning("gRPC 服务器已经在运行")
            return
            
        # 读取配置 - 使用固定的gRPC端口
        host = "0.0.0.0"
        port = 8090  # 固定使用8090端口作为gRPC端口
        
        logger.info(f"正在启动 gRPC 服务器，地址: {host}:{port}")
        
        # 创建 gRPC 服务器
        self.server = grpc.aio.server()
        
        # 添加服务
        add_MessageServiceServicer_to_server(self.service_impl, self.server)
        
        # 绑定端口
        listen_addr = f'{host}:{port}'
        self.server.add_insecure_port(listen_addr)
        
        # 启动服务器
        await self.server.start()
        self.is_running = True
        
        logger.info(f"gRPC 服务器已启动，监听地址: {listen_addr}")
        
        # 保持服务器运行
        try:
            await self.server.wait_for_termination()
        except Exception as e:
            logger.error(f"gRPC 服务器运行时出错: {e}")
        finally:
            self.is_running = False
    
    async def stop(self):
        """停止 gRPC 服务器"""
        if self.server and self.is_running:
            logger.info("正在停止 gRPC 服务器...")
            await self.server.stop(grace=5.0)
            self.is_running = False
            logger.info("gRPC 服务器已停止")
    
    async def send_message(self, target_platform: str, message_data: dict, require_ack: bool = False) -> bool:
        """发送消息到指定平台"""
        return await self.service_impl.send_message_to_client(target_platform, message_data, require_ack)
    
    async def broadcast_message(self, message_data: dict):
        """广播消息到所有连接的客户端"""
        await self.service_impl.broadcast_message(message_data)


# 全局 gRPC 服务器实例
_global_grpc_server: Optional[GrpcServer] = None


def get_global_grpc_server() -> GrpcServer:
    """获取全局 gRPC 服务器实例"""
    global _global_grpc_server
    if _global_grpc_server is None:
        _global_grpc_server = GrpcServer()
    return _global_grpc_server
