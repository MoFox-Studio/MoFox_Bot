import time
from datetime import datetime
from typing import Any

import orjson

from src.chat.utils.chat_message_builder import build_readable_actions, get_actions_by_timestamp_with_chat
from src.chat.utils.prompt import Prompt
from src.common.logger import get_logger
from src.config.config import global_config, model_config
from src.mood.mood_manager import mood_manager
from src.person_info.person_info import get_person_info_manager
from src.plugin_system.apis import (
    chat_api,
    database_api,
    generator_api,
    llm_api,
    message_api,
    person_api,
    schedule_api,
    send_api,
)

from .prompts import DECISION_PROMPT, PLAN_PROMPT

logger = get_logger(__name__)


class ProactiveThinkerExecutor:
    """
    主动思考执行器 V2
    - 统一执行入口
    - 引入决策模块，判断是否及如何发起对话
    - 结合人设、日程、关系信息生成更具情境的对话
    """

    def __init__(self):
        """
        初始化 ProactiveThinkerExecutor 实例。
        目前无需初始化操作。
        """
        pass

    async def execute(self, stream_id: str, start_mode: str = "wake_up"):
        """
        统一执行入口
        Args:
            stream_id: 聊天流ID
            start_mode: 启动模式, 'cold_start' 或 'wake_up'
        """
        logger.info(f"开始为聊天流 {stream_id} 执行主动思考，模式: {start_mode}")

        # 1. 信息收集
        context = await self._gather_context(stream_id)
        if not context:
            return

        # 2. 决策阶段
        decision_result = await self._make_decision(context, start_mode)

        if not decision_result or not decision_result.get("should_reply"):
            reason = decision_result.get("reason", "未提供") if decision_result else "决策过程返回None"
            logger.info(f"决策结果为：不回复。原因: {reason}")
            await database_api.store_action_info(
                chat_stream=self._get_stream_from_id(stream_id),
                action_name="proactive_decision",
                action_prompt_display=f"主动思考决定不回复,原因: {reason}",
                action_done=True,
                action_data=decision_result,
            )
            return

        # 3. 规划与执行阶段
        topic = decision_result.get("topic", "打个招呼")
        reason = decision_result.get("reason", "无")
        await database_api.store_action_info(
            chat_stream=self._get_stream_from_id(stream_id),
            action_name="proactive_decision",
            action_prompt_display=f"主动思考决定回复,原因: {reason},话题:{topic}",
            action_done=True,
            action_data=decision_result,
        )
        logger.info(f"决策结果为：回复。话题: {topic}")

        # 根据聊天类型构建特定上下文
        if context["chat_type"] == "private":
            user_info = context["user_info"]
            relationship = context["relationship"]
            target_user_or_group = f"你的朋友 '{user_info.user_nickname}'"
            context_specific_block = f"""
1.  **你的日程**:
{context["schedule_context"]}
2.  **你和Ta的关系**:
    - 详细印象: {relationship["impression"]}
    - 好感度: {relationship["attitude"]}/100
3.  **最近的聊天摘要**:
{context["recent_chat_history"]}
4.  **你最近的相关动作**:
{context["action_history_context"]}
"""
        else:  # group
            group_info = context["group_info"]
            target_user_or_group = f"群聊 '{group_info['group_name']}'"
            context_specific_block = f"""
1.  **你的日程**:
{context["schedule_context"]}
2.  **群聊信息**:
    - 群名称: {group_info["group_name"]}
3.  **最近的聊天摘要**:
{context["recent_chat_history"]}
4.  **你最近的相关动作**:
{context["action_history_context"]}
"""

        plan_prompt = PLAN_PROMPT.format(
            bot_nickname=global_config.bot.nickname,
            persona_core=context["persona"]["core"],
            persona_side=context["persona"]["side"],
            identity=context["persona"]["identity"],
            current_time=context["current_time"],
            target_user_or_group=target_user_or_group,
            reason=reason,
            topic=topic,
            context_specific_block=context_specific_block,
            mood_state=context["mood_state"],
        )

        if global_config.debug.show_prompt:
            logger.info(f"主动思考回复器原始提示词:{plan_prompt}")

        is_success, response, _, _ = await llm_api.generate_with_model(
            prompt=plan_prompt, model_config=model_config.model_task_config.replyer
        )

        if is_success and response:
            stream = self._get_stream_from_id(stream_id)
            if stream:
                # 使用消息分割器处理并发送消息
                reply_set = generator_api.process_human_text(response, enable_splitter=True, enable_chinese_typo=False)
                for reply_type, content in reply_set:
                    if reply_type == "text":
                        await send_api.text_to_stream(stream_id=stream.stream_id, text=content)
            else:
                logger.warning(f"无法发送消息，因为找不到 stream_id 为 {stream_id} 的聊天流")

    def _get_stream_from_id(self, stream_id: str):
        """
        根据 stream_id 解析并获取对应的聊天流对象。

        Args:
            stream_id: 聊天流的唯一标识符，格式为 "platform:chat_id:stream_type"。

        Returns:
            对应的 ChatStream 对象，如果解析失败或找不到则返回 None。
        """
        try:
            platform, chat_id, stream_type = stream_id.split(":")
            if stream_type == "private":
                return chat_api.ChatManager.get_private_stream_by_user_id(platform=platform, user_id=chat_id)
            elif stream_type == "group":
                return chat_api.ChatManager.get_group_stream_by_group_id(platform=platform, group_id=chat_id)
        except Exception as e:
            logger.error(f"获取 stream_id ({stream_id}) 失败: {e}")
            return None

    async def _gather_context(self, stream_id: str) -> dict[str, Any] | None:
        """
        收集构建决策和规划提示词所需的所有上下文信息。

        此函数会根据聊天流是私聊还是群聊，收集不同的信息，
        包括但不限于日程、聊天历史、人设、关系信息等。

        Args:
            stream_id: 聊天流ID。

        Returns:
            一个包含所有上下文信息的字典，如果找不到聊天流则返回 None。
        """
        stream = self._get_stream_from_id(stream_id)
        if not stream:
            logger.warning(f"无法找到 stream_id 为 {stream_id} 的聊天流")
            return None

        # 1. 收集通用信息 (日程, 聊天历史, 动作历史)
        schedules = await schedule_api.ScheduleAPI.get_today_schedule()
        schedule_context = (
            "\n".join([f"- {s.get('time_range', '未知时间')}: {s.get('activity', '未知活动')}" for s in schedules])
            if schedules
            else "今天没有日程安排。"
        )

        recent_messages = await message_api.get_recent_messages(
            stream.stream_id, limit=50, limit_mode="latest", hours=12
        )
        recent_chat_history = (
            await message_api.build_readable_messages_to_str(recent_messages) if recent_messages else "无"
        )

        action_history_list = await get_actions_by_timestamp_with_chat(
            chat_id=stream.stream_id,
            timestamp_start=time.time() - 3600 * 24,  # 过去24小时
            timestamp_end=time.time(),
            limit=7,
        )

        action_history_context = build_readable_actions(actions=action_history_list)

        # 2. 构建基础上下文
        mood_state = "暂时没有"
        if global_config.mood.enable_mood:
            try:
                mood_state = mood_manager.get_mood_by_chat_id(stream.stream_id).mood_state
            except Exception as e:
                logger.error(f"获取情绪失败,原因:{e}")
        base_context = {
            "schedule_context": schedule_context,
            "recent_chat_history": recent_chat_history,
            "action_history_context": action_history_context,
            "mood_state": mood_state,
            "persona": {
                "core": global_config.personality.personality_core,
                "side": global_config.personality.personality_side,
                "identity": global_config.personality.identity,
            },
            "current_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        # 3. 根据聊天类型补充特定上下文
        if stream.group_info:  # 群聊场景
            base_context.update(
                {
                    "chat_type": "group",
                    "group_info": {"group_name": stream.group_info.group_name, "group_id": stream.group_info.group_id},
                }
            )
            return base_context
        elif stream.user_info:  # 私聊场景
            user_info = stream.user_info
            if not user_info.platform or not user_info.user_id:
                logger.warning(f"Stream {stream_id} 的 user_info 不完整")
                return None

            person_id = person_api.get_person_id(user_info.platform, int(user_info.user_id))
            person_info_manager = get_person_info_manager()
            person_info = await person_info_manager.get_values(person_id, ["user_id", "platform", "person_name"])
            cross_context_block = await Prompt.build_cross_context(stream.stream_id, "s4u", person_info)

            # 获取关系信息
            short_impression = await person_info_manager.get_value(person_id, "short_impression") or "无"
            impression = await person_info_manager.get_value(person_id, "impression") or "无"
            attitude = await person_info_manager.get_value(person_id, "attitude") or 50

            base_context.update(
                {
                    "chat_type": "private",
                    "person_id": person_id,
                    "user_info": user_info,
                    "cross_context_block": cross_context_block,
                    "relationship": {
                        "short_impression": short_impression,
                        "impression": impression,
                        "attitude": attitude,
                    },
                }
            )
            return base_context
        else:
            logger.warning(f"Stream {stream_id} 既没有 group_info 也没有 user_info")
            return None

    async def _make_decision(self, context: dict[str, Any], start_mode: str) -> dict[str, Any] | None:
        """
        调用 LLM 进行决策，判断是否应该主动发起对话，以及聊什么话题。
        """
        if context["chat_type"] not in ["private", "group"]:
            return {"should_reply": False, "reason": "未知的聊天类型"}

        # 根据聊天类型构建特定上下文
        if context["chat_type"] == "private":
            user_info = context["user_info"]
            relationship = context["relationship"]
            target_user_or_group = f"用户 '{user_info.user_nickname}'"
            context_specific_block = f"""
    1.  **启动模式**: {start_mode} ({"初次见面/很久未见" if start_mode == "cold_start" else "日常唤醒"})
    2.  **你的日程**:
    {context["schedule_context"]}
    3.  **你和Ta的关系**:
        - 简短印象: {relationship["short_impression"]}
        - 详细印象: {relationship["impression"]}
        - 好感度: {relationship["attitude"]}/100
    4.  **和Ta在别处的讨论摘要**:
    {context["cross_context_block"]}
    5.  **最近的聊天摘要**:
    {context["recent_chat_history"]}
    """
        else:  # group
            group_info = context["group_info"]
            target_user_or_group = f"群聊 '{group_info['group_name']}'"
            context_specific_block = f"""
    1.  **启动模式**: {start_mode} ({"首次加入/很久未发言" if start_mode == "cold_start" else "日常唤醒"})
    2.  **你的日程**:
    {context["schedule_context"]}
    3.  **群聊信息**:
        - 群名称: {group_info["group_name"]}
    4.  **最近的聊天摘要**:
    {context["recent_chat_history"]}
    """
        prompt = DECISION_PROMPT.format(
            bot_nickname=global_config.bot.nickname,
            persona_core=context["persona"]["core"],
            persona_side=context["persona"]["side"],
            identity=context["persona"]["identity"],
            mood_state=context["mood_state"],
            action_history_context=context["action_history_context"],
            current_time=context["current_time"],
            target_user_or_group=target_user_or_group,
            context_specific_block=context_specific_block,
        )

        if global_config.debug.show_prompt:
            logger.info(f"主动思考决策器原始提示词:{prompt}")

        is_success, response, _, _ = await llm_api.generate_with_model(
            prompt=prompt, model_config=model_config.model_task_config.utils
        )

        if not is_success:
            return {"should_reply": False, "reason": "决策模型生成失败"}

        try:
            if global_config.debug.show_prompt:
                logger.info(f"主动思考决策器响应:{response}")
            decision = orjson.loads(response)
            return decision
        except orjson.JSONDecodeError:
            logger.error(f"决策LLM返回的JSON格式无效: {response}")
            return {"should_reply": False, "reason": "决策模型返回格式错误"}
