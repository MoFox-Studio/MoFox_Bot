"""
按聊天流分配消费者的消息路由系统

核心思想：
- 为每个活跃的聊天流（stream_id）创建独立的消息队列和消费者协程
- 同一聊天流的消息由同一个 worker 处理，保证顺序性
- 不同聊天流的消息并发处理，提高吞吐量
- 动态管理流的生命周期，自动清理不活跃的流
"""

import asyncio
import time
from typing import Dict, Optional

from src.common.logger import get_logger

logger = get_logger("stream_router")


class StreamConsumer:
    """单个聊天流的消息消费者
    
    维护独立的消息队列和处理协程
    """
    
    def __init__(self, stream_id: str, queue_maxsize: int = 100):
        self.stream_id = stream_id
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=queue_maxsize)
        self.worker_task: Optional[asyncio.Task] = None
        self.last_active_time = time.time()
        self.is_running = False
        
        # 性能统计
        self.stats = {
            "total_messages": 0,
            "total_processing_time": 0.0,
            "queue_overflow_count": 0,
        }
    
    async def start(self) -> None:
        """启动消费者"""
        if not self.is_running:
            self.is_running = True
            self.worker_task = asyncio.create_task(self._process_loop())
            logger.debug(f"Stream Consumer 启动: {self.stream_id}")
    
    async def stop(self) -> None:
        """停止消费者"""
        self.is_running = False
        if self.worker_task:
            self.worker_task.cancel()
            try:
                await self.worker_task
            except asyncio.CancelledError:
                pass
        logger.debug(f"Stream Consumer 停止: {self.stream_id}")
    
    async def enqueue(self, message: dict) -> None:
        """将消息加入队列"""
        self.last_active_time = time.time()
        
        try:
            # 使用 put_nowait 避免阻塞路由器
            self.queue.put_nowait(message)
        except asyncio.QueueFull:
            self.stats["queue_overflow_count"] += 1
            logger.warning(
                f"Stream {self.stream_id} 队列已满 "
                f"({self.queue.qsize()}/{self.queue.maxsize})，"
                f"消息被丢弃！溢出次数: {self.stats['queue_overflow_count']}"
            )
            # 可选策略：丢弃最旧的消息
            # try:
            #     self.queue.get_nowait()
            #     self.queue.put_nowait(message)
            #     logger.debug(f"Stream {self.stream_id} 丢弃最旧消息，添加新消息")
            # except asyncio.QueueEmpty:
            #     pass
    
    async def _process_loop(self) -> None:
        """消息处理循环"""
        # 延迟导入，避免循环依赖
        from .recv_handler.message_handler import message_handler
        from .recv_handler.meta_event_handler import meta_event_handler
        from .recv_handler.notice_handler import notice_handler
        
        logger.info(f"Stream {self.stream_id} 处理循环启动")
        
        try:
            while self.is_running:
                try:
                    # 等待消息，1秒超时
                    message = await asyncio.wait_for(
                        self.queue.get(),
                        timeout=1.0
                    )
                    
                    start_time = time.time()
                    
                    # 处理消息
                    post_type = message.get("post_type")
                    if post_type == "message":
                        await message_handler.handle_raw_message(message)
                    elif post_type == "meta_event":
                        await meta_event_handler.handle_meta_event(message)
                    elif post_type == "notice":
                        await notice_handler.handle_notice(message)
                    else:
                        logger.warning(f"未知的 post_type: {post_type}")
                    
                    processing_time = time.time() - start_time
                    
                    # 更新统计
                    self.stats["total_messages"] += 1
                    self.stats["total_processing_time"] += processing_time
                    self.last_active_time = time.time()
                    self.queue.task_done()
                    
                    # 性能监控（每100条消息输出一次）
                    if self.stats["total_messages"] % 100 == 0:
                        avg_time = self.stats["total_processing_time"] / self.stats["total_messages"]
                        logger.info(
                            f"Stream {self.stream_id[:30]}... 统计: "
                            f"消息数={self.stats['total_messages']}, "
                            f"平均耗时={avg_time:.3f}秒, "
                            f"队列长度={self.queue.qsize()}"
                        )
                    
                    # 动态延迟：队列空时短暂休眠
                    if self.queue.qsize() == 0:
                        await asyncio.sleep(0.01)
                
                except asyncio.TimeoutError:
                    # 超时是正常的，继续循环
                    continue
                except asyncio.CancelledError:
                    logger.info(f"Stream {self.stream_id} 处理循环被取消")
                    break
                except Exception as e:
                    logger.error(f"Stream {self.stream_id} 处理消息时出错: {e}", exc_info=True)
                    # 继续处理下一条消息
                    await asyncio.sleep(0.1)
        
        finally:
            logger.info(f"Stream {self.stream_id} 处理循环结束")
    
    def get_stats(self) -> dict:
        """获取性能统计"""
        avg_time = (
            self.stats["total_processing_time"] / self.stats["total_messages"]
            if self.stats["total_messages"] > 0
            else 0
        )
        
        return {
            "stream_id": self.stream_id,
            "queue_size": self.queue.qsize(),
            "total_messages": self.stats["total_messages"],
            "avg_processing_time": avg_time,
            "queue_overflow_count": self.stats["queue_overflow_count"],
            "last_active_time": self.last_active_time,
        }


class StreamRouter:
    """流路由器
    
    负责将消息路由到对应的聊天流队列
    动态管理聊天流的生命周期
    """
    
    def __init__(
        self,
        max_streams: int = 500,
        stream_timeout: int = 600,
        stream_queue_size: int = 100,
        cleanup_interval: int = 60,
    ):
        self.streams: Dict[str, StreamConsumer] = {}
        self.lock = asyncio.Lock()
        self.max_streams = max_streams
        self.stream_timeout = stream_timeout
        self.stream_queue_size = stream_queue_size
        self.cleanup_interval = cleanup_interval
        self.cleanup_task: Optional[asyncio.Task] = None
        self.is_running = False
    
    async def start(self) -> None:
        """启动路由器"""
        if not self.is_running:
            self.is_running = True
            self.cleanup_task = asyncio.create_task(self._cleanup_loop())
            logger.info(
                f"StreamRouter 已启动 - "
                f"最大流数: {self.max_streams}, "
                f"超时: {self.stream_timeout}秒, "
                f"队列大小: {self.stream_queue_size}"
            )
    
    async def stop(self) -> None:
        """停止路由器"""
        self.is_running = False
        
        if self.cleanup_task:
            self.cleanup_task.cancel()
            try:
                await self.cleanup_task
            except asyncio.CancelledError:
                pass
        
        # 停止所有流消费者
        logger.info(f"正在停止 {len(self.streams)} 个流消费者...")
        for consumer in self.streams.values():
            await consumer.stop()
        
        self.streams.clear()
        logger.info("StreamRouter 已停止")
    
    async def route_message(self, message: dict) -> None:
        """路由消息到对应的流"""
        stream_id = self._extract_stream_id(message)
        
        # 快速路径：流已存在
        if stream_id in self.streams:
            await self.streams[stream_id].enqueue(message)
            return
        
        # 慢路径：需要创建新流
        async with self.lock:
            # 双重检查
            if stream_id not in self.streams:
                # 检查流数量限制
                if len(self.streams) >= self.max_streams:
                    logger.warning(
                        f"达到最大流数量限制 ({self.max_streams})，"
                        f"尝试清理不活跃的流..."
                    )
                    await self._cleanup_inactive_streams()
                    
                    # 清理后仍然超限，记录警告但继续创建
                    if len(self.streams) >= self.max_streams:
                        logger.error(
                            f"清理后仍达到最大流数量 ({len(self.streams)}/{self.max_streams})！"
                        )
                
                # 创建新流
                consumer = StreamConsumer(stream_id, self.stream_queue_size)
                self.streams[stream_id] = consumer
                await consumer.start()
                logger.info(f"创建新的 Stream Consumer: {stream_id} (总流数: {len(self.streams)})")
        
        await self.streams[stream_id].enqueue(message)
    
    def _extract_stream_id(self, message: dict) -> str:
        """从消息中提取 stream_id
        
        返回格式: platform:id:type
        例如: qq:123456:group 或 qq:789012:private
        """
        post_type = message.get("post_type")
        
        # 非消息类型，使用默认流（避免创建过多流）
        if post_type not in ["message", "notice"]:
            return "system:meta_event"
        
        # 消息类型
        if post_type == "message":
            message_type = message.get("message_type")
            if message_type == "group":
                group_id = message.get("group_id")
                return f"qq:{group_id}:group"
            elif message_type == "private":
                user_id = message.get("user_id")
                return f"qq:{user_id}:private"
        
        # notice 类型
        elif post_type == "notice":
            group_id = message.get("group_id")
            if group_id:
                return f"qq:{group_id}:group"
            user_id = message.get("user_id")
            if user_id:
                return f"qq:{user_id}:private"
        
        # 未知类型，使用通用流
        return "unknown:unknown"
    
    async def _cleanup_inactive_streams(self) -> None:
        """清理不活跃的流"""
        current_time = time.time()
        to_remove = []
        
        for stream_id, consumer in self.streams.items():
            if current_time - consumer.last_active_time > self.stream_timeout:
                to_remove.append(stream_id)
        
        for stream_id in to_remove:
            await self.streams[stream_id].stop()
            del self.streams[stream_id]
            logger.debug(f"清理不活跃的流: {stream_id}")
        
        if to_remove:
            logger.info(
                f"清理了 {len(to_remove)} 个不活跃的流 "
                f"(当前活跃流: {len(self.streams)}/{self.max_streams})"
            )
    
    async def _cleanup_loop(self) -> None:
        """定期清理循环"""
        logger.info(f"清理循环已启动，间隔: {self.cleanup_interval}秒")
        try:
            while self.is_running:
                await asyncio.sleep(self.cleanup_interval)
                await self._cleanup_inactive_streams()
        except asyncio.CancelledError:
            logger.info("清理循环已停止")
    
    def get_all_stats(self) -> list[dict]:
        """获取所有流的统计信息"""
        return [consumer.get_stats() for consumer in self.streams.values()]
    
    def get_summary(self) -> dict:
        """获取路由器摘要"""
        total_messages = sum(c.stats["total_messages"] for c in self.streams.values())
        total_queue_size = sum(c.queue.qsize() for c in self.streams.values())
        total_overflows = sum(c.stats["queue_overflow_count"] for c in self.streams.values())
        
        # 计算平均队列长度
        avg_queue_size = total_queue_size / len(self.streams) if self.streams else 0
        
        # 找出最繁忙的流
        busiest_stream = None
        if self.streams:
            busiest_stream = max(
                self.streams.values(),
                key=lambda c: c.stats["total_messages"]
            ).stream_id
        
        return {
            "total_streams": len(self.streams),
            "max_streams": self.max_streams,
            "total_messages_processed": total_messages,
            "total_queue_size": total_queue_size,
            "avg_queue_size": avg_queue_size,
            "total_queue_overflows": total_overflows,
            "busiest_stream": busiest_stream,
        }


# 全局路由器实例
stream_router = StreamRouter()
