"""
主动思考事件处理器
监听bot的reply事件，在reply后重置对应聊天流的主动思考定时任务
"""

from src.common.logger import get_logger
from src.plugin_system import BaseEventHandler, EventType
from src.plugin_system.base.base_event import HandlerResult
from src.plugins.built_in.affinity_flow_chatter.proactive_thinking_scheduler import (
    proactive_thinking_scheduler,
)

logger = get_logger("proactive_thinking_event")


class ProactiveThinkingReplyHandler(BaseEventHandler):
    """Reply事件处理器
    
    当bot回复某个聊天流后：
    1. 如果该聊天流的主动思考被暂停（因为抛出了话题），则恢复它
    2. 无论是否暂停，都重置定时任务，重新开始计时
    """
    
    handler_name: str = "proactive_thinking_reply_handler"
    handler_description: str = "监听reply事件，重置主动思考定时任务"
    init_subscribe: list[EventType | str] = [EventType.AFTER_SEND]
    
    async def execute(self, kwargs: dict | None) -> HandlerResult:
        """处理reply事件
        
        Args:
            kwargs: 事件参数，应包含 stream_id
            
        Returns:
            HandlerResult: 处理结果
        """
        logger.info("[主动思考事件] ProactiveThinkingReplyHandler 开始执行")
        logger.info(f"[主动思考事件] 接收到的参数: {kwargs}")
        
        if not kwargs:
            logger.warning("[主动思考事件] kwargs 为空，跳过处理")
            return HandlerResult(success=True, continue_process=True, message=None)
        
        stream_id = kwargs.get("stream_id")
        if not stream_id:
            logger.warning(f"[主动思考事件] Reply事件缺少stream_id参数，kwargs={kwargs}")
            return HandlerResult(success=True, continue_process=True, message=None)
        
        logger.info(f"[主动思考事件] 收到 AFTER_SEND 事件，stream_id={stream_id}")
        
        try:
            from src.config.config import global_config
            
            # 检查是否启用reply重置
            logger.info(f"[主动思考事件] reply_reset_enabled={global_config.proactive_thinking.reply_reset_enabled}")
            if not global_config.proactive_thinking.reply_reset_enabled:
                logger.info(f"[主动思考事件] reply_reset_enabled 为 False，跳过重置")
                return HandlerResult(success=True, continue_process=True, message=None)
            
            # 检查是否被暂停
            was_paused = await proactive_thinking_scheduler.is_paused(stream_id)
            logger.info(f"[主动思考事件] 聊天流 {stream_id} 暂停状态: {was_paused}")
            
            if was_paused:
                logger.info(f"[主动思考事件] 检测到reply事件，聊天流 {stream_id} 之前因抛出话题而暂停，现在恢复")
            
            # 重置定时任务（这会自动清除暂停标记并创建新任务）
            logger.info(f"[主动思考事件] 准备调用 schedule_proactive_thinking，stream_id={stream_id}")
            success = await proactive_thinking_scheduler.schedule_proactive_thinking(stream_id)
            logger.info(f"[主动思考事件] schedule_proactive_thinking 调用完成，success={success}")
            
            if success:
                if was_paused:
                    logger.info(f"[主动思考事件] ✅ 聊天流 {stream_id} 的主动思考已恢复并重置")
                else:
                    logger.info(f"[主动思考事件] ✅ 聊天流 {stream_id} 的主动思考定时任务已重置")
            else:
                logger.warning(f"[主动思考事件] ❌ 重置聊天流 {stream_id} 的主动思考任务失败")
            
        except Exception as e:
            logger.error(f"[主动思考事件] ❌ 处理reply事件时出错: {e}", exc_info=True)
        
        # 总是继续处理其他handler
        return HandlerResult(success=True, continue_process=True, message=None)


class ProactiveThinkingMessageHandler(BaseEventHandler):
    """消息事件处理器
    
    当收到消息时，如果该聊天流还没有主动思考任务，则创建一个
    这样可以确保新的聊天流也能获得主动思考功能
    """
    
    handler_name: str = "proactive_thinking_message_handler"
    handler_description: str = "监听消息事件，为新聊天流创建主动思考任务"
    init_subscribe: list[EventType | str] = [EventType.ON_MESSAGE]
    
    async def execute(self, kwargs: dict | None) -> HandlerResult:
        """处理消息事件
        
        Args:
            kwargs: 事件参数，格式为 {"message": MessageRecv}
            
        Returns:
            HandlerResult: 处理结果
        """
        if not kwargs:
            return HandlerResult(success=True, continue_process=True, message=None)
        
        # 从 kwargs 中获取 MessageRecv 对象
        message = kwargs.get("message")
        if not message or not hasattr(message, "chat_stream"):
            return HandlerResult(success=True, continue_process=True, message=None)
        
        # 从 chat_stream 获取 stream_id
        chat_stream = message.chat_stream
        if not chat_stream or not hasattr(chat_stream, "stream_id"):
            return HandlerResult(success=True, continue_process=True, message=None)
        
        stream_id = chat_stream.stream_id
        
        try:
            from src.config.config import global_config
            
            # 检查是否启用主动思考
            if not global_config.proactive_thinking.enable:
                return HandlerResult(success=True, continue_process=True, message=None)
            
            # 检查该聊天流是否已经有任务
            task_info = await proactive_thinking_scheduler.get_task_info(stream_id)
            if task_info:
                # 已经有任务，不需要创建
                return HandlerResult(success=True, continue_process=True, message=None)
            
            # 从 message_info 获取平台和聊天ID信息
            message_info = message.message_info
            platform = message_info.platform
            is_group = message_info.group_info is not None
            chat_id = message_info.group_info.group_id if is_group else message_info.user_info.user_id  # type: ignore
            
            # 构造配置字符串
            stream_config = f"{platform}:{chat_id}:{'group' if is_group else 'private'}"
            
            # 检查黑白名单
            if not proactive_thinking_scheduler._check_whitelist_blacklist(stream_config):
                return HandlerResult(success=True, continue_process=True, message=None)
            
            # 创建主动思考任务
            success = await proactive_thinking_scheduler.schedule_proactive_thinking(stream_id)
            if success:
                logger.info(f"为新聊天流 {stream_id} 创建了主动思考任务")
            
        except Exception as e:
            logger.error(f"处理消息事件时出错: {e}", exc_info=True)
        
        # 总是继续处理其他handler
        return HandlerResult(success=True, continue_process=True, message=None)
