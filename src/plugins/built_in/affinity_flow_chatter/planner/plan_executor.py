"""
PlanExecutor: 接收 Plan 对象并执行其中的所有动作。
集成用户关系追踪机制，自动记录交互并更新关系。
"""

import asyncio
import time
from typing import Any

from src.chat.planner_actions.action_manager import ChatterActionManager
from src.common.data_models.info_data_model import ActionPlannerInfo, Plan
from src.common.logger import get_logger
from src.config.config import global_config

logger = get_logger("plan_executor")

# 全局背景任务集合
_background_tasks = set()


class ChatterPlanExecutor:
    """
    增强版PlanExecutor，集成用户关系追踪机制。

    功能：
    1. 执行Plan中的所有动作
    2. 自动记录用户交互并添加到关系追踪
    3. 分类执行回复动作和其他动作
    4. 提供完整的执行统计和监控
    """

    def __init__(self, action_manager: ChatterActionManager):
        """
        初始化增强版PlanExecutor。

        Args:
            action_manager (ChatterActionManager): 用于实际执行各种动作的管理器实例。
        """
        self.action_manager = action_manager

        # 执行统计
        self.execution_stats = {
            "total_executed": 0,
            "successful_executions": 0,
            "failed_executions": 0,
            "reply_executions": 0,
            "other_action_executions": 0,
            "execution_times": [],
        }

    async def execute(self, plan: Plan) -> dict[str, Any]:
        """
        遍历并执行Plan对象中`decided_actions`列表里的所有动作。

        Args:
            plan (Plan): 包含待执行动作列表的Plan对象。

        Returns:
            Dict[str, any]: 执行结果统计信息
        """
        if not plan.decided_actions:
            logger.info("没有需要执行的动作。")
            return {"executed_count": 0, "results": []}

        # 像hfc一样，提前打印将要执行的动作
        action_types = [action.action_type for action in plan.decided_actions]
        logger.info(f"选择动作: {', '.join(action_types) if action_types else '无'}")

        # 根据配置决定是否启用批量存储模式
        if global_config.database.batch_action_storage_enabled:
            self.action_manager.enable_batch_storage(plan.chat_id)
            logger.debug("已启用批量存储模式")
        else:
            logger.debug("批量存储功能已禁用，使用立即存储模式")

        execution_results = []
        reply_actions = []
        other_actions = []

        # 分类动作：回复动作和其他动作
        # 回复类动作包括：reply, proactive_reply, respond
        for action_info in plan.decided_actions:
            if action_info.action_type in ["reply", "proactive_reply", "respond"]:
                reply_actions.append(action_info)
            else:
                other_actions.append(action_info)

        # 执行回复动作（优先执行）
        if reply_actions:
            reply_result = await self._execute_reply_actions(reply_actions, plan)
            execution_results.extend(reply_result["results"])
            self.execution_stats["reply_executions"] += len(reply_actions)

        # 将其他动作放入后台任务执行，避免阻塞主流程
        if other_actions:
            task = asyncio.create_task(self._execute_other_actions(other_actions, plan))
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)
            logger.info(f"已将 {len(other_actions)} 个其他动作放入后台任务执行。")
            # 注意：后台任务的结果不会立即计入本次返回的统计数据

        # 更新总体统计
        self.execution_stats["total_executed"] += len(plan.decided_actions)
        successful_count = sum(1 for r in execution_results if r["success"])
        self.execution_stats["successful_executions"] += successful_count
        self.execution_stats["failed_executions"] += len(execution_results) - successful_count

        logger.info(
            f"规划执行完成: 总数={len(plan.decided_actions)}, 成功={successful_count}, 失败={len(execution_results) - successful_count}"
        )

        # 批量存储所有待处理的动作
        await self._flush_action_manager_batch_storage(plan)

        return {
            "executed_count": len(plan.decided_actions),
            "successful_count": successful_count,
            "failed_count": len(execution_results) - successful_count,
            "results": execution_results,
        }

    async def _execute_reply_actions(self, reply_actions: list[ActionPlannerInfo], plan: Plan) -> dict[str, Any]:
        """串行执行所有回复动作，增加去重逻辑，避免对同一消息多次回复"""
        results = []

        # 首先根据配置决定是否允许多重回复
        if not global_config.chat.enable_multiple_replies and len(reply_actions) > 1:
            # 不允许多重回复，需要过滤
            filtered_actions = self._filter_multiple_replies(reply_actions)
            logger.info(f"[多重回复] 配置已禁用多重回复，从 {len(reply_actions)} 个回复中过滤为 {len(filtered_actions)} 个")
            reply_actions = filtered_actions

        # --- 消息ID去重逻辑 ---
        unique_actions = []
        replied_message_ids = set()
        for action_info in reply_actions:
            target_message = action_info.action_message
            message_id = None
            if target_message:
                message_id = target_message.message_id

            if message_id:
                if message_id not in replied_message_ids:
                    unique_actions.append(action_info)
                    replied_message_ids.add(message_id)
                else:
                    logger.warning(
                        f"[多重回复] 检测到对消息ID '{message_id}' 的重复回复，已过滤。"
                        f" (动作: {action_info.action_type}, 原因: {action_info.reasoning})"
                    )
            else:
                # 如果没有message_id，无法去重，直接添加
                unique_actions.append(action_info)
        # --- 去重逻辑结束 ---

        total_actions = len(unique_actions)
        if len(reply_actions) > total_actions:
            logger.info(f"[多重回复] 原始回复任务 {len(reply_actions)} 个，去重后剩余 {total_actions} 个。")
        elif total_actions > 1:
            logger.info(f"[多重回复] 开始执行 {total_actions} 个回复任务。")

        for i, action_info in enumerate(unique_actions):
            is_last_action = i == total_actions - 1
            if total_actions > 1:
                logger.info(f"[多重回复] 正在执行第 {i + 1}/{total_actions} 个回复...")

            # 传递 clear_unread 参数
            result = await self._execute_single_reply_action(action_info, plan, clear_unread=is_last_action)
            results.append(result)

        if total_actions > 1:
            logger.info("[多重回复] 所有回复任务执行完毕。")
        return {"results": results}

    def _filter_multiple_replies(self, reply_actions: list[ActionPlannerInfo]) -> list[ActionPlannerInfo]:
        """
        根据配置策略过滤多重回复

        Args:
            reply_actions: 原始回复动作列表

        Returns:
            过滤后的回复动作列表
        """
        if len(reply_actions) <= 1:
            return reply_actions

        strategy = global_config.chat.multiple_replies_strategy

        if strategy == "keep_first":
            # 保留第一个回复
            selected = reply_actions[0]
            logger.info(f"[多重回复] 使用策略 'keep_first'，选择第一个回复: {selected.action_type}")
            return [selected]

        elif strategy == "keep_last":
            # 保留最后一个回复
            selected = reply_actions[-1]
            logger.info(f"[多重回复] 使用策略 'keep_last'，选择最后一个回复: {selected.action_type}")
            return [selected]

        elif strategy == "keep_best":
            # 保留最佳回复（这里可以根据reasoning、action_type等来评分）
            best_action = self._select_best_reply(reply_actions)
            logger.info(f"[多重回复] 使用策略 'keep_best'，选择最佳回复: {best_action.action_type}")
            return [best_action]

        else:
            # 默认保留第一个
            logger.warning(f"[多重回复] 未知策略 '{strategy}'，默认保留第一个回复")
            return [reply_actions[0]]

    def _select_best_reply(self, reply_actions: list[ActionPlannerInfo]) -> ActionPlannerInfo:
        """
        从多个回复动作中选择最佳的一个

        Args:
            reply_actions: 回复动作列表

        Returns:
            选出的最佳回复动作
        """
        # 定义优先级映射
        action_type_priority = {
            "reply": 3,        # 针对特定消息的回复，优先级最高
            "proactive_reply": 2,  # 主动回复，优先级中等
            "respond": 1,      # 统一回应，优先级最低
        }

        # 首先按动作类型排序
        sorted_actions = sorted(
            reply_actions,
            key=lambda x: action_type_priority.get(x.action_type, 0),
            reverse=True
        )

        # 如果有相同优先级的，选择reasoning更长的（通常表示更详细的思考）
        if len(sorted_actions) > 1:
            same_priority = [a for a in sorted_actions
                           if action_type_priority.get(a.action_type, 0) == action_type_priority.get(sorted_actions[0].action_type, 0)]

            if len(same_priority) > 1:
                # 选择reasoning最长的
                best = max(same_priority, key=lambda x: len(x.reasoning or ""))
                logger.debug(f"[多重回复] 多个相同优先级动作，选择reasoning最长的: {best.action_type}")
                return best

        return sorted_actions[0]

    async def _execute_single_reply_action(
        self, action_info: ActionPlannerInfo, plan: Plan, clear_unread: bool = True
    ) -> dict[str, Any]:
        """执行单个回复动作"""
        start_time = time.time()
        success = False
        error_message = ""
        reply_content = ""

        try:
            logger.info(f"执行回复动作: {action_info.action_type} (原因: {action_info.reasoning})")

            # 获取用户ID
            user_id = action_info.action_message.user_info.user_id if action_info.action_message else None

            if user_id and user_id == str(global_config.bot.qq_account):
                logger.warning("尝试回复自己，跳过此动作以防止死循环。")
                return {
                    "action_type": action_info.action_type,
                    "success": False,
                    "error_message": "尝试回复自己，跳过此动作以防止死循环。",
                    "execution_time": 0,
                    "reasoning": action_info.reasoning,
                    "reply_content": "",
                }
            # 构建回复动作参数
            action_data = action_info.action_data or {}

            action_params = {
                "chat_id": plan.chat_id,
                "target_message": action_info.action_message,
                "reasoning": action_info.reasoning,
                "action_data": action_data,
                "clear_unread_messages": clear_unread,
            }

            # 通过动作管理器执行回复
            execution_result = await self.action_manager.execute_action(
                action_name=action_info.action_type, **action_params
            )

            # 从返回结果中提取真正的回复文本
            reply_content = execution_result.get("reply_text", "")
            success = execution_result.get("success", False)

            if success:
                logger.info(f"回复动作 '{action_info.action_type}' 执行成功。")
            else:
                raise Exception(execution_result.get("error", "未知错误"))

        except Exception as e:
            error_message = str(e)
            logger.error(f"执行回复动作失败: {action_info.action_type}, 错误: {error_message}")

        execution_time = time.time() - start_time
        self.execution_stats["execution_times"].append(execution_time)

        return {
            "action_type": action_info.action_type,
            "success": success,
            "error_message": error_message,
            "execution_time": execution_time,
            "reasoning": action_info.reasoning,
            "reply_content": reply_content[:200] + "..."
            if reply_content and len(reply_content) > 200
            else reply_content,
        }

    async def _execute_other_actions(self, other_actions: list[ActionPlannerInfo], plan: Plan) -> dict[str, Any]:
        """执行其他动作"""
        results = []

        # 并行执行其他动作
        tasks = []
        for action_info in other_actions:
            task = self._execute_single_other_action(action_info, plan)
            tasks.append(task)

        if tasks:
            executed_results = await asyncio.gather(*tasks, return_exceptions=True)
            for i, result in enumerate(executed_results):
                if isinstance(result, Exception):
                    logger.error(f"执行动作 {other_actions[i].action_type} 时发生异常: {result}")
                    results.append(
                        {
                            "action_type": other_actions[i].action_type,
                            "success": False,
                            "error_message": str(result),
                            "execution_time": 0,
                            "reasoning": other_actions[i].reasoning,
                        }
                    )
                else:
                    results.append(result)

        return {"results": results}

    async def _execute_single_other_action(self, action_info: ActionPlannerInfo, plan: Plan) -> dict[str, Any]:
        """执行单个其他动作"""
        start_time = time.time()
        success = False
        error_message = ""

        try:
            logger.info(f"执行其他动作: {action_info.action_type} (原因: {action_info.reasoning})")

            action_data = action_info.action_data or {}

            # 针对 poke_user 动作，特殊处理
            if action_info.action_type == "poke_user":
                target_message = action_info.action_message
                if target_message:
                    user_id = target_message.user_info.user_id
                    user_name = target_message.user_info.user_nickname
                    message_id = target_message.message_id

                    if user_id:
                        action_data["user_id"] = user_id
                        logger.info(f"检测到戳一戳动作，目标用户ID: {user_id}")
                    elif user_name:
                        action_data["user_name"] = user_name
                        logger.info(f"检测到戳一戳动作，目标用户: {user_name}")
                    else:
                        logger.warning("无法从戳一戳消息中获取用户ID或昵称。")

                    # 传递原始消息ID以支持引用
                    if message_id:
                        action_data["target_message_id"] = message_id

            # 构建动作参数
            action_params = {
                "chat_id": plan.chat_id,
                "target_message": action_info.action_message,
                "reasoning": action_info.reasoning,
                "action_data": action_data,
                "clear_unread_messages": False,  # 其他动作不应清除未读消息
            }

            # 通过动作管理器执行动作
            await self.action_manager.execute_action(action_name=action_info.action_type, **action_params)

            success = True
            logger.info(f"其他动作 '{action_info.action_type}' 执行成功。")

        except Exception as e:
            error_message = str(e)
            logger.error(f"执行其他动作失败: {action_info.action_type}, 错误: {error_message}")

        execution_time = time.time() - start_time
        self.execution_stats["execution_times"].append(execution_time)

        return {
            "action_type": action_info.action_type,
            "success": success,
            "error_message": error_message,
            "execution_time": execution_time,
            "reasoning": action_info.reasoning,
        }

    async def _add_bot_reply_to_read_messages(self, action_info: ActionPlannerInfo, plan: Plan, reply_content: str):
        """将机器人回复添加到已读消息中"""
        try:
            if not reply_content or not plan.chat_id:
                logger.debug("跳过添加已读消息：回复内容为空或缺少chat_id")
                return

            # 获取chat_stream对象
            from src.plugin_system.apis.chat_api import get_chat_manager

            chat_manager = get_chat_manager()
            chat_stream = await chat_manager.get_stream(plan.chat_id)

            if not chat_stream:
                logger.warning(f"无法获取chat_stream: {plan.chat_id}")
                return

            # 构建机器人回复的DatabaseMessages对象
            from src.common.data_models.database_data_model import DatabaseMessages

            current_time = time.time()

            # 构建用户信息
            bot_user_id = str(global_config.bot.qq_account)
            bot_nickname = global_config.bot.nickname

            # 创建机器人回复消息
            bot_message = DatabaseMessages(
                message_id=f"bot_reply_{int(current_time * 1000)}",  # 生成唯一ID
                time=current_time,
                chat_id=plan.chat_id,
                reply_to=None,  # 不是回复消息
                interest_value=None,  # 机器人回复不需要兴趣值
                processed_plain_text=reply_content,
                display_message=reply_content,
                is_read=True,  # 标记为已读
                is_emoji=False,
                is_picid=False,
                is_command=False,
                is_notify=False,
                # 用户信息
                user_id=bot_user_id,
                user_nickname=bot_nickname,
                user_cardname=bot_nickname,
                user_platform="qq",
                # 聊天上下文信息
                chat_info_user_id=(chat_stream.user_info.user_id or bot_user_id) if chat_stream.user_info else bot_user_id,
                chat_info_user_nickname=(chat_stream.user_info.user_nickname or bot_nickname)
                if chat_stream.user_info
                else bot_nickname,
                chat_info_user_cardname=(chat_stream.user_info.user_cardname or bot_nickname)
                if chat_stream.user_info
                else bot_nickname,
                chat_info_user_platform=chat_stream.platform,
                chat_info_stream_id=chat_stream.stream_id,
                chat_info_platform=chat_stream.platform,
                chat_info_create_time=chat_stream.create_time,
                chat_info_last_active_time=chat_stream.last_active_time,
                # 群组信息（如果是群聊）
                chat_info_group_id=chat_stream.group_info.group_id if chat_stream.group_info else None,
                chat_info_group_name=chat_stream.group_info.group_name if chat_stream.group_info else None,
                chat_info_group_platform=getattr(chat_stream.group_info, "platform", None)
                if chat_stream.group_info
                else None,
                # 动作信息
                actions=["bot_reply"],
                should_reply=False,
                should_act=False,
            )

            # 添加到chat_stream的已读消息中
            chat_stream.context_manager.context.history_messages.append(bot_message)
            logger.debug(f"机器人回复已添加到已读消息: {reply_content[:50]}...")

        except Exception as e:
            logger.error(f"添加机器人回复到已读消息时出错: {e}")
            logger.debug(f"plan.chat_id: {plan.chat_id}")
            logger.debug(f"reply_content: {reply_content[:100] if reply_content else 'None'}")

    def get_execution_stats(self) -> dict[str, Any]:
        """获取执行统计信息"""
        stats = self.execution_stats.copy()

        # 计算平均执行时间
        if stats["execution_times"]:
            avg_time = sum(stats["execution_times"]) / len(stats["execution_times"])
            stats["average_execution_time"] = avg_time
            stats["max_execution_time"] = max(stats["execution_times"])
            stats["min_execution_time"] = min(stats["execution_times"])
        else:
            stats["average_execution_time"] = 0
            stats["max_execution_time"] = 0
            stats["min_execution_time"] = 0

        # 移除执行时间列表以避免返回过大数据
        stats.pop("execution_times", None)

        return stats

    def reset_stats(self):
        """重置统计信息"""
        self.execution_stats = {
            "total_executed": 0,
            "successful_executions": 0,
            "failed_executions": 0,
            "reply_executions": 0,
            "other_action_executions": 0,
            "execution_times": [],
        }

    def get_recent_performance(self, limit: int = 10) -> list[dict[str, Any]]:
        """获取最近的执行性能"""
        recent_times = self.execution_stats["execution_times"][-limit:]
        if not recent_times:
            return []

        return [
            {
                "execution_index": i + 1,
                "execution_time": time_val,
                "timestamp": time.time() - (len(recent_times) - i) * 60,  # 估算时间戳
            }
            for i, time_val in enumerate(recent_times)
        ]

    async def _flush_action_manager_batch_storage(self, plan: Plan):
        """使用 action_manager 的批量存储功能存储所有待处理的动作"""
        try:
            # 通过 chat_id 获取真实的 chat_stream 对象
            from src.plugin_system.apis.chat_api import get_chat_manager

            chat_manager = get_chat_manager()
            chat_stream = await chat_manager.get_stream(plan.chat_id)

            if chat_stream:
                # 调用 action_manager 的批量存储
                await self.action_manager.flush_batch_storage(chat_stream)
                logger.info("批量存储完成：通过 action_manager 存储所有动作记录")

            # 禁用批量存储模式
            self.action_manager.disable_batch_storage()

        except Exception as e:
            logger.error(f"批量存储动作记录时发生错误: {e}")
            # 确保在出错时也禁用批量存储模式
            self.action_manager.disable_batch_storage()
