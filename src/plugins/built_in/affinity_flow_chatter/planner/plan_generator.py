"""
PlanGenerator: 负责搜集和汇总所有决策所需的信息，生成一个未经筛选的"原始计划" (Plan)。
"""

import time
from typing import TYPE_CHECKING

from src.chat.utils.chat_message_builder import get_raw_msg_before_timestamp_with_chat
from src.chat.utils.utils import get_chat_type_and_target_info
from src.common.data_models.database_data_model import DatabaseMessages
from src.common.data_models.info_data_model import Plan, TargetPersonInfo
from src.config.config import global_config
from src.plugin_system.base.component_types import ActionInfo, ChatMode, ChatType

if TYPE_CHECKING:
    from src.chat.planner_actions.action_manager import ChatterActionManager


class ChatterPlanGenerator:
    """
    ChatterPlanGenerator 负责在规划流程的初始阶段收集所有必要信息。

    它会汇总以下信息来构建一个"原始"的 Plan 对象，该对象后续会由 PlanFilter 进行筛选：
    -   当前聊天信息 (ID, 目标用户)
    -   当前可用的动作列表
    -   最近的聊天历史记录

    Attributes:
        chat_id (str): 当前聊天的唯一标识符。
        action_manager (ActionManager): 用于获取可用动作列表的管理器。
    """

    def __init__(self, chat_id: str, action_manager: "ChatterActionManager"):
        """
        初始化 ChatterPlanGenerator。

        Args:
            chat_id (str): 当前聊天的 ID。
            action_manager (ChatterActionManager): 一个 ChatterActionManager 实例。
        """
        self.chat_id = chat_id
        self.action_manager = action_manager

    async def generate(self, mode: ChatMode) -> Plan:
        """
        收集所有信息，生成并返回一个初始的 Plan 对象。

        这个 Plan 对象包含了决策所需的所有上下文信息。

        Args:
            mode (ChatMode): 当前的聊天模式。

        Returns:
            Plan: 包含所有上下文信息的初始计划对象。
        """
        try:
            # 获取聊天类型和目标信息
            chat_type, target_info = await get_chat_type_and_target_info(self.chat_id)
            if chat_type:
                chat_type = ChatType.GROUP
            else:
                #遇到未知类型也当私聊处理
                chat_type = ChatType.PRIVATE

            # 获取可用动作列表
            available_actions = await self._get_available_actions(chat_type, mode)

            # 获取聊天历史记录
            recent_messages = await self._get_recent_messages()

            # 构建计划对象
            # 使用 target_info 字典创建 TargetPersonInfo 实例
            target_person_info = TargetPersonInfo(**target_info) if target_info else TargetPersonInfo()

            # 构建计划对象
            plan = Plan(
                chat_id=self.chat_id,
                chat_type=chat_type,
                mode=mode,
                target_info=target_person_info,
                available_actions=available_actions,
                chat_history=recent_messages,
            )

            return plan

        except Exception:
            # 如果生成失败，返回一个基本的空计划
            return Plan(
                chat_type = ChatType.PRIVATE,#空计划默认当成私聊
                chat_id=self.chat_id,
                mode=mode,
                target_info=TargetPersonInfo(),
                available_actions={},
                chat_history=[],
            )

    async def _get_available_actions(self, chat_type: ChatType, mode: ChatMode) -> dict[str, ActionInfo]:
        """
        获取当前可用的动作列表。

        Args:
            chat_type (ChatType): 聊天类型。
            mode (ChatMode): 聊天模式。

        Returns:
            Dict[str, ActionInfo]: 可用动作的字典。
        """
        try:
            # 从组件注册表获取可用动作
            available_actions = self.action_manager.get_using_actions()

            # 根据聊天类型和模式筛选动作
            filtered_actions = {}
            for action_name, action_info in available_actions.items():
                # 检查动作是否支持当前聊天类型
                chat_type_allowed = (
                    isinstance(action_info.chat_type_allow, list)
                    and (ChatType.ALL in action_info.chat_type_allow or chat_type in action_info.chat_type_allow)
                ) or action_info.chat_type_allow == ChatType.ALL or action_info.chat_type_allow == chat_type

                # 检查动作是否支持当前模式
                mode_allowed = (
                    isinstance(action_info.mode_enable, list)
                    and (ChatMode.ALL in action_info.mode_enable or mode in action_info.mode_enable)
                ) or action_info.mode_enable == ChatMode.ALL or action_info.mode_enable == mode

                if chat_type_allowed and mode_allowed:
                    filtered_actions[action_name] = action_info

            return filtered_actions

        except Exception:
            # 如果获取失败，返回空字典
            return {}

    async def _get_recent_messages(self) -> list[DatabaseMessages]:
        """
        获取最近的聊天历史记录。

        Returns:
            list[DatabaseMessages]: 最近的聊天消息列表。
        """
        try:
            # 获取最近的消息记录
            raw_messages = await get_raw_msg_before_timestamp_with_chat(
                chat_id=self.chat_id, timestamp=time.time(), limit=global_config.chat.max_context_size
            )

            # 转换为 DatabaseMessages 对象
            recent_messages = []
            for msg in raw_messages:
                try:
                    db_msg = DatabaseMessages(
                        message_id=msg.get("message_id", ""),
                        time=float(msg.get("time", 0)),
                        chat_id=msg.get("chat_id", ""),
                        processed_plain_text=msg.get("processed_plain_text", ""),
                        user_id=msg.get("user_id", ""),
                        user_nickname=msg.get("user_nickname", ""),
                        user_platform=msg.get("user_platform", ""),
                    )
                    recent_messages.append(db_msg)
                except Exception:
                    # 跳过格式错误的消息
                    continue

            return recent_messages

        except Exception:
            # 如果获取失败，返回空列表
            return []

    def get_generator_stats(self) -> dict:
        """
        获取生成器统计信息。

        Returns:
            Dict: 统计信息字典。
        """
        return {
            "chat_id": self.chat_id,
            "action_count": len(self.action_manager._using_actions)
            if hasattr(self.action_manager, "_using_actions")
            else 0,
            "generation_time": time.time(),
        }
