import asyncio
import time
import traceback
from typing import Any

from src.chat.message_receive.chat_stream import ChatStream, get_chat_manager
from src.chat.utils.timer_calculator import Timer
from src.common.data_models.database_data_model import DatabaseMessages
from src.common.logger import get_logger
from src.config.config import global_config
from src.person_info.person_info import get_person_info_manager
from src.plugin_system.apis import database_api, generator_api, message_api, send_api
from src.plugin_system.base.base_action import BaseAction
from src.plugin_system.base.component_types import ActionInfo, ComponentType
from src.plugin_system.core.component_registry import component_registry

logger = get_logger("action_manager")


class ChatterActionManager:
    """
    动作管理器，用于管理各种类型的动作

    现在统一使用新插件系统，简化了原有的新旧兼容逻辑。
    """

    def __init__(self):
        """初始化动作管理器"""

        # 当前正在使用的动作集合，默认加载默认动作
        self._using_actions: dict[str, ActionInfo] = {}

        # 初始化时将默认动作加载到使用中的动作
        self._using_actions = component_registry.get_default_actions()

        self.log_prefix: str = "ChatterActionManager"
        # 批量存储支持
        self._batch_storage_enabled = False
        self._pending_actions = []
        self._current_chat_id = None

    # === 执行Action方法 ===

    @staticmethod
    def create_action(
        action_name: str,
        action_data: dict,
        reasoning: str,
        cycle_timers: dict,
        thinking_id: str,
        chat_stream: ChatStream,
        log_prefix: str,
        shutting_down: bool = False,
        action_message: DatabaseMessages | None = None,
    ) -> BaseAction | None:
        """
        创建动作处理器实例

        Args:
            action_name: 动作名称
            action_data: 动作数据
            reasoning: 执行理由
            cycle_timers: 计时器字典
            thinking_id: 思考ID
            chat_stream: 聊天流
            log_prefix: 日志前缀
            shutting_down: 是否正在关闭

        Returns:
            Optional[BaseAction]: 创建的动作处理器实例，如果动作名称未注册则返回None
        """
        try:
            # 获取组件类 - 明确指定查询Action类型
            component_class: type[BaseAction] = component_registry.get_component_class(
                action_name, ComponentType.ACTION
            )  # type: ignore
            if not component_class:
                logger.warning(f"{log_prefix} 未找到Action组件: {action_name}")
                return None

            # 获取组件信息
            component_info = component_registry.get_component_info(action_name, ComponentType.ACTION)
            if not component_info:
                logger.warning(f"{log_prefix} 未找到Action组件信息: {action_name}")
                return None

            # 获取插件配置
            plugin_config = component_registry.get_plugin_config(component_info.plugin_name)

            # 创建动作实例
            instance = component_class(
                action_data=action_data,
                reasoning=reasoning,
                cycle_timers=cycle_timers,
                thinking_id=thinking_id,
                chat_stream=chat_stream,
                log_prefix=log_prefix,
                shutting_down=shutting_down,
                plugin_config=plugin_config,
                action_message=action_message,
            )

            logger.debug(f"创建Action实例成功: {action_name}")
            return instance

        except Exception as e:
            logger.error(f"创建Action实例失败 {action_name}: {e}")
            import traceback

            logger.error(traceback.format_exc())
            return None

    def get_using_actions(self) -> dict[str, ActionInfo]:
        """获取当前正在使用的动作集合"""
        return self._using_actions.copy()

    # === Modify相关方法 ===
    def remove_action_from_using(self, action_name: str) -> bool:
        """
        从当前使用的动作集中移除指定动作

        Args:
            action_name: 动作名称

        Returns:
            bool: 移除是否成功
        """
        if action_name not in self._using_actions:
            logger.warning(f"移除失败: 动作 {action_name} 不在当前使用的动作集中")
            return False

        del self._using_actions[action_name]
        logger.debug(f"已从使用集中移除动作 {action_name}")
        return True

    def restore_actions(self) -> None:
        """恢复到默认动作集"""
        actions_to_restore = list(self._using_actions.keys())
        self._using_actions = component_registry.get_default_actions()
        logger.debug(f"恢复动作集: 从 {actions_to_restore} 恢复到默认动作集 {list(self._using_actions.keys())}")

    async def execute_action(
        self,
        action_name: str,
        chat_id: str,
        target_message: DatabaseMessages | None = None,
        reasoning: str = "",
        action_data: dict | None = None,
        thinking_id: str | None = None,
        log_prefix: str = "",
        clear_unread_messages: bool = True,
    ) -> Any:
        """
        执行单个动作的通用函数

        Args:
            action_name: 动作名称
            chat_id: 聊天id
            target_message: 目标消息
            reasoning: 执行理由
            action_data: 动作数据
            thinking_id: 思考ID
            log_prefix: 日志前缀

        Returns:
            执行结果
        """

        chat_stream = None
        try:
            logger.debug(f"🎯 [ActionManager] execute_action接收到 target_message: {target_message}")
            # 通过chat_id获取chat_stream
            chat_manager = get_chat_manager()
            chat_stream = await chat_manager.get_stream(chat_id)

            if not chat_stream:
                logger.error(f"{log_prefix} 无法找到chat_id对应的chat_stream: {chat_id}")
                return {
                    "action_type": action_name,
                    "success": False,
                    "reply_text": "",
                    "error": "chat_stream not found",
                }

            # 设置正在回复的状态
            chat_stream.context_manager.context.is_replying = True

            if action_name == "no_action":
                return {"action_type": "no_action", "success": True, "reply_text": "", "command": ""}

            if action_name == "no_reply":
                # 直接处理no_reply逻辑，不再通过动作系统
                reason = reasoning or "选择不回复"
                logger.info(f"{log_prefix} 选择不回复，原因: {reason}")

                # 存储no_reply信息到数据库（支持批量存储）
                if self._batch_storage_enabled:
                    self.add_action_to_batch(
                        action_name="no_reply",
                        action_data={"reason": reason},
                        thinking_id=thinking_id or "",
                        action_done=True,
                        action_build_into_prompt=False,
                        action_prompt_display=reason,
                    )
                else:
                    asyncio.create_task(database_api.store_action_info(
                        chat_stream=chat_stream,
                        action_build_into_prompt=False,
                        action_prompt_display=reason,
                        action_done=True,
                        thinking_id=thinking_id or "",
                        action_data={"reason": reason},
                        action_name="no_reply",
                    ))

                return {"action_type": "no_reply", "success": True, "reply_text": "", "command": ""}

            elif action_name != "reply" and action_name != "respond" and action_name != "no_action":
                # 执行普通动作
                success, reply_text, command = await self._handle_action(
                    chat_stream,
                    action_name,
                    reasoning,
                    action_data or {},
                    {},  # cycle_timers
                    thinking_id,
                    target_message,
                )

                # 记录执行的动作到目标消息
                if success:
                    asyncio.create_task(self._record_action_to_message(chat_stream, action_name, target_message, action_data))
                    # 重置打断计数
                    await self._reset_interruption_count_after_action(chat_stream.stream_id)

                return {
                    "action_type": action_name,
                    "success": success,
                    "reply_text": reply_text,
                    "command": command,
                }
            else:
                # 检查目标消息是否为表情包消息以及配置是否允许回复表情包
                if target_message and getattr(target_message, "is_emoji", False):
                    # 如果是表情包消息且配置不允许回复表情包，则跳过回复
                    if not getattr(global_config.chat, "allow_reply_to_emoji", True):
                        logger.info(f"{log_prefix} 目标消息为表情包且配置不允许回复表情包，跳过回复")
                        return {"action_type": action_name, "success": True, "reply_text": "", "skip_reason": "emoji_not_allowed"}

                # 生成回复 (reply 或 respond)
                # reply: 针对单条消息的回复，使用 s4u 模板
                # respond: 对未读消息的统一回应，使用 normal 模板
                try:
                    # 根据动作类型确定提示词模式
                    prompt_mode = "s4u" if action_name == "reply" else "normal"

                    # 将prompt_mode传递给generate_reply
                    action_data_with_mode = (action_data or {}).copy()
                    action_data_with_mode["prompt_mode"] = prompt_mode

                    # 只传递当前正在执行的动作，而不是所有可用动作
                    # 这样可以让LLM明确知道"已决定执行X动作"，而不是"有这些动作可用"
                    current_action_info = self._using_actions.get(action_name)
                    current_actions: dict[str, Any] = {action_name: current_action_info} if current_action_info else {}

                    # 附加目标消息信息（如果存在）
                    if target_message:
                        # 提取目标消息的关键信息
                        target_msg_info = {
                            "message_id": getattr(target_message, "message_id", ""),
                            "sender": getattr(target_message.user_info, "user_nickname", "") if hasattr(target_message, "user_info") else "",
                            "content": getattr(target_message, "processed_plain_text", ""),
                            "time": getattr(target_message, "time", 0),
                        }
                        current_actions["_target_message"] = target_msg_info

                    success, response_set, _ = await generator_api.generate_reply(
                        chat_stream=chat_stream,
                        reply_message=target_message,
                        action_data=action_data_with_mode,
                        available_actions=current_actions,  # type: ignore
                        enable_tool=global_config.tool.enable_tool,
                        request_type="chat.replyer",
                        from_plugin=False,
                    )
                    if not success or not response_set:
                        # 安全地获取 processed_plain_text
                        if target_message:
                            msg_text = target_message.processed_plain_text or "未知消息"
                        else:
                            msg_text = "未知消息"

                        logger.info(f"对 {msg_text} 的回复生成失败")
                        return {"action_type": action_name, "success": False, "reply_text": "", "loop_info": None}
                except asyncio.CancelledError:
                    logger.debug(f"{log_prefix} 并行执行：回复生成任务已被取消")
                    return {"action_type": action_name, "success": False, "reply_text": "", "loop_info": None}

                # 从action_data中提取should_quote_reply参数
                should_quote_reply = None
                if action_data and isinstance(action_data, dict):
                    should_quote_reply = action_data.get("should_quote_reply", None)

                # respond动作默认不引用回复，保持对话流畅
                if action_name == "respond" and should_quote_reply is None:
                    should_quote_reply = False

                async def _after_reply():
                    # 发送并存储回复
                    reply_text, cycle_timers_reply = await self._send_and_store_reply(
                        chat_stream,
                        response_set,
                        asyncio.get_event_loop().time(),
                        target_message,
                        {},  # cycle_timers
                        thinking_id,
                        [],  # actions
                        should_quote_reply,  # 传递should_quote_reply参数
                    )

                    # 记录回复动作到目标消息
                    await self._record_action_to_message(chat_stream, action_name, target_message, action_data)

                    # 回复成功，重置打断计数
                    await self._reset_interruption_count_after_action(chat_stream.stream_id)

                    return reply_text
                asyncio.create_task(_after_reply())
                return {"action_type": action_name, "success": True}

        except Exception as e:
            logger.error(f"{log_prefix} 执行动作时出错: {e}")
            logger.error(f"{log_prefix} 错误信息: {traceback.format_exc()}")
            return {
                "action_type": action_name,
                "success": False,
                "reply_text": "",
                "loop_info": None,
                "error": str(e),
            }
        finally:
            # 确保重置正在回复的状态
            if chat_stream:
                chat_stream.context_manager.context.is_replying = False

    async def _record_action_to_message(self, chat_stream, action_name, target_message, action_data):
        """
        记录执行的动作到目标消息中

        Args:
            chat_stream: ChatStream实例
            action_name: 动作名称
            target_message: 目标消息
            action_data: 动作数据
        """
        try:
            from src.chat.message_manager.message_manager import message_manager

            # 获取目标消息ID
            target_message_id = None
            if target_message:
                target_message_id = target_message.message_id
            elif action_data and isinstance(action_data, dict):
                target_message_id = action_data.get("target_message_id")

            if not target_message_id:
                logger.debug(f"无法获取目标消息ID，动作: {action_name}")
                return

            # 通过message_manager更新消息的动作记录并刷新focus_energy
            await message_manager.add_action(
                stream_id=chat_stream.stream_id, message_id=target_message_id, action=action_name
            )
            logger.debug(f"已记录动作 {action_name} 到消息 {target_message_id} 并更新focus_energy")

        except Exception as e:
            logger.error(f"记录动作到消息失败: {e}")
            # 不抛出异常，避免影响主要功能

    async def _reset_interruption_count_after_action(self, stream_id: str):
        """在动作执行成功后重置打断计数"""

        try:
            from src.plugin_system.apis.chat_api import get_chat_manager

            chat_manager = get_chat_manager()
            chat_stream = await chat_manager.get_stream(stream_id)
            if chat_stream:
                context = chat_stream.context_manager
                if context.context.interruption_count > 0:
                    old_count = context.context.interruption_count
                    # old_afc_adjustment = context.context.get_afc_threshold_adjustment()
                    await context.context.reset_interruption_count()
                    logger.debug(
                        f"动作执行成功，重置聊天流 {stream_id} 的打断计数: {old_count} -> 0"
                    )
        except Exception as e:
            logger.warning(f"重置打断计数时出错: {e}")

    async def _handle_action(
        self, chat_stream, action, reasoning, action_data, cycle_timers, thinking_id, action_message
    ) -> tuple[bool, str, str]:
        """
        处理具体的动作执行

        Args:
            chat_stream: ChatStream实例
            action: 动作名称
            reasoning: 执行理由
            action_data: 动作数据
            cycle_timers: 循环计时器
            thinking_id: 思考ID
            action_message: 动作消息

        Returns:
            tuple: (执行是否成功, 回复文本, 命令文本)

        功能说明:
        - 创建对应的动作处理器
        - 执行动作并捕获异常
        - 返回执行结果供上级方法整合
        """
        if not chat_stream:
            return False, "", ""
        try:
            # 创建动作处理器
            action_handler = self.create_action(
                action_name=action,
                action_data=action_data,
                reasoning=reasoning,
                cycle_timers=cycle_timers,
                thinking_id=thinking_id,
                chat_stream=chat_stream,
                log_prefix=self.log_prefix,
                action_message=action_message,
            )
            if not action_handler:
                # 动作处理器创建失败，尝试回退机制
                logger.warning(f"{self.log_prefix} 创建动作处理器失败: {action}，尝试回退方案")

                # 获取当前可用的动作
                available_actions = self.get_using_actions()
                fallback_action = None

                # 回退优先级：reply > 第一个可用动作
                if "reply" in available_actions:
                    fallback_action = "reply"
                elif available_actions:
                    fallback_action = next(iter(available_actions.keys()))

                if fallback_action and fallback_action != action:
                    logger.info(f"{self.log_prefix} 使用回退动作: {fallback_action}")
                    action_handler = self.create_action(
                        action_name=fallback_action,
                        action_data=action_data,
                        reasoning=f"原动作'{action}'不可用，自动回退。{reasoning}",
                        cycle_timers=cycle_timers,
                        thinking_id=thinking_id,
                        chat_stream=chat_stream,
                        log_prefix=self.log_prefix,
                        action_message=action_message,
                    )

                if not action_handler:
                    logger.error(f"{self.log_prefix} 回退方案也失败，无法创建任何动作处理器")
                    return False, "", ""

            # 执行动作
            success, reply_text = await action_handler.handle_action()
            return success, reply_text, ""
        except Exception as e:
            logger.error(f"{self.log_prefix} 处理{action}时出错: {e}")
            traceback.print_exc()
            return False, "", ""

    async def _send_and_store_reply(
        self,
        chat_stream: ChatStream,
        response_set,
        loop_start_time,
        action_message,
        cycle_timers: dict[str, float],
        thinking_id,
        actions,
        should_quote_reply: bool | None = None,
    ) -> tuple[str, dict[str, float]]:
        """
        发送并存储回复信息

        Args:
            chat_stream: ChatStream实例
            response_set: 回复内容集合
            loop_start_time: 循环开始时间
            action_message: 动作消息
            cycle_timers: 循环计时器
            thinking_id: 思考ID
            actions: 动作列表
            should_quote_reply: 是否应该引用回复原消息，None表示自动决定

        Returns:
            Tuple[Dict[str, Any], str, Dict[str, float]]: 循环信息, 回复文本, 循环计时器
        """
        # 发送回复
        with Timer("回复发送", cycle_timers):
            reply_text = await self.send_response(
                chat_stream, response_set, loop_start_time, action_message, should_quote_reply
            )

        # 存储reply action信息
        person_info_manager = get_person_info_manager()

        # 获取 platform，如果不存在则从 chat_stream 获取，如果还是 None 则使用默认值
        if action_message:
            platform = action_message.chat_info.platform
            user_id = action_message.user_info.user_id
        else:
            platform = getattr(chat_stream, "platform", "unknown")
            user_id = ""

        # 获取用户信息并生成回复提示
        person_id = person_info_manager.get_person_id(
            platform,
            user_id,
        )
        person_name = await person_info_manager.get_value(person_id, "person_name")
        action_prompt_display = f"你对{person_name}进行了回复：{reply_text}"

        # 存储动作信息到数据库（支持批量存储）
        if self._batch_storage_enabled:
            self.add_action_to_batch(
                action_name="reply",
                action_data={"reply_text": reply_text},
                thinking_id=thinking_id or "",
                action_done=True,
                action_build_into_prompt=False,
                action_prompt_display=action_prompt_display,
            )
        else:
            await database_api.store_action_info(
                chat_stream=chat_stream,
                action_build_into_prompt=False,
                action_prompt_display=action_prompt_display,
                action_done=True,
                thinking_id=thinking_id,
                action_data={"reply_text": reply_text},
                action_name="reply",
            )

        return reply_text, cycle_timers

    async def send_response(
        self, chat_stream, reply_set, thinking_start_time, message_data, should_quote_reply: bool | None = None
    ) -> str:
        """
        发送回复内容的具体实现

        Args:
            chat_stream: ChatStream实例
            reply_set: 回复内容集合，包含多个回复段
            thinking_start_time: 思考开始时间
            message_data: 消息数据
            should_quote_reply: 是否应该引用回复原消息，None表示自动决定

        Returns:
            str: 完整的回复文本

        功能说明:
        - 检查是否有新消息需要回复
        - 处理主动思考的"沉默"决定
        - 根据消息数量决定是否添加回复引用
        - 逐段发送回复内容，支持打字效果
        - 正确处理元组格式的回复段
        """
        current_time = time.time()
        # 计算新消息数量
        await message_api.count_new_messages(
            chat_id=chat_stream.stream_id, start_time=thinking_start_time, end_time=current_time
        )

        # 根据新消息数量决定是否需要引用回复
        reply_text = ""
        # 检查是否为主动思考消息
        if message_data:
            is_proactive_thinking = getattr(message_data, "message_type", None) == "proactive_thinking"
        else:
            is_proactive_thinking = True

        logger.debug(f"[send_response] message_data: {message_data}")

        first_replied = False
        for reply_seg in reply_set:
            # 调试日志：验证reply_seg的格式
            logger.debug(f"Processing reply_seg type: {type(reply_seg)}, content: {reply_seg}")

            # 修正：正确处理元组格式 (格式为: (type, content))
            if isinstance(reply_seg, tuple) and len(reply_seg) >= 2:
                _, data = reply_seg
            else:
                # 向下兼容：如果已经是字符串，则直接使用
                data = str(reply_seg)

            if isinstance(data, list):
                data = "".join(map(str, data))
            reply_text += data

            # 如果是主动思考且内容为"沉默"，则不发送
            if is_proactive_thinking and data.strip() == "沉默":
                logger.info(f"{self.log_prefix} 主动思考决定保持沉默，不发送消息")
                continue

            # 发送第一段回复
            if not first_replied:
                # 决定是否引用回复
                is_private_chat = not bool(chat_stream.group_info)

                # 如果明确指定了should_quote_reply，则使用指定值
                if should_quote_reply is not None:
                    set_reply_flag = should_quote_reply and bool(message_data)
                    logger.debug(
                        f"📤 [ActionManager] 使用planner指定的引用设置: should_quote_reply={should_quote_reply}"
                    )
                else:
                    # 否则使用默认逻辑：默认不引用，让对话更流畅自然
                    set_reply_flag = False
                    logger.debug(
                        f"📤 [ActionManager] 使用默认引用逻辑: 默认不引用(is_private={is_private_chat})"
                    )

                logger.debug(
                    f"📤 [ActionManager] 准备发送第一段回复。message_data: {message_data}, set_reply: {set_reply_flag}"
                )
                await send_api.text_to_stream(
                    text=data,
                    stream_id=chat_stream.stream_id,
                    reply_to_message=message_data,
                    set_reply=set_reply_flag,
                    typing=False,
                )
                first_replied = True
            else:
                # 发送后续回复
                await send_api.text_to_stream(
                    text=data,
                    stream_id=chat_stream.stream_id,
                    reply_to_message=None,
                    set_reply=False,
                    typing=True,
                )

        return reply_text

    def enable_batch_storage(self, chat_id: str):
        """启用批量存储模式"""
        self._batch_storage_enabled = True
        self._current_chat_id = chat_id
        self._pending_actions.clear()
        logger.debug(f"已启用批量存储模式，chat_id: {chat_id}")

    def disable_batch_storage(self):
        """禁用批量存储模式"""
        self._batch_storage_enabled = False
        self._current_chat_id = None
        self._pending_actions = []  # 清空队列
        logger.debug("已禁用批量存储模式")

    def add_action_to_batch(
        self,
        action_name: str,
        action_data: dict,
        thinking_id: str = "",
        action_done: bool = True,
        action_build_into_prompt: bool = False,
        action_prompt_display: str = "",
    ):
        """添加动作到批量存储列表"""
        if not self._batch_storage_enabled:
            return False

        action_record = {
            "action_name": action_name,
            "action_data": action_data,
            "thinking_id": thinking_id,
            "action_done": action_done,
            "action_build_into_prompt": action_build_into_prompt,
            "action_prompt_display": action_prompt_display,
            "timestamp": time.time(),
        }
        self._pending_actions.append(action_record)
        logger.debug(f"已添加动作到批量存储列表: {action_name} (当前待处理: {len(self._pending_actions)} 个)")
        return True

    async def flush_batch_storage(self, chat_stream):
        """批量存储所有待处理的动作记录"""
        if not self._pending_actions:
            logger.debug("没有待处理的动作需要批量存储")
            return

        try:
            logger.info(f"开始批量存储 {len(self._pending_actions)} 个动作记录")

            # 批量存储所有动作
            stored_count = 0
            for action_data in self._pending_actions:
                try:
                    result = await database_api.store_action_info(
                        chat_stream=chat_stream,
                        action_name=action_data.get("action_name", ""),
                        action_data=action_data.get("action_data", {}),
                        action_done=action_data.get("action_done", True),
                        action_build_into_prompt=action_data.get("action_build_into_prompt", False),
                        action_prompt_display=action_data.get("action_prompt_display", ""),
                        thinking_id=action_data.get("thinking_id", ""),
                    )
                    if result:
                        stored_count += 1
                except Exception as e:
                    logger.error(f"存储单个动作记录失败: {e}")

            logger.info(f"批量存储完成: 成功存储 {stored_count}/{len(self._pending_actions)} 个动作记录")

            # 清空待处理列表
            self._pending_actions.clear()

        except Exception as e:
            logger.error(f"批量存储动作记录时发生错误: {e}")
