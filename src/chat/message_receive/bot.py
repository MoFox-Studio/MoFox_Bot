import os
import re
import time
import traceback
from typing import Any

from maim_message import UserInfo

# 导入反注入系统
from src.chat.antipromptinjector import initialize_anti_injector
from src.chat.message_manager import message_manager
from src.chat.message_receive.chat_stream import ChatStream, get_chat_manager
from src.chat.message_receive.message import MessageRecv, MessageRecvS4U
from src.chat.message_receive.storage import MessageStorage
from src.chat.utils.prompt import Prompt, global_prompt_manager
from src.chat.utils.utils import is_mentioned_bot_in_message
from src.common.logger import get_logger
from src.config.config import global_config
from src.mais4u.mais4u_chat.s4u_msg_processor import S4UMessageProcessor
from src.mood.mood_manager import mood_manager  # 导入情绪管理器
from src.plugin_system.base import BaseCommand, EventType
from src.plugin_system.core import component_registry, event_manager, global_announcement_manager

# 获取项目根目录（假设本文件在src/chat/message_receive/下，根目录为上上上级目录）
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))

# 配置主程序日志格式
logger = get_logger("chat")
anti_injector_logger = get_logger("anti_injector")


def _check_ban_words(text: str, chat: ChatStream, userinfo: UserInfo) -> bool:
    """检查消息是否包含过滤词

    Args:
        text: 待检查的文本
        chat: 聊天对象
        userinfo: 用户信息

    Returns:
        bool: 是否包含过滤词
    """
    for word in global_config.message_receive.ban_words:
        if word in text:
            chat_name = chat.group_info.group_name if chat.group_info else "私聊"
            logger.info(f"[{chat_name}]{userinfo.user_nickname}:{text}")
            logger.info(f"[过滤词识别]消息中含有{word}，filtered")
            return True
    return False


def _check_ban_regex(text: str, chat: ChatStream, userinfo: UserInfo) -> bool:
    """检查消息是否匹配过滤正则表达式

    Args:
        text: 待检查的文本
        chat: 聊天对象
        userinfo: 用户信息

    Returns:
        bool: 是否匹配过滤正则
    """
    for pattern in global_config.message_receive.ban_msgs_regex:
        if re.search(pattern, text):
            chat_name = chat.group_info.group_name if chat.group_info else "私聊"
            logger.info(f"[{chat_name}]{userinfo.user_nickname}:{text}")
            logger.info(f"[正则表达式过滤]消息匹配到{pattern}，filtered")
            return True
    return False


class ChatBot:
    def __init__(self):
        self.bot = None  # bot 实例引用
        self._started = False
        self.mood_manager = mood_manager  # 获取情绪管理器单例
        # 亲和力流消息处理器 - 直接使用全局afc_manager

        self.s4u_message_processor = S4UMessageProcessor()

        # 初始化反注入系统
        self._initialize_anti_injector()

        # 启动消息管理器
        self._message_manager_started = False

    def _initialize_anti_injector(self):
        """初始化反注入系统"""
        try:
            initialize_anti_injector()

            anti_injector_logger.info(
                f"反注入系统已初始化 - 启用: {global_config.anti_prompt_injection.enabled}, "
                f"模式: {global_config.anti_prompt_injection.process_mode}, "
                f"规则: {global_config.anti_prompt_injection.enabled_rules}, LLM: {global_config.anti_prompt_injection.enabled_LLM}"
            )
        except Exception as e:
            anti_injector_logger.error(f"反注入系统初始化失败: {e}")

    async def _ensure_started(self):
        """确保所有任务已启动"""
        if not self._started:
            logger.debug("确保ChatBot所有任务已启动")

            # 启动消息管理器
            if not self._message_manager_started:
                await message_manager.start()
                self._message_manager_started = True
                logger.info("消息管理器已启动")

            self._started = True

    async def _process_plus_commands(self, message: MessageRecv):
        """独立处理PlusCommand系统"""
        try:
            text = message.processed_plain_text

            # 获取配置的命令前缀
            from src.config.config import global_config

            prefixes = global_config.command.command_prefixes

            # 检查是否以任何前缀开头
            matched_prefix = None
            for prefix in prefixes:
                if text.startswith(prefix):
                    matched_prefix = prefix
                    break

            if not matched_prefix:
                return False, None, True  # 不是命令，继续处理

            # 移除前缀
            command_part = text[len(matched_prefix) :].strip()

            # 分离命令名和参数
            parts = command_part.split(None, 1)
            if not parts:
                return False, None, True  # 没有命令名，继续处理

            command_word = parts[0].lower()
            args_text = parts[1] if len(parts) > 1 else ""

            # 查找匹配的PlusCommand
            plus_command_registry = component_registry.get_plus_command_registry()
            matching_commands = []

            for plus_command_name, plus_command_class in plus_command_registry.items():
                plus_command_info = component_registry.get_registered_plus_command_info(plus_command_name)
                if not plus_command_info:
                    continue

                # 检查命令名是否匹配（命令名和别名）
                all_commands = [plus_command_name.lower()] + [
                    alias.lower() for alias in plus_command_info.command_aliases
                ]
                if command_word in all_commands:
                    matching_commands.append((plus_command_class, plus_command_info, plus_command_name))

            if not matching_commands:
                return False, None, True  # 没有找到匹配的PlusCommand，继续处理

            # 如果有多个匹配，按优先级排序
            if len(matching_commands) > 1:
                matching_commands.sort(key=lambda x: x[1].priority, reverse=True)
                logger.warning(
                    f"文本 '{text}' 匹配到多个PlusCommand: {[cmd[2] for cmd in matching_commands]}，使用优先级最高的"
                )

            plus_command_class, plus_command_info, plus_command_name = matching_commands[0]

            # 检查命令是否被禁用
            if (
                message.chat_stream
                and message.chat_stream.stream_id
                and plus_command_name
                in global_announcement_manager.get_disabled_chat_commands(message.chat_stream.stream_id)
            ):
                logger.info("用户禁用的PlusCommand，跳过处理")
                return False, None, True

            message.is_command = True

            # 获取插件配置
            plugin_config = component_registry.get_plugin_config(plus_command_name)

            # 创建PlusCommand实例
            plus_command_instance = plus_command_class(message, plugin_config)

            try:
                # 检查聊天类型限制
                if not plus_command_instance.is_chat_type_allowed():
                    is_group = message.message_info.group_info
                    logger.info(
                        f"PlusCommand {plus_command_class.__name__} 不支持当前聊天类型: {'群聊' if is_group else '私聊'}"
                    )
                    return False, None, True  # 跳过此命令，继续处理其他消息

                # 设置参数
                from src.plugin_system.base.command_args import CommandArgs

                command_args = CommandArgs(args_text)
                plus_command_instance.args = command_args

                # 执行命令
                success, response, intercept_message = await plus_command_instance.execute(command_args)

                # 记录命令执行结果
                if success:
                    logger.info(f"PlusCommand执行成功: {plus_command_class.__name__} (拦截: {intercept_message})")
                else:
                    logger.warning(f"PlusCommand执行失败: {plus_command_class.__name__} - {response}")

                # 根据命令的拦截设置决定是否继续处理消息
                return True, response, not intercept_message  # 找到命令，根据intercept_message决定是否继续

            except Exception as e:
                logger.error(f"执行PlusCommand时出错: {plus_command_class.__name__} - {e}")
                logger.error(traceback.format_exc())

                try:
                    await plus_command_instance.send_text(f"命令执行出错: {e!s}")
                except Exception as send_error:
                    logger.error(f"发送错误消息失败: {send_error}")

                # 命令出错时，根据命令的拦截设置决定是否继续处理消息
                return True, str(e), False  # 出错时继续处理消息

        except Exception as e:
            logger.error(f"处理PlusCommand时出错: {e}")
            return False, None, True  # 出错时继续处理消息

    async def _process_commands_with_new_system(self, message: MessageRecv):
        # sourcery skip: use-named-expression
        """使用新插件系统处理命令"""
        try:
            text = message.processed_plain_text

            # 使用新的组件注册中心查找命令
            command_result = component_registry.find_command_by_text(text)
            if command_result:
                command_class, matched_groups, command_info = command_result
                plugin_name = command_info.plugin_name
                command_name = command_info.name
                if (
                    message.chat_stream
                    and message.chat_stream.stream_id
                    and command_name
                    in global_announcement_manager.get_disabled_chat_commands(message.chat_stream.stream_id)
                ):
                    logger.info("用户禁用的命令，跳过处理")
                    return False, None, True

                message.is_command = True

                # 获取插件配置
                plugin_config = component_registry.get_plugin_config(plugin_name)

                # 创建命令实例
                command_instance: BaseCommand = command_class(message, plugin_config)
                command_instance.set_matched_groups(matched_groups)

                try:
                    # 检查聊天类型限制
                    if not command_instance.is_chat_type_allowed():
                        is_group = message.message_info.group_info
                        logger.info(
                            f"命令 {command_class.__name__} 不支持当前聊天类型: {'群聊' if is_group else '私聊'}"
                        )
                        return False, None, True  # 跳过此命令，继续处理其他消息

                    # 执行命令
                    success, response, intercept_message = await command_instance.execute()

                    # 记录命令执行结果
                    if success:
                        logger.info(f"命令执行成功: {command_class.__name__} (拦截: {intercept_message})")
                    else:
                        logger.warning(f"命令执行失败: {command_class.__name__} - {response}")

                    # 根据命令的拦截设置决定是否继续处理消息
                    return True, response, not intercept_message  # 找到命令，根据intercept_message决定是否继续

                except Exception as e:
                    logger.error(f"执行命令时出错: {command_class.__name__} - {e}")
                    logger.error(traceback.format_exc())

                    try:
                        await command_instance.send_text(f"命令执行出错: {e!s}")
                    except Exception as send_error:
                        logger.error(f"发送错误消息失败: {send_error}")

                    # 命令出错时，根据命令的拦截设置决定是否继续处理消息
                    return True, str(e), False  # 出错时继续处理消息

            # 没有找到命令，继续处理消息
            return False, None, True

        except Exception as e:
            logger.error(f"处理命令时出错: {e}")
            return False, None, True  # 出错时继续处理消息

    async def handle_notice_message(self, message: MessageRecv):
        if message.message_info.message_id == "notice":
            message.is_notify = True
            logger.info("notice消息")
            # print(message)

            return True

        # 处理适配器响应消息
        if hasattr(message, "message_segment") and message.message_segment:
            if message.message_segment.type == "adapter_response":
                await self.handle_adapter_response(message)
                return True
            elif message.message_segment.type == "adapter_command":
                # 适配器命令消息不需要进一步处理
                logger.debug("收到适配器命令消息，跳过后续处理")
                return True

        return False

    async def handle_adapter_response(self, message: MessageRecv):
        """处理适配器命令响应"""
        try:
            from src.plugin_system.apis.send_api import put_adapter_response

            seg_data = message.message_segment.data
            request_id = seg_data.get("request_id")
            response_data = seg_data.get("response")

            if request_id and response_data:
                logger.debug(f"收到适配器响应: request_id={request_id}")
                put_adapter_response(request_id, response_data)
            else:
                logger.warning("适配器响应消息格式不正确")

        except Exception as e:
            logger.error(f"处理适配器响应时出错: {e}")

    async def do_s4u(self, message_data: dict[str, Any]):
        message = MessageRecvS4U(message_data)
        group_info = message.message_info.group_info
        user_info = message.message_info.user_info

        get_chat_manager().register_message(message)
        chat = await get_chat_manager().get_or_create_stream(
            platform=message.message_info.platform,  # type: ignore
            user_info=user_info,  # type: ignore
            group_info=group_info,
        )

        message.update_chat_stream(chat)

        # 处理消息内容
        await message.process()

        await self.s4u_message_processor.process_message(message)

        return

    async def message_process(self, message_data: dict[str, Any]) -> None:
        """处理转化后的统一格式消息"""
        try:
            # 首先处理可能的切片消息重组
            from src.utils.message_chunker import reassembler

            # 尝试重组切片消息
            reassembled_message = await reassembler.process_chunk(message_data)
            if reassembled_message is None:
                # 这是一个切片，但还未完整，等待更多切片
                logger.debug("等待更多切片，跳过此次处理")
                return
            elif reassembled_message != message_data:
                # 消息已被重组，使用重组后的消息
                logger.info("使用重组后的完整消息进行处理")
                message_data = reassembled_message

            # 确保所有任务已启动
            await self._ensure_started()

            # 控制握手等消息可能缺少 message_info，这里直接跳过避免 KeyError
            if not isinstance(message_data, dict):
                logger.warning(f"收到无法解析的消息类型: {type(message_data)}，已跳过")
                return
            message_info = message_data.get("message_info")
            if not isinstance(message_info, dict):
                logger.debug(
                    "收到缺少 message_info 的消息，已跳过。可用字段: %s",
                    ", ".join(message_data.keys()),
                )
                return

            platform = message_info.get("platform")

            if platform == "amaidesu_default":
                await self.do_s4u(message_data)
                return

            if message_info.get("group_info") is not None:
                message_info["group_info"]["group_id"] = str(
                    message_info["group_info"]["group_id"]
                )
            if message_info.get("user_info") is not None:
                message_info["user_info"]["user_id"] = str(
                    message_info["user_info"]["user_id"]
                )
            # print(message_data)
            # logger.debug(str(message_data))
            message = MessageRecv(message_data)

            message.is_mentioned, _ = is_mentioned_bot_in_message(message)
            group_info = message.message_info.group_info
            user_info = message.message_info.user_info
            if message.message_info.additional_config:
                sent_message = message.message_info.additional_config.get("echo", False)
                if sent_message:  # 这一段只是为了在一切处理前劫持上报的自身消息，用于更新message_id，需要ada支持上报事件，实际测试中不会对正常使用造成任何问题
                    await MessageStorage.update_message(message)
                    return

            get_chat_manager().register_message(message)

            chat = await get_chat_manager().get_or_create_stream(
                platform=message.message_info.platform,  # type: ignore
                user_info=user_info,  # type: ignore
                group_info=group_info,
            )

            message.update_chat_stream(chat)

            # 处理消息内容，生成纯文本
            await message.process()

            # 在这里打印[所见]日志，确保在所有处理和过滤之前记录
            chat_name = chat.group_info.group_name if chat.group_info else "私聊"
            logger.info(
                f"[{chat_name}]{message.message_info.user_info.user_nickname}:{message.processed_plain_text}\u001b[0m"
            )

            # 过滤检查
            if _check_ban_words(message.processed_plain_text, chat, user_info) or _check_ban_regex(  # type: ignore
                message.raw_message,  # type: ignore
                chat,
                user_info,  # type: ignore
            ):
                return

            # 命令处理 - 首先尝试PlusCommand独立处理
            is_plus_command, plus_cmd_result, plus_continue_process = await self._process_plus_commands(message)

            # 如果是PlusCommand且不需要继续处理，则直接返回
            if is_plus_command and not plus_continue_process:
                await MessageStorage.store_message(message, chat)
                logger.info(f"PlusCommand处理完成，跳过后续消息处理: {plus_cmd_result}")
                return

            # 如果不是PlusCommand，尝试传统的BaseCommand处理
            if not is_plus_command:
                is_command, cmd_result, continue_process = await self._process_commands_with_new_system(message)

                # 如果是命令且不需要继续处理，则直接返回
                if is_command and not continue_process:
                    await MessageStorage.store_message(message, chat)
                    logger.info(f"命令处理完成，跳过后续消息处理: {cmd_result}")
                    return

            result = await event_manager.trigger_event(EventType.ON_MESSAGE, permission_group="SYSTEM", message=message)
            if not result.all_continue_process():
                raise UserWarning(f"插件{result.get_summary().get('stopped_handlers', '')}于消息到达时取消了消息处理")

            # TODO:暂不可用
            # 确认从接口发来的message是否有自定义的prompt模板信息
            if message.message_info.template_info and not message.message_info.template_info.template_default:
                template_group_name: str | None = message.message_info.template_info.template_name  # type: ignore
                template_items = message.message_info.template_info.template_items
                async with global_prompt_manager.async_message_scope(template_group_name):
                    if isinstance(template_items, dict):
                        for k in template_items.keys():
                            await Prompt.create_async(template_items[k], k)
                            logger.debug(f"注册{template_items[k]},{k}")
            else:
                template_group_name = None

            async def preprocess():
                from src.common.data_models.database_data_model import DatabaseMessages

                message_info = message.message_info
                msg_user_info = getattr(message_info, "user_info", None)
                stream_user_info = getattr(message.chat_stream, "user_info", None)
                group_info = getattr(message.chat_stream, "group_info", None)

                message_id = message_info.message_id or ""
                message_time = message_info.time if message_info.time is not None else time.time()
                is_mentioned = None
                if isinstance(message.is_mentioned, bool):
                    is_mentioned = message.is_mentioned
                elif isinstance(message.is_mentioned, int | float):
                    is_mentioned = message.is_mentioned != 0

                user_id = ""
                user_nickname = ""
                user_cardname = None
                user_platform = ""
                if msg_user_info:
                    user_id = str(getattr(msg_user_info, "user_id", "") or "")
                    user_nickname = getattr(msg_user_info, "user_nickname", "") or ""
                    user_cardname = getattr(msg_user_info, "user_cardname", None)
                    user_platform = getattr(msg_user_info, "platform", "") or ""
                elif stream_user_info:
                    user_id = str(getattr(stream_user_info, "user_id", "") or "")
                    user_nickname = getattr(stream_user_info, "user_nickname", "") or ""
                    user_cardname = getattr(stream_user_info, "user_cardname", None)
                    user_platform = getattr(stream_user_info, "platform", "") or ""

                chat_user_id = str(getattr(stream_user_info, "user_id", "") or "")
                chat_user_nickname = getattr(stream_user_info, "user_nickname", "") or ""
                chat_user_cardname = getattr(stream_user_info, "user_cardname", None)
                chat_user_platform = getattr(stream_user_info, "platform", "") or ""

                group_id = getattr(group_info, "group_id", None)
                group_name = getattr(group_info, "group_name", None)
                group_platform = getattr(group_info, "platform", None)

                # 创建数据库消息对象
                db_message = DatabaseMessages(
                    message_id=message_id,
                    time=float(message_time),
                    chat_id=message.chat_stream.stream_id,
                    processed_plain_text=message.processed_plain_text,
                    display_message=message.processed_plain_text,
                    is_mentioned=is_mentioned,
                    is_at=bool(message.is_at) if message.is_at is not None else None,
                    is_emoji=bool(message.is_emoji),
                    is_picid=bool(message.is_picid),
                    is_command=bool(message.is_command),
                    is_notify=bool(message.is_notify),
                    user_id=user_id,
                    user_nickname=user_nickname,
                    user_cardname=user_cardname,
                    user_platform=user_platform,
                    chat_info_stream_id=message.chat_stream.stream_id,
                    chat_info_platform=message.chat_stream.platform,
                    chat_info_create_time=float(message.chat_stream.create_time),
                    chat_info_last_active_time=float(message.chat_stream.last_active_time),
                    chat_info_user_id=chat_user_id,
                    chat_info_user_nickname=chat_user_nickname,
                    chat_info_user_cardname=chat_user_cardname,
                    chat_info_user_platform=chat_user_platform,
                    chat_info_group_id=group_id,
                    chat_info_group_name=group_name,
                    chat_info_group_platform=group_platform,
                )

                # 兼容历史逻辑：显式设置群聊相关属性，便于后续逻辑通过 hasattr 判断
                if group_info:
                    setattr(db_message, "chat_info_group_id", group_id)
                    setattr(db_message, "chat_info_group_name", group_name)
                    setattr(db_message, "chat_info_group_platform", group_platform)
                else:
                    setattr(db_message, "chat_info_group_id", None)
                    setattr(db_message, "chat_info_group_name", None)
                    setattr(db_message, "chat_info_group_platform", None)

                # 先交给消息管理器处理，计算兴趣度等衍生数据
                try:
                    await message_manager.add_message(message.chat_stream.stream_id, db_message)
                    logger.debug(f"消息已添加到消息管理器: {message.chat_stream.stream_id}")
                except Exception as e:
                    logger.error(f"消息添加到消息管理器失败: {e}")

                # 将兴趣度结果同步回原始消息，便于后续流程使用
                message.interest_value = getattr(db_message, "interest_value", getattr(message, "interest_value", 0.0))
                setattr(
                    message,
                    "should_reply",
                    getattr(db_message, "should_reply", getattr(message, "should_reply", False)),
                )
                setattr(message, "should_act", getattr(db_message, "should_act", getattr(message, "should_act", False)))

                # 存储消息到数据库，只进行一次写入
                try:
                    await MessageStorage.store_message(message, message.chat_stream)
                    logger.debug(
                        "消息已存储到数据库: %s (interest=%.3f, should_reply=%s, should_act=%s)",
                        message.message_info.message_id,
                        getattr(message, "interest_value", -1.0),
                        getattr(message, "should_reply", None),
                        getattr(message, "should_act", None),
                    )
                except Exception as e:
                    logger.error(f"存储消息到数据库失败: {e}")
                    traceback.print_exc()

                # 情绪系统更新 - 在消息存储后触发情绪更新
                try:
                    if global_config.mood.enable_mood:
                        # 获取兴趣度用于情绪更新
                        interest_rate = getattr(message, "interest_value", 0.0)
                        if interest_rate is None:
                            interest_rate = 0.0
                        logger.debug(f"开始更新情绪状态，兴趣度: {interest_rate:.2f}")

                        # 获取当前聊天的情绪对象并更新情绪状态
                        chat_mood = mood_manager.get_mood_by_chat_id(message.chat_stream.stream_id)
                        await chat_mood.update_mood_by_message(message, interest_rate)
                        logger.debug("情绪状态更新完成")
                except Exception as e:
                    logger.error(f"更新情绪状态失败: {e}")
                    traceback.print_exc()

            if template_group_name:
                async with global_prompt_manager.async_message_scope(template_group_name):
                    await preprocess()
            else:
                await preprocess()

        except Exception as e:
            logger.error(f"预处理消息失败: {e}")
            traceback.print_exc()


# 创建全局ChatBot实例
chat_bot = ChatBot()
