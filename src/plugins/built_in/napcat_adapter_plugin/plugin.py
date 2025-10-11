import asyncio
import json
import inspect
import websockets as Server
from . import event_types, CONSTS, event_handlers

from typing import List

from src.plugin_system import BasePlugin, BaseEventHandler, register_plugin, EventType, ConfigField
from src.plugin_system.core.event_manager import event_manager
from src.plugin_system.apis import config_api

from src.common.logger import get_logger

from .src.message_chunker import chunker, reassembler
from .src.recv_handler.message_handler import message_handler
from .src.recv_handler.meta_event_handler import meta_event_handler
from .src.recv_handler.notice_handler import notice_handler
from .src.recv_handler.message_sending import message_send_instance
from .src.send_handler import send_handler
from .src.mmc_com_layer import mmc_start_com, router, mmc_stop_com
from .src.response_pool import put_response, check_timeout_response
from .src.websocket_manager import websocket_manager

logger = get_logger("napcat_adapter")

message_queue = asyncio.Queue()


def get_classes_in_module(module):
    classes = []
    for _name, member in inspect.getmembers(module):
        if inspect.isclass(member):
            classes.append(member)
    return classes


async def message_recv(server_connection: Server.ServerConnection):
    await message_handler.set_server_connection(server_connection)
    asyncio.create_task(notice_handler.set_server_connection(server_connection))
    await send_handler.set_server_connection(server_connection)
    async for raw_message in server_connection:
        message_str = ""
        try:
            # 确保消息是字符串
            if isinstance(raw_message, bytes):
                message_str = raw_message.decode('utf-8', errors='ignore')
            elif isinstance(raw_message, str):
                message_str = raw_message
            else:
                logger.warning(f"接收到未知类型的消息: {type(raw_message)}")
                continue

            # 只在debug模式下记录原始消息
            if logger.level <= 10:  # DEBUG level
                logger.debug(f"{message_str[:1500]}..." if (len(message_str) > 1500) else message_str)

            # 解析消息
            decoded_raw_message: dict = json.loads(message_str)

            # 检查是否是切片消息 (来自 MMC)
            if chunker.is_chunk_message(decoded_raw_message):
                logger.debug("接收到切片消息，尝试重组")
                # 尝试重组消息
                reassembled_message = await reassembler.add_chunk(decoded_raw_message)
                if reassembled_message:
                    # 重组完成，处理完整消息
                    logger.debug("消息重组完成，处理完整消息")
                    decoded_raw_message = reassembled_message
                else:
                    # 切片尚未完整，继续等待更多切片
                    logger.debug("等待更多切片...")
                    continue

            # 处理完整消息（可能是重组后的，也可能是原本就完整的）
            post_type = decoded_raw_message.get("post_type")
            if post_type in ["meta_event", "message", "notice"]:
                await message_queue.put(decoded_raw_message)
            elif post_type is None:
                await put_response(decoded_raw_message)

        except json.JSONDecodeError as e:
            logger.error(f"消息解析失败: {e}")
            logger.debug(f"原始消息: {message_str[:500]}...")
        except Exception as e:
            logger.error(f"处理消息时出错: {e}")
            logger.debug(f"原始消息: {message_str[:500]}...")


async def message_process():
    """消息处理主循环"""
    logger.info("消息处理器已启动")
    try:
        while True:
            try:
                # 使用超时等待，以便能够响应取消请求
                message = await asyncio.wait_for(message_queue.get(), timeout=1.0)

                post_type = message.get("post_type")
                if post_type == "message":
                    await message_handler.handle_raw_message(message)
                elif post_type == "meta_event":
                    await meta_event_handler.handle_meta_event(message)
                elif post_type == "notice":
                    await notice_handler.handle_notice(message)
                else:
                    logger.warning(f"未知的post_type: {post_type}")

                message_queue.task_done()
                await asyncio.sleep(0.05)

            except asyncio.TimeoutError:
                # 超时是正常的，继续循环
                continue
            except asyncio.CancelledError:
                logger.info("消息处理器收到取消信号")
                break
            except Exception as e:
                logger.error(f"处理消息时出错: {e}")
                # 即使出错也标记任务完成，避免队列阻塞
                try:
                    message_queue.task_done()
                except ValueError:
                    pass
                await asyncio.sleep(0.1)

    except asyncio.CancelledError:
        logger.info("消息处理器已停止")
        raise
    except Exception as e:
        logger.error(f"消息处理器异常: {e}")
        raise
    finally:
        logger.info("消息处理器正在清理...")
        # 清空剩余的队列项目
        try:
            while not message_queue.empty():
                try:
                    message_queue.get_nowait()
                    message_queue.task_done()
                except asyncio.QueueEmpty:
                    break
        except Exception as e:
            logger.debug(f"清理消息队列时出错: {e}")


async def napcat_server(plugin_config: dict):
    """启动 Napcat WebSocket 连接（支持正向和反向连接）"""
    # 使用插件系统配置API获取配置
    mode = config_api.get_plugin_config(plugin_config, "napcat_server.mode")
    logger.info(f"正在启动 adapter，连接模式: {mode}")

    try:
        await websocket_manager.start_connection(message_recv, plugin_config)
    except Exception as e:
        logger.error(f"启动 WebSocket 连接失败: {e}")
        raise


async def graceful_shutdown():
    """优雅关闭所有组件"""
    try:
        logger.info("正在关闭adapter...")

        # 停止消息重组器的清理任务
        try:
            await reassembler.stop_cleanup_task()
        except Exception as e:
            logger.warning(f"停止消息重组器清理任务时出错: {e}")

        # 停止功能管理器文件监控（已迁移到插件系统配置，无需操作）

        # 关闭消息处理器（包括消息缓冲器）
        try:
            await message_handler.shutdown()
        except Exception as e:
            logger.warning(f"关闭消息处理器时出错: {e}")

        # 关闭 WebSocket 连接
        try:
            await websocket_manager.stop_connection()
        except Exception as e:
            logger.warning(f"关闭WebSocket连接时出错: {e}")

        # 关闭 MoFox-Bot 连接
        try:
            await mmc_stop_com()
        except Exception as e:
            logger.warning(f"关闭MoFox-Bot连接时出错: {e}")

        # 取消所有剩余任务
        current_task = asyncio.current_task()
        tasks = [t for t in asyncio.all_tasks() if t is not current_task and not t.done()]

        if tasks:
            logger.info(f"正在取消 {len(tasks)} 个剩余任务...")
            for task in tasks:
                task.cancel()

            # 等待任务取消完成，忽略 CancelledError
            try:
                await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=10)
            except asyncio.TimeoutError:
                logger.warning("部分任务取消超时")
            except Exception as e:
                logger.debug(f"任务取消过程中的异常（可忽略）: {e}")

        logger.info("Adapter已成功关闭")

    except Exception as e:
        logger.error(f"Adapter关闭中出现错误: {e}")
    finally:
        # 确保消息队列被清空
        try:
            while not message_queue.empty():
                try:
                    message_queue.get_nowait()
                    message_queue.task_done()
                except asyncio.QueueEmpty:
                    break
        except Exception:
            pass


class LauchNapcatAdapterHandler(BaseEventHandler):
    """自动启动Adapter"""

    handler_name: str = "launch_napcat_adapter_handler"
    handler_description: str = "自动启动napcat adapter"
    weight: int = 100
    intercept_message: bool = False
    init_subscribe = [EventType.ON_START]

    async def execute(self, kwargs):
        # 启动消息重组器的清理任务
        logger.info("启动消息重组器...")
        await reassembler.start_cleanup_task()

        logger.info("开始启动Napcat Adapter")

        # 创建单独的异步任务，防止阻塞主线程
        asyncio.create_task(self._start_mofox_bot_connection())
        asyncio.create_task(napcat_server(self.plugin_config))
        asyncio.create_task(message_process())
        asyncio.create_task(check_timeout_response())

    async def _start_mofox_bot_connection(self):
        """非阻塞方式启动MoFox-Bot连接，等待主服务启动后再连接"""
        # 等待一段时间让MoFox-Bot主服务完全启动
        await asyncio.sleep(5)

        max_attempts = 10
        attempt = 0

        while attempt < max_attempts:
            try:
                logger.info(f"尝试连接MoFox-Bot (第{attempt + 1}次)")
                await mmc_start_com(self.plugin_config)
                setattr(message_send_instance, 'mofox_bot_router', router)
                logger.info("MoFox-Bot router连接已建立")
                return
            except Exception as e:
                attempt += 1
                if attempt >= max_attempts:
                    logger.error(f"MoFox-Bot连接失败，已达到最大重试次数: {e}")
                    return
                else:
                    delay = min(2 + attempt, 10)  # 逐渐增加延迟，最大10秒
                    logger.warning(f"MoFox-Bot连接失败: {e}，{delay}秒后重试")
                    await asyncio.sleep(delay)


class StopNapcatAdapterHandler(BaseEventHandler):
    """关闭Adapter"""

    handler_name: str = "stop_napcat_adapter_handler"
    handler_description: str = "关闭napcat adapter"
    weight: int = 100
    intercept_message: bool = False
    init_subscribe = [EventType.ON_STOP]

    async def execute(self, kwargs):
        await graceful_shutdown()
        return


@register_plugin
class NapcatAdapterPlugin(BasePlugin):
    plugin_name = CONSTS.PLUGIN_NAME
    dependencies: List[str] = []  # 插件依赖列表
    python_dependencies: List[str] = []  # Python包依赖列表
    config_file_name: str = "config.toml"  # 配置文件名

    @property
    def enable_plugin(self) -> bool:
        """通过配置文件动态控制插件启用状态"""
        # 如果已经通过配置加载了状态，使用配置中的值
        if hasattr(self, "_is_enabled"):
            return self._is_enabled
        # 否则使用默认值（禁用状态）
        return False

    # 配置节描述
    config_section_descriptions = {"plugin": "插件基本信息"}

    # 配置Schema定义
    config_schema: dict = {
        "plugin": {
            "name": ConfigField(type=str, default="napcat_adapter_plugin", description="插件名称"),
            "version": ConfigField(type=str, default="1.1.0", description="插件版本"),
            "config_version": ConfigField(type=str, default="1.3.1", description="配置文件版本"),
            "enabled": ConfigField(type=bool, default=True, description="是否启用插件"),
        },
        "inner": {
            "version": ConfigField(type=str, default="0.2.1", description="配置版本号，请勿修改"),
        },
        "nickname": {
            "nickname": ConfigField(type=str, default="", description="昵称配置（目前未使用）"),
        },
        "napcat_server": {
            "mode": ConfigField(
                type=str,
                default="reverse",
                description="连接模式：reverse=反向连接(作为服务器), forward=正向连接(作为客户端)",
                choices=["reverse", "forward"],
            ),
            "host": ConfigField(type=str, default="localhost", description="主机地址"),
            "port": ConfigField(type=int, default=8095, description="端口号"),
            "url": ConfigField(
                type=str,
                default="",
                description="正向连接时的完整WebSocket URL，如 ws://localhost:8080/ws (仅在forward模式下使用)",
            ),
            "access_token": ConfigField(
                type=str, default="", description="WebSocket 连接的访问令牌，用于身份验证（可选）"
            ),
            "heartbeat_interval": ConfigField(type=int, default=30, description="心跳间隔时间（按秒计）"),
        },
        "maibot_server": {
            "platform_name": ConfigField(type=str, default="qq", description="平台名称，用于消息路由"),
        },
        "voice": {
            "use_tts": ConfigField(
                type=bool, default=False, description="是否使用tts语音（请确保你配置了tts并有对应的adapter）"
            ),
        },
        "slicing": {
            "max_frame_size": ConfigField(
                type=int, default=64, description="WebSocket帧的最大大小，单位为字节，默认64KB"
            ),
            "delay_ms": ConfigField(type=int, default=10, description="切片发送间隔时间，单位为毫秒"),
        },
        "debug": {
            "level": ConfigField(
                type=str,
                default="INFO",
                description="日志等级（DEBUG, INFO, WARNING, ERROR, CRITICAL）",
                choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
            ),
        },
        "features": {
            # 权限设置
            "group_list_type": ConfigField(
                type=str,
                default="blacklist",
                description="群聊列表类型：whitelist（白名单）或 blacklist（黑名单）",
                choices=["whitelist", "blacklist"],
            ),
            "group_list": ConfigField(type=list, default=[], description="群聊ID列表"),
            "private_list_type": ConfigField(
                type=str,
                default="blacklist",
                description="私聊列表类型：whitelist（白名单）或 blacklist（黑名单）",
                choices=["whitelist", "blacklist"],
            ),
            "private_list": ConfigField(type=list, default=[], description="用户ID列表"),
            "ban_user_id": ConfigField(
                type=list, default=[], description="全局禁止用户ID列表，这些用户无法在任何地方使用机器人"
            ),
            "ban_qq_bot": ConfigField(type=bool, default=False, description="是否屏蔽QQ官方机器人消息"),
            # 聊天功能设置
            "enable_poke": ConfigField(type=bool, default=True, description="是否启用戳一戳功能"),
            "ignore_non_self_poke": ConfigField(type=bool, default=False, description="是否无视不是针对自己的戳一戳"),
            "poke_debounce_seconds": ConfigField(
                type=int, default=3, description="戳一戳防抖时间（秒），在指定时间内第二次针对机器人的戳一戳将被忽略"
            ),
            "enable_reply_at": ConfigField(type=bool, default=True, description="是否启用引用回复时艾特用户的功能"),
            "reply_at_rate": ConfigField(type=float, default=0.5, description="引用回复时艾特用户的几率 (0.0 ~ 1.0)"),
            "enable_emoji_like": ConfigField(type=bool, default=True, description="是否启用群聊表情回复功能"),
            # 视频处理设置
            "enable_video_analysis": ConfigField(type=bool, default=True, description="是否启用视频识别功能"),
            "max_video_size_mb": ConfigField(type=int, default=100, description="视频文件最大大小限制（MB）"),
            "download_timeout": ConfigField(type=int, default=60, description="视频下载超时时间（秒）"),
            "supported_formats": ConfigField(
                type=list, default=["mp4", "avi", "mov", "mkv", "flv", "wmv", "webm"], description="支持的视频格式"
            ),
            # 消息缓冲功能已移除
        },
    }

    # 配置节描述
    config_section_descriptions = {
        "plugin": "插件基本信息",
        "inner": "内部配置信息（请勿修改）",
        "nickname": "昵称配置（目前未使用）",
        "napcat_server": "Napcat连接的ws服务设置",
        "maibot_server": "连接麦麦的ws服务设置",
        "voice": "发送语音设置",
        "slicing": "WebSocket消息切片设置",
        "debug": "调试设置",
        "features": "功能设置（权限控制、聊天功能、视频处理、消息缓冲等）",
    }

    def register_events(self):
        # 注册事件
        for e in event_types.NapcatEvent.ON_RECEIVED:
            event_manager.register_event(e.value, allowed_triggers=[self.plugin_name])

        for e in event_types.NapcatEvent.ACCOUNT:
            event_manager.register_event(e.value, allowed_subscribers=[f"{e.value}_handler"])

        for e in event_types.NapcatEvent.GROUP:
            event_manager.register_event(e.value, allowed_subscribers=[f"{e.value}_handler"])

        for e in event_types.NapcatEvent.MESSAGE:
            event_manager.register_event(e.value, allowed_subscribers=[f"{e.value}_handler"])

    def get_plugin_components(self):
        self.register_events()

        components = []
        components.append((LauchNapcatAdapterHandler.get_handler_info(), LauchNapcatAdapterHandler))
        components.append((StopNapcatAdapterHandler.get_handler_info(), StopNapcatAdapterHandler))
        for handler in get_classes_in_module(event_handlers):
            if issubclass(handler, BaseEventHandler):
                components.append((handler.get_handler_info(), handler))
        return components

    async def on_plugin_loaded(self):
        # 设置插件配置
        message_send_instance.set_plugin_config(self.config)
        # 设置chunker的插件配置
        chunker.set_plugin_config(self.config)
        # 设置response_pool的插件配置
        from .src.response_pool import set_plugin_config as set_response_pool_config

        set_response_pool_config(self.config)
        # 设置send_handler的插件配置
        send_handler.set_plugin_config(self.config)
        # 设置message_handler的插件配置
        message_handler.set_plugin_config(self.config)
        # 设置notice_handler的插件配置
        notice_handler.set_plugin_config(self.config)
        # 设置meta_event_handler的插件配置
        meta_event_handler.set_plugin_config(self.config)
        # 设置其他handler的插件配置（现在由component_registry在注册时自动设置）
