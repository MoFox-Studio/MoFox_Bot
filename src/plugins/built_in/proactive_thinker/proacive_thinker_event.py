import asyncio
import random
import time
import traceback
from datetime import datetime

from maim_message import UserInfo

from src.chat.message_receive.chat_stream import get_chat_manager
from src.common.logger import get_logger
from src.config.config import global_config
from src.manager.async_task_manager import AsyncTask, async_task_manager
from src.plugin_system import BaseEventHandler, EventType
from src.plugin_system.apis import chat_api, message_api, person_api
from src.plugin_system.base.base_event import HandlerResult
from src.chat.message_manager.sleep_system.state_manager import SleepState, sleep_state_manager

from .proactive_thinker_executor import ProactiveThinkerExecutor

logger = get_logger(__name__)


class ColdStartTask(AsyncTask):
    """
    “冷启动”任务，在机器人启动时执行一次。
    它的核心职责是“唤醒”那些因重启而“沉睡”的聊天流，确保它们能够接收主动思考。
    对于在白名单中但从未有过记录的全新用户，它也会发起第一次“破冰”问候。
    """

    def __init__(self, bot_start_time: float):
        super().__init__(task_name="ColdStartTask")
        self.chat_manager = get_chat_manager()
        self.executor = ProactiveThinkerExecutor()
        self.bot_start_time = bot_start_time

    async def run(self):
        """任务主逻辑，在启动后执行一次白名单扫描。"""
        logger.info("冷启动任务已启动，将在短暂延迟后开始唤醒沉睡的聊天流...")
        await asyncio.sleep(30)  # 延迟以确保所有服务和聊天流已从数据库加载完毕

        try:
            current_state = sleep_state_manager.get_current_state()
            if current_state  == SleepState.SLEEPING:
                logger.info("bot正在睡觉,跳过本次任务")
                return
            logger.info("【冷启动】开始扫描白名单，唤醒沉睡的聊天流...")

            # 【修复】增加对私聊总开关的判断
            if not global_config.proactive_thinking.enable_in_private:
                logger.info("【冷启动】私聊主动思考功能未启用，任务结束。")
                return

            enabled_private_chats = global_config.proactive_thinking.enabled_private_chats
            if not enabled_private_chats:
                logger.debug("【冷启动】私聊白名单为空，任务结束。")
                return

            for chat_id in enabled_private_chats:
                try:
                    platform, user_id_str = chat_id.split(":")
                    user_id = int(user_id_str)

                    should_wake_up = False
                    stream = chat_api.get_stream_by_user_id(user_id_str, platform)

                    if not stream:
                        should_wake_up = True
                        logger.info(f"【冷启动】发现全新用户 {chat_id}，准备发起第一次问候。")
                    elif stream.last_active_time < self.bot_start_time:
                        should_wake_up = True
                        logger.info(
                            f"【冷启动】发现沉睡的聊天流 {chat_id} (最后活跃于 {datetime.fromtimestamp(stream.last_active_time)})，准备唤醒。"
                        )

                    if should_wake_up:
                        person_id = person_api.get_person_id(platform, user_id)
                        nickname = await person_api.get_person_value(person_id, "nickname")
                        user_nickname = nickname or f"用户{user_id}"
                        user_info = UserInfo(platform=platform, user_id=str(user_id), user_nickname=user_nickname)

                        # 使用 get_or_create_stream 来安全地获取或创建流
                        stream = await self.chat_manager.get_or_create_stream(platform, user_info)

                        formatted_stream_id = f"{stream.user_info.platform}:{stream.user_info.user_id}:private"
                        await self.executor.execute(stream_id=formatted_stream_id, start_mode="cold_start")
                        logger.info(f"【冷启动】已为用户 {chat_id} (昵称: {user_nickname}) 发送唤醒/问候消息。")

                except ValueError:
                    logger.warning(f"【冷启动】白名单条目格式错误或用户ID无效，已跳过: {chat_id}")
                except Exception as e:
                    logger.error(f"【冷启动】处理用户 {chat_id} 时发生未知错误: {e}", exc_info=True)

        except asyncio.CancelledError:
            logger.info("冷启动任务被正常取消。")
        except Exception as e:
            logger.error(f"【冷启动】任务出现严重错误: {e}", exc_info=True)
        finally:
            logger.info("【冷启动】任务执行完毕。")


class ProactiveThinkingTask(AsyncTask):
    """
    主动思考的后台任务（日常唤醒），负责在聊天“冷却”后重新活跃气氛。
    它只处理已经存在的聊天流。
    """

    def __init__(self):
        super().__init__(task_name="ProactiveThinkingTask")
        self.chat_manager = get_chat_manager()
        self.executor = ProactiveThinkerExecutor()

    def _get_next_interval(self) -> float:
        """
        动态计算下一次执行的时间间隔，模拟人类行为的随机性。
        结合了基础间隔、随机偏移和每日不同时段的活跃度调整。
        """
        # 从配置中读取基础间隔和随机范围
        base_interval = global_config.proactive_thinking.interval
        sigma = global_config.proactive_thinking.interval_sigma

        # 1. 在 [base - sigma, base + sigma] 范围内随机取一个值
        interval = random.uniform(base_interval - sigma, base_interval + sigma)

        # 2. 根据当前时间，应用活跃度调整因子
        now = datetime.now()
        current_time_str = now.strftime("%H:%M")

        adjust_rules = global_config.proactive_thinking.talk_frequency_adjust
        if adjust_rules and adjust_rules[0]:
            # 按时间对规则排序，确保能找到正确的时间段
            rules = sorted([rule.split(",") for rule in adjust_rules[0][1:]], key=lambda x: x[0])

            factor = 1.0
            # 找到最后一个小于等于当前时间的规则
            for time_str, factor_str in rules:
                if current_time_str >= time_str:
                    factor = float(factor_str)
                else:
                    break  # 后面的时间都比当前晚，无需再找
            # factor > 1 表示更活跃，所以用除法来缩短间隔
            interval /= factor

        # 保证最小间隔，防止过于频繁的骚扰
        return max(60.0, interval)

    async def run(self):
        """任务主循环，周期性地检查所有已存在的聊天是否需要“唤醒”。"""
        logger.info("日常唤醒任务已启动，将根据动态间隔检查聊天活跃度。")
        await asyncio.sleep(15)  # 初始等待

        while True:
            # 计算下一次检查前的休眠时间
            next_interval = self._get_next_interval()
            try:
                current_state = sleep_state_manager.get_current_state()
                if current_state  == SleepState.SLEEPING:
                    logger.info("bot正在睡觉,跳过本次任务")
                    return
                logger.debug(f"【日常唤醒】下一次检查将在 {next_interval:.2f} 秒后进行。")
                await asyncio.sleep(next_interval)

                logger.info("【日常唤醒】开始检查不活跃的聊天...")

                # 加载白名单配置
                enabled_private = set(global_config.proactive_thinking.enabled_private_chats)
                enabled_groups = set(global_config.proactive_thinking.enabled_group_chats)

                # 分别处理私聊和群聊
                # 1. 处理私聊：首先检查私聊总开关
                if global_config.proactive_thinking.enable_in_private:
                    for chat_id in enabled_private:
                        try:
                            platform, user_id_str = chat_id.split(":")
                            # 【核心逻辑】检查聊天流是否存在。不存在则跳过，交由ColdStartTask处理。
                            stream = chat_api.get_stream_by_user_id(user_id_str, platform)
                            if not stream:
                                continue

                            # 检查冷却时间
                            recent_messages = await message_api.get_recent_messages(
                                chat_id=stream.stream_id, limit=1, limit_mode="latest"
                            )
                            last_message_time = recent_messages[0]["time"] if recent_messages else stream.create_time
                            time_since_last_active = time.time() - last_message_time
                            if time_since_last_active > next_interval:
                                logger.info(
                                    f"【日常唤醒-私聊】聊天流 {stream.stream_id} 已冷却 {time_since_last_active:.2f} 秒，触发主动对话。"
                                )
                                formatted_stream_id = f"{stream.user_info.platform}:{stream.user_info.user_id}:private"
                                await self.executor.execute(stream_id=formatted_stream_id, start_mode="wake_up")
                                stream.update_active_time()
                                await self.chat_manager._save_stream(stream)

                        except ValueError:
                            logger.warning(f"【日常唤醒】私聊白名单条目格式错误，已跳过: {chat_id}")
                        except Exception as e:
                            logger.error(f"【日常唤醒】处理私聊用户 {chat_id} 时发生未知错误: {e}", exc_info=True)

                # 2. 处理群聊：首先检查群聊总开关
                if global_config.proactive_thinking.enable_in_group:
                    all_streams = list(self.chat_manager.streams.values())
                    for stream in all_streams:
                        if not stream.group_info:
                            continue  # 只处理群聊

                        # 【修复】检查群聊是否在白名单内
                        if f"qq:{stream.group_info.group_id}" in enabled_groups:
                            # 检查冷却时间
                            recent_messages = await message_api.get_recent_messages(chat_id=stream.stream_id, limit=1)
                            last_message_time = recent_messages[0]["time"] if recent_messages else stream.create_time
                            time_since_last_active = time.time() - last_message_time
                            if time_since_last_active > next_interval:
                                logger.info(
                                    f"【日常唤醒-群聊】聊天流 {stream.stream_id} 已冷却 {time_since_last_active:.2f} 秒，触发主动对话。"
                                )
                                formatted_stream_id = f"{stream.user_info.platform}:{stream.group_info.group_id}:group"
                                await self.executor.execute(stream_id=formatted_stream_id, start_mode="wake_up")
                                stream.update_active_time()
                                await self.chat_manager._save_stream(stream)

            except asyncio.CancelledError:
                logger.info("日常唤醒任务被正常取消。")
                break
            except Exception as e:
                traceback.print_exc()  # 打印完整的堆栈跟踪
                logger.error(f"【日常唤醒】任务出现错误，将在60秒后重试: {e}", exc_info=True)
                await asyncio.sleep(60)


class ProactiveThinkerEventHandler(BaseEventHandler):
    """主动思考插件的启动事件处理器，负责根据配置启动一个或两个后台任务。"""

    handler_name: str = "proactive_thinker_on_start"
    handler_description: str = "主动思考插件的启动事件处理器"
    init_subscribe: list[EventType | str] = [EventType.ON_START]

    async def execute(self, kwargs: dict | None) -> "HandlerResult":
        """在机器人启动时执行，根据配置决定是否启动后台任务。"""
        logger.info("检测到插件启动事件，正在初始化【主动思考】")
        # 检查总开关
        if global_config.proactive_thinking.enable:
            bot_start_time = time.time()  # 记录“诞生时刻”

            # 启动负责“日常唤醒”的核心任务
            proactive_task = ProactiveThinkingTask()
            await async_task_manager.add_task(proactive_task)

            # 检查“冷启动”功能的独立开关
            if global_config.proactive_thinking.enable_cold_start:
                cold_start_task = ColdStartTask(bot_start_time)
                await async_task_manager.add_task(cold_start_task)

        else:
            logger.info("【主动思考】功能未启用，所有任务均跳过启动。")
        return HandlerResult(success=True, continue_process=True, message=None)
