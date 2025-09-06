import orjson
import time
import traceback
import asyncio
import math
import random
import json
from typing import Dict, Any, Optional, Tuple, List, TYPE_CHECKING
from rich.traceback import install
from datetime import datetime
from json_repair import repair_json

from src.llm_models.utils_model import LLMRequest
from src.config.config import global_config, model_config
from src.common.logger import get_logger
from src.chat.utils.prompt import Prompt, global_prompt_manager
from src.chat.utils.chat_message_builder import (
    build_readable_actions,
    get_actions_by_timestamp_with_chat,
    build_readable_messages_with_id,
    get_raw_msg_before_timestamp_with_chat,
)
from src.chat.utils.utils import get_chat_type_and_target_info
from src.chat.planner_actions.action_manager import ActionManager
from src.chat.message_receive.chat_stream import get_chat_manager
from src.plugin_system.base.component_types import ActionInfo, ChatMode, ComponentType, ActionActivationType
from src.plugin_system.core.component_registry import component_registry
from src.schedule.schedule_manager import schedule_manager
from src.mood.mood_manager import mood_manager
from src.chat.memory_system.Hippocampus import hippocampus_manager

if TYPE_CHECKING:
    pass

logger = get_logger("planner")

install(extra_lines=3)


def init_prompt():
    Prompt(
        """
{schedule_block}
{mood_block}
{time_block}
{identity_block}

{custom_prompt_block}
{chat_context_description}，以下是具体的聊天内容。
{chat_content_block}

{moderation_prompt}

现在请你根据聊天内容和用户的最新消息选择合适的action和触发action的消息:
{actions_before_now_block}

{no_action_block}

动作：reply
动作描述：参与聊天回复，发送文本进行表达
- 你想要闲聊或者随便附
- {mentioned_bonus}
- 如果你刚刚进行了回复，不要对同一个话题重复回应
- 不要回复自己发送的消息
{{
    "action": "reply",
    "target_message_id":"触发action的消息id",
    "reason":"回复的原因"
}}

{action_options_text}

你必须从上面列出的可用action中选择一个，并说明触发action的消息id（不是消息原文）和选择该action的原因。消息id格式:m+数字

请根据动作示例，以严格的 JSON 格式输出，不要输出markdown格式```json等内容，直接输出且仅包含 JSON 内容：
""",
        "planner_prompt",
    )

    Prompt(
        """
# 主动思考决策

## 你的内部状态
{time_block}
{identity_block}
{schedule_block}
{mood_block}

## 长期记忆摘要
{long_term_memory_block}

## 最近的聊天内容
{chat_content_block}

## 任务
基于以上所有信息（特别是最近的聊天内容），分析当前情况，决定是否适合主动开启一个**新的、但又与当前氛围相关**的话题。

## 可用动作
动作：proactive_reply
动作描述：在当前对话的基础上，主动发起一个新的对话，分享一个有趣的想法、见闻或者对未来的计划。
- 当你觉得可以说些什么来活跃气氛，并且内容与当前聊天氛围不冲突时
- 当你有一些新的想法或计划想要分享，并且可以自然地衔接当前话题时
{{
    "action": "proactive_reply",
    "reason": "决定主动发起对话的具体原因",
    "topic": "你想要发起对话的主题或内容（需要简洁）"
}}

动作：do_nothing
动作描述：保持沉默，不主动发起任何动作或对话。
- 当你分析了所有信息后，觉得当前不是一个发起互动的好时机时
- 当最近的聊天内容很连贯，你的插入会打断别人时
{{
    "action": "do_nothing",
    "reason":"决定保持沉默的具体原因"
}}

你必须从上面列出的可用action中选择一个。
请以严格的 JSON 格式输出，且仅包含 JSON 内容：
""",
        "proactive_planner_prompt",
    )

    Prompt(
        """
动作：{action_name}
动作描述：{action_description}
{action_require}
{{
    "action": "{action_name}",{action_parameters},
    "target_message_id":"触发action的消息id",
    "reason":"触发action的原因"
}}
""",
        "action_prompt",
    )

    Prompt(
        """
{name_block}

{chat_context_description}，{time_block}，现在请你根据以下聊天内容，选择一个或多个合适的action。如果没有合适的action，请选择no_action。,
{chat_content_block}

**要求**
1.action必须符合使用条件，如果符合条件，就选择
2.如果聊天内容不适合使用action，即使符合条件，也不要使用
3.{moderation_prompt}
4.请注意如果相同的内容已经被执行，请不要重复执行
这是你最近执行过的动作:
{actions_before_now_block}

**可用的action**

no_action：不选择任何动作
{{
    "action": "no_action",
    "reason":"不动作的原因"
}}

{action_options_text}

请选择，并说明触发action的消息id和选择该action的原因。消息id格式:m+数字
请根据动作示例，以严格的 JSON 格式输出，且仅包含 JSON 内容：
""",
        "sub_planner_prompt",
    )


class ActionPlanner:
    def __init__(self, chat_id: str, action_manager: ActionManager):
        self.chat_id = chat_id
        self.log_prefix = f"[{get_chat_manager().get_stream_name(chat_id) or chat_id}]"
        self.action_manager = action_manager
        # LLM规划器配置
        # --- 大脑 ---
        self.planner_llm = LLMRequest(
            model_set=model_config.model_task_config.planner, request_type="planner"
        )
        # --- 小脑 (新增) ---
        self.planner_small_llm = LLMRequest(
            model_set=model_config.model_task_config.planner_small, request_type="planner_small"
        )

        self.last_obs_time_mark = 0.0

    async def _get_long_term_memory_context(self) -> str:
        """
        获取长期记忆上下文
        """
        try:
            # 1. 生成时间相关的关键词
            now = datetime.now()
            keywords = ["今天", "日程", "计划"]
            if 5 <= now.hour < 12:
                keywords.append("早上")
            elif 12 <= now.hour < 18:
                keywords.append("中午")
            else:
                keywords.append("晚上")

            # TODO: 添加与聊天对象相关的关键词

            # 2. 调用 hippocampus_manager 检索记忆
            retrieved_memories = await hippocampus_manager.get_memory_from_topic(
                valid_keywords=keywords, max_memory_num=5, max_memory_length=1
            )

            if not retrieved_memories:
                return "最近没有什么特别的记忆。"

            # 3. 格式化记忆
            memory_statements = []
            for topic, memory_item in retrieved_memories:
                memory_statements.append(f"关于'{topic}', 你记得'{memory_item}'。")

            return " ".join(memory_statements)
        except Exception as e:
            logger.error(f"获取长期记忆时出错: {e}")
            return "回忆时出现了一些问题。"

    async def _build_action_options(
        self,
        current_available_actions: Dict[str, ActionInfo],
        mode: ChatMode,
        target_prompt: str = "",
    ) -> str:
        """
        构建动作选项
        """
        action_options_block = ""

        if mode == ChatMode.PROACTIVE:
            action_options_block += """动作：do_nothing
动作描述：保持沉默，不主动发起任何动作或对话。
- 当你分析了所有信息后，觉得当前不是一个发起互动的好时机时
{{
    "action": "do_nothing",
    "reason":"决定保持沉默的具体原因"
}}

"""
        for action_name, action_info in current_available_actions.items():
            # TODO: 增加一个字段来判断action是否支持在PROACTIVE模式下使用

            param_text = ""
            if action_info.action_parameters:
                param_text = "\n" + "\n".join(
                    f'    "{p_name}":"{p_desc}"' for p_name, p_desc in action_info.action_parameters.items()
                )

            require_text = "\n".join(f"- {req}" for req in action_info.action_require)

            using_action_prompt = await global_prompt_manager.get_prompt_async("action_prompt")
            action_options_block += using_action_prompt.format(
                action_name=action_name,
                action_description=action_info.description,
                action_parameters=param_text,
                action_require=require_text,
            )
        return action_options_block

    def find_message_by_id(self, message_id: str, message_id_list: list) -> Optional[Dict[str, Any]]:
        # sourcery skip: use-next
        """
        根据message_id从message_id_list中查找对应的原始消息

        Args:
            message_id: 要查找的消息ID
            message_id_list: 消息ID列表，格式为[{'id': str, 'message': dict}, ...]

        Returns:
            找到的原始消息字典，如果未找到则返回None
        """
        # 检测message_id 是否为纯数字
        if message_id.isdigit():
            message_id = f"m{message_id}"
        for item in message_id_list:
            if item.get("id") == message_id:
                return item.get("message")
        return None

    def get_latest_message(self, message_id_list: list) -> Optional[Dict[str, Any]]:
        """
        获取消息列表中的最新消息

        Args:
            message_id_list: 消息ID列表，格式为[{'id': str, 'message': dict}, ...]

        Returns:
            最新的消息字典，如果列表为空则返回None
        """
        if not message_id_list:
            return None
        # 假设消息列表是按时间顺序排列的，最后一个是最新的
        return message_id_list[-1].get("message")

    def _parse_single_action(
        self,
        action_json: dict,
        message_id_list: list,  # 使用 planner.py 的 list of dict
        current_available_actions: list,  # 使用 planner.py 的 list of tuple
    ) -> List[Dict[str, Any]]:
        """
        [注释] 解析单个小脑LLM返回的action JSON，并将其转换为标准化的字典。
        """
        parsed_actions = []
        try:
            action = action_json.get("action", "no_action")
            reasoning = action_json.get("reason", "未提供原因")
            action_data = {k: v for k, v in action_json.items() if k not in ["action", "reason"]}

            target_message = None
            if action != "no_action":
                if target_message_id := action_json.get("target_message_id"):
                    target_message = self.find_message_by_id(target_message_id, message_id_list)
                    if target_message is None:
                        logger.warning(f"{self.log_prefix}无法找到target_message_id '{target_message_id}'")
                        target_message = self.get_latest_message(message_id_list)
                else:
                    logger.warning(f"{self.log_prefix}动作'{action}'缺少target_message_id")

            available_action_names = [name for name, _ in current_available_actions]
            if action not in ["no_action", "reply"] and action not in available_action_names:
                logger.warning(
                    f"{self.log_prefix}LLM 返回了当前不可用或无效的动作: '{action}' (可用: {available_action_names})，将强制使用 'no_action'"
                )
                reasoning = f"LLM 返回了当前不可用的动作 '{action}' (可用: {available_action_names})。原始理由: {reasoning}"
                action = "no_action"

            # 将列表转换为字典格式以供将来使用
            available_actions_dict = dict(current_available_actions)
            parsed_actions.append(
                {
                    "action_type": action,
                    "reasoning": reasoning,
                    "action_data": action_data,
                    "action_message": target_message,
                    "available_actions": available_actions_dict,
                }
            )
        except Exception as e:
            logger.error(f"{self.log_prefix}解析单个action时出错: {e}")
            parsed_actions.append(
                {
                    "action_type": "no_action",
                    "reasoning": f"解析action时出错: {e}",
                    "action_data": {},
                    "action_message": None,
                    "available_actions": dict(current_available_actions),
                }
            )
        return parsed_actions

    def _filter_no_actions(self, action_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        [注释] 从一个action字典列表中过滤掉所有的 'no_action'。
        如果过滤后列表为空, 则返回一个空的列表, 或者根据需要返回一个默认的no_action字典。
        """
        non_no_actions = [a for a in action_list if a.get("action_type") not in ["no_action", "no_reply"]]
        if non_no_actions:
            return non_no_actions
        # 如果都是 no_action，则返回一个包含第一个 no_action 的列表，以保留 reason
        return action_list[:1] if action_list else []

    async def sub_plan(
        self,
        action_list: list,  # 使用 planner.py 的 list of tuple
        chat_content_block: str,
        message_id_list: list,  # 使用 planner.py 的 list of dict
        is_group_chat: bool = False,
        chat_target_info: Optional[dict] = None,
    ) -> List[Dict[str, Any]]:
        """
        [注释] "小脑"规划器。接收一小组actions，使用轻量级LLM判断其中哪些应该被触发。
        这是一个独立的、并行的思考单元。返回一个包含action字典的列表。
        """
        try:
            actions_before_now = get_actions_by_timestamp_with_chat(
                chat_id=self.chat_id,
                timestamp_start=time.time() - 1200,
                timestamp_end=time.time(),
                limit=20,
            )
            action_names_in_list = [name for name, _ in action_list]
            filtered_actions = [
                record for record in actions_before_now if record.get("action_name") in action_names_in_list
            ]
            actions_before_now_block = build_readable_actions(actions=filtered_actions)

            chat_context_description = "你现在正在一个群聊中"
            if not is_group_chat and chat_target_info:
                chat_target_name = chat_target_info.get("person_name") or chat_target_info.get("user_nickname") or "对方"
                chat_context_description = f"你正在和 {chat_target_name} 私聊"

            action_options_block = ""
            for using_actions_name, using_actions_info in action_list:
                param_text = ""
                if using_actions_info.action_parameters:
                    param_text = "\n" + "\n".join(
                        f'    "{p_name}":"{p_desc}"'
                        for p_name, p_desc in using_actions_info.action_parameters.items()
                    )
                require_text = "\n".join(f"- {req}" for req in using_actions_info.action_require)
                using_action_prompt = await global_prompt_manager.get_prompt_async("action_prompt")
                action_options_block += using_action_prompt.format(
                    action_name=using_actions_name,
                    action_description=using_actions_info.description,
                    action_parameters=param_text,
                    action_require=require_text,
                )

            moderation_prompt_block = "请不要输出违法违规内容，不要输出色情，暴力，政治相关内容，如有敏感内容，请规避。"
            time_block = f"当前时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            bot_name = global_config.bot.nickname
            bot_nickname = f",也有人叫你{','.join(global_config.bot.alias_names)}" if global_config.bot.alias_names else ""
            name_block = f"你的名字是{bot_name}{bot_nickname}，请注意哪些是你自己的发言。"

            planner_prompt_template = await global_prompt_manager.get_prompt_async("sub_planner_prompt")
            prompt = planner_prompt_template.format(
                time_block=time_block,
                chat_context_description=chat_context_description,
                chat_content_block=chat_content_block,
                actions_before_now_block=actions_before_now_block,
                action_options_text=action_options_block,
                moderation_prompt=moderation_prompt_block,
                name_block=name_block,
            )
        except Exception as e:
            logger.error(f"构建小脑提示词时出错: {e}\n{traceback.format_exc()}")
            return [{"action_type": "no_action", "reasoning": f"构建小脑Prompt时出错: {e}"}]

        action_dicts: List[Dict[str, Any]] = []
        try:
            llm_content, (reasoning_content, _, _) = await self.planner_small_llm.generate_response_async(prompt=prompt)
            if global_config.debug.show_prompt:
                logger.info(f"{self.log_prefix}小脑原始提示词: {prompt}")
                logger.info(f"{self.log_prefix}小脑原始响应: {llm_content}")
            else:
                logger.debug(f"{self.log_prefix}小脑原始响应: {llm_content}")

            if llm_content:
                parsed_json = orjson.loads(repair_json(llm_content))
                if isinstance(parsed_json, list):
                    for item in parsed_json:
                        if isinstance(item, dict):
                            action_dicts.extend(self._parse_single_action(item, message_id_list, action_list))
                elif isinstance(parsed_json, dict):
                    action_dicts.extend(self._parse_single_action(parsed_json, message_id_list, action_list))

        except Exception as e:
            logger.warning(f"{self.log_prefix}解析小脑响应JSON失败: {e}. LLM原始输出: '{llm_content}'")
            action_dicts.append({"action_type": "no_action", "reasoning": f"解析小脑响应失败: {e}"})

        if not action_dicts:
            action_dicts.append({"action_type": "no_action", "reasoning": "小脑未返回有效action"})

        return action_dicts

    async def plan(
        self,
        mode: ChatMode = ChatMode.FOCUS,
        loop_start_time: float = 0.0,
        available_actions: Optional[Dict[str, ActionInfo]] = None,
    ) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """
        [注释] "大脑"规划器。
        1. 启动多个并行的"小脑"(sub_plan)来决定是否执行具体的actions。
        2. 自己(大脑)则专注于决定是否进行聊天回复(reply)。
        3. 整合大脑和小脑的决策，返回最终要执行的动作列表。
        """
        # --- 1. 准备上下文信息 ---
        message_list_before_now = get_raw_msg_before_timestamp_with_chat(
            chat_id=self.chat_id,
            timestamp=time.time(),
            limit=int(global_config.chat.max_context_size * 0.6),
        )
        # 大脑使用较长的上下文
        chat_content_block, message_id_list = build_readable_messages_with_id(
            messages=message_list_before_now,
            timestamp_mode="normal",
            read_mark=self.last_obs_time_mark,
            truncate=True,
            show_actions=True,
        )
        # 小脑使用较短、较新的上下文
        message_list_before_now_short = message_list_before_now[-int(global_config.chat.max_context_size * 0.3) :]
        chat_content_block_short, message_id_list_short = build_readable_messages_with_id(
            messages=message_list_before_now_short,
            timestamp_mode="normal",
            truncate=False,
            show_actions=False,
        )
        self.last_obs_time_mark = time.time()

        is_group_chat, chat_target_info, current_available_actions = self.get_necessary_info()
        if available_actions is None:
            available_actions = current_available_actions

        # --- 2. 启动小脑并行思考 ---
        all_sub_planner_results: List[Dict[str, Any]] = []
        try:
            sub_planner_actions: Dict[str, ActionInfo] = {}
            for action_name, action_info in available_actions.items():
                if action_info.activation_type in [ActionActivationType.LLM_JUDGE, ActionActivationType.ALWAYS]:
                    sub_planner_actions[action_name] = action_info
                elif action_info.activation_type == ActionActivationType.RANDOM:
                    if random.random() < action_info.random_activation_probability:
                        sub_planner_actions[action_name] = action_info
                elif action_info.activation_type == ActionActivationType.KEYWORD:
                    if any(keyword in chat_content_block_short for keyword in action_info.activation_keywords):
                        sub_planner_actions[action_name] = action_info

            if sub_planner_actions:
                sub_planner_actions_num = len(sub_planner_actions)
                planner_size_config = global_config.chat.planner_size
                sub_planner_size = int(planner_size_config) + (
                    1 if random.random() < planner_size_config - int(planner_size_config) else 0
                )
                sub_planner_num = math.ceil(sub_planner_actions_num / sub_planner_size)
                logger.info(f"{self.log_prefix}使用{sub_planner_num}个小脑进行思考 (尺寸: {sub_planner_size})")

                action_items = list(sub_planner_actions.items())
                random.shuffle(action_items)
                sub_planner_lists = [action_items[i::sub_planner_num] for i in range(sub_planner_num)]

                sub_plan_tasks = [
                    self.sub_plan(
                        action_list=action_group,
                        chat_content_block=chat_content_block_short,
                        message_id_list=message_id_list_short,
                        is_group_chat=is_group_chat,
                        chat_target_info=chat_target_info,
                    )
                    for action_group in sub_planner_lists
                ]
                sub_plan_results = await asyncio.gather(*sub_plan_tasks)
                for sub_result in sub_plan_results:
                    all_sub_planner_results.extend(sub_result)
                
                sub_actions_str = ", ".join(
                    a["action_type"] for a in all_sub_planner_results if a["action_type"] != "no_action"
                ) or "no_action"
                logger.info(f"{self.log_prefix}小脑决策: [{sub_actions_str}]")

        except Exception as e:
            logger.error(f"{self.log_prefix}小脑调度过程中出错: {e}\n{traceback.format_exc()}")

        # --- 3. 大脑独立思考是否回复 ---
        action, reasoning, action_data, target_message = "no_reply", "大脑初始化默认", {}, None
        try:
            prompt, _ = await self.build_planner_prompt(
                is_group_chat=is_group_chat,
                chat_target_info=chat_target_info,
                current_available_actions={},  # 大脑不考虑具体action
                mode=mode,
                chat_content_block_override=chat_content_block,
                message_id_list_override=message_id_list,
            )
            llm_content, _ = await self.planner_llm.generate_response_async(prompt=prompt)

            if llm_content:
                parsed_json = orjson.loads(repair_json(llm_content))
                parsed_json = parsed_json[-1] if isinstance(parsed_json, list) and parsed_json else parsed_json
                if isinstance(parsed_json, dict):
                    action = parsed_json.get("action", "no_reply")
                    reasoning = parsed_json.get("reason", "未提供原因")
                    action_data = {k: v for k, v in parsed_json.items() if k not in ["action", "reason"]}
                    if action != "no_reply":
                        if target_id := parsed_json.get("target_message_id"):
                            target_message = self.find_message_by_id(target_id, message_id_list)
                        if not target_message:
                            target_message = self.get_latest_message(message_id_list)
            logger.info(f"{self.log_prefix}大脑决策: [{action}]")

        except Exception as e:
            logger.error(f"{self.log_prefix}大脑处理过程中发生意外错误: {e}\n{traceback.format_exc()}")
            action, reasoning = "no_reply", f"大脑处理错误: {e}"

        # --- 4. 整合大脑和小脑的决策 ---
        is_parallel = True
        for info in all_sub_planner_results:
            action_type = info.get("action_type")
            if action_type and action_type not in ["no_action", "no_reply"]:
                action_info = available_actions.get(action_type)
                if action_info and not action_info.parallel_action:
                    is_parallel = False
                    break

        action_data["loop_start_time"] = loop_start_time
        final_actions: List[Dict[str, Any]] = []

        if is_parallel:
            logger.info(f"{self.log_prefix}决策模式: 大脑与小脑并行")
            if action not in ["no_action", "no_reply"]:
                final_actions.append(
                    {
                        "action_type": action,
                        "reasoning": reasoning,
                        "action_data": action_data,
                        "action_message": target_message,
                        "available_actions": available_actions,
                    }
                )
            final_actions.extend(all_sub_planner_results)
        else:
            logger.info(f"{self.log_prefix}决策模式: 小脑优先 (检测到非并行action)")
            final_actions.extend(all_sub_planner_results)

        final_actions = self._filter_no_actions(final_actions)

        if not final_actions:
            final_actions = [
                {
                    "action_type": "no_action",
                    "reasoning": "所有规划器都选择不执行动作",
                    "action_data": {}, "action_message": None, "available_actions": available_actions
                }
            ]

        final_target_message = target_message
        if not final_target_message and final_actions:
            final_target_message = next((act.get("action_message") for act in final_actions if act.get("action_message")), None)

        actions_str = ", ".join([a.get('action_type', 'N/A') for a in final_actions])
        logger.info(f"{self.log_prefix}最终执行动作 ({len(final_actions)}): [{actions_str}]")
        
        return final_actions, final_target_message

    async def build_planner_prompt(
        self,
        is_group_chat: bool,
        chat_target_info: Optional[dict],
        current_available_actions: Dict[str, ActionInfo],
        mode: ChatMode = ChatMode.FOCUS,
        chat_content_block_override: Optional[str] = None,
        message_id_list_override: Optional[List] = None,
        refresh_time: bool = False,  # 添加缺失的参数
    ) -> tuple[str, list]:
        """构建 Planner LLM 的提示词 (获取模板并填充数据)"""
        try:
            # --- 通用信息获取 ---
            time_block = f"当前时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            bot_name = global_config.bot.nickname
            bot_nickname = (
                f",也有人叫你{','.join(global_config.bot.alias_names)}" if global_config.bot.alias_names else ""
            )
            bot_core_personality = global_config.personality.personality_core
            identity_block = f"你的名字是{bot_name}{bot_nickname}，你{bot_core_personality}："

            schedule_block = ""
            if global_config.planning_system.schedule_enable:
                if current_activity := schedule_manager.get_current_activity():
                    schedule_block = f"你当前正在：{current_activity},但注意它与群聊的聊天无关。"

            mood_block = ""
            if global_config.mood.enable_mood:
                chat_mood = mood_manager.get_mood_by_chat_id(self.chat_id)
                mood_block = f"你现在的心情是：{chat_mood.mood_state}"

            # --- 根据模式构建不同的Prompt ---
            if mode == ChatMode.PROACTIVE:
                long_term_memory_block = await self._get_long_term_memory_context()
                
                # 获取最近的聊天记录用于主动思考决策
                message_list_short = get_raw_msg_before_timestamp_with_chat(
                    chat_id=self.chat_id,
                    timestamp=time.time(),
                    limit=int(global_config.chat.max_context_size * 0.2), # 主动思考时只看少量最近消息
                )
                chat_content_block, _ = build_readable_messages_with_id(
                    messages=message_list_short,
                    timestamp_mode="normal",
                    truncate=False,
                    show_actions=False,
                )

                prompt_template = await global_prompt_manager.get_prompt_async("proactive_planner_prompt")
                prompt = prompt_template.format(
                    time_block=time_block,
                    identity_block=identity_block,
                    schedule_block=schedule_block,
                    mood_block=mood_block,
                    long_term_memory_block=long_term_memory_block,
                    chat_content_block=chat_content_block or "最近没有聊天内容。",
                )
                return prompt, []

            # --- FOCUS 和 NORMAL 模式的逻辑 ---
            message_list_before_now = get_raw_msg_before_timestamp_with_chat(
                chat_id=self.chat_id,
                timestamp=time.time(),
                limit=int(global_config.chat.max_context_size * 0.6),
            )

            chat_content_block, message_id_list = build_readable_messages_with_id(
                messages=message_list_before_now,
                timestamp_mode="normal",
                read_mark=self.last_obs_time_mark,
                truncate=True,
                show_actions=True,
            )

            actions_before_now = get_actions_by_timestamp_with_chat(
                chat_id=self.chat_id,
                timestamp_start=time.time() - 3600,
                timestamp_end=time.time(),
                limit=5,
            )

            actions_before_now_block = build_readable_actions(actions=actions_before_now)
            actions_before_now_block = f"你刚刚选择并执行过的action是：\n{actions_before_now_block}"

            if refresh_time:
                self.last_obs_time_mark = time.time()

            mentioned_bonus = ""
            if global_config.chat.mentioned_bot_inevitable_reply:
                mentioned_bonus = "\n- 有人提到你"
            if global_config.chat.at_bot_inevitable_reply:
                mentioned_bonus = "\n- 有人提到你，或者at你"

            if mode == ChatMode.FOCUS:
                no_action_block = """
- 'no_reply' 表示不进行回复，等待合适的回复时机
- 当你刚刚发送了消息，没有人回复时，选择no_reply
- 当你一次发送了太多消息，为了避免打扰聊天节奏，选择no_reply
动作：no_reply
动作描述：不进行回复，等待合适的回复时机
- 当你刚刚发送了消息，没有人回复时，选择no_reply
- 当你一次发送了太多消息，为了避免打扰聊天节奏，选择no_reply
{{
    "action": "no_reply",
    "reason":"不回复的原因"
}}
"""
            else:  # NORMAL Mode
                no_action_block = """重要说明：
- 'reply' 表示只进行普通聊天回复，不执行任何额外动作
- 其他action表示在普通回复的基础上，执行相应的额外动作
{{
    "action": "reply",
    "target_message_id":"触发action的消息id",
    "reason":"回复的原因"
}}"""

            chat_context_description = "你现在正在一个群聊中"
            chat_target_name = None
            if not is_group_chat and chat_target_info:
                chat_target_name = (
                    chat_target_info.get("person_name") or chat_target_info.get("user_nickname") or "对方"
                )
                chat_context_description = f"你正在和 {chat_target_name} 私聊"

            action_options_block = await self._build_action_options(current_available_actions, mode)

            moderation_prompt_block = "请不要输出违法违规内容，不要输出色情，暴力，政治相关内容，如有敏感内容，请规避。"

            custom_prompt_block = ""
            if global_config.custom_prompt.planner_custom_prompt_content:
                custom_prompt_block = global_config.custom_prompt.planner_custom_prompt_content

            planner_prompt_template = await global_prompt_manager.get_prompt_async("planner_prompt")
            prompt = planner_prompt_template.format(
                schedule_block=schedule_block,
                mood_block=mood_block,
                time_block=time_block,
                chat_context_description=chat_context_description,
                chat_content_block=chat_content_block,
                actions_before_now_block=actions_before_now_block,
                mentioned_bonus=mentioned_bonus,
                no_action_block=no_action_block,
                action_options_text=action_options_block,
                moderation_prompt=moderation_prompt_block,
                identity_block=identity_block,
                custom_prompt_block=custom_prompt_block,
                bot_name=bot_name,
            )
            return prompt, message_id_list
        except Exception as e:
            logger.error(f"构建 Planner 提示词时出错: {e}")
            logger.error(traceback.format_exc())
            return "构建 Planner Prompt 时出错", []

    def get_necessary_info(self) -> Tuple[bool, Optional[dict], Dict[str, ActionInfo]]:
        """
        获取 Planner 需要的必要信息
        """
        is_group_chat = True
        is_group_chat, chat_target_info = get_chat_type_and_target_info(self.chat_id)
        logger.debug(f"{self.log_prefix}获取到聊天信息 - 群聊: {is_group_chat}, 目标信息: {chat_target_info}")

        current_available_actions_dict = self.action_manager.get_using_actions()

        # 获取完整的动作信息
        all_registered_actions: Dict[str, ActionInfo] = component_registry.get_components_by_type(  # type: ignore
            ComponentType.ACTION
        )
        current_available_actions = {}
        for action_name in current_available_actions_dict:
            if action_name in all_registered_actions:
                current_available_actions[action_name] = all_registered_actions[action_name]
            else:
                logger.warning(f"{self.log_prefix}使用中的动作 {action_name} 未在已注册动作中找到")

        # 将no_reply作为系统级特殊动作添加到可用动作中
        # no_reply虽然是系统级决策，但需要让规划器认为它是可用的
        no_reply_info = ActionInfo(
            name="no_reply",
            component_type=ComponentType.ACTION,
            description="系统级动作：选择不回复消息的决策",
            action_parameters={},
            activation_keywords=[],
            plugin_name="SYSTEM",
            enabled=True,  # 始终启用
            parallel_action=False,
        )
        current_available_actions["no_reply"] = no_reply_info

        return is_group_chat, chat_target_info, current_available_actions


init_prompt()
