"""
主规划器入口，负责协调 PlanGenerator, PlanFilter, 和 PlanExecutor。
集成兴趣度评分系统和用户关系追踪机制，实现智能化的聊天决策。
"""

import asyncio
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from src.common.logger import get_logger
from src.config.config import global_config
from src.mood.mood_manager import mood_manager
from src.plugin_system.base.component_types import ChatMode
from src.plugins.built_in.affinity_flow_chatter.planner.plan_executor import ChatterPlanExecutor
from src.plugins.built_in.affinity_flow_chatter.planner.plan_filter import ChatterPlanFilter
from src.plugins.built_in.affinity_flow_chatter.planner.plan_generator import ChatterPlanGenerator

if TYPE_CHECKING:
    from src.chat.planner_actions.action_manager import ChatterActionManager
    from src.common.data_models.info_data_model import Plan
    from src.common.data_models.message_manager_data_model import StreamContext

# 导入提示词模块以确保其被初始化

logger = get_logger("planner")


class ChatterActionPlanner:
    """
    增强版ActionPlanner，集成兴趣度评分和用户关系追踪机制。

    核心功能：
    1. 兴趣度评分系统：根据兴趣匹配度、关系分、提及度、时间因子对消息评分
    2. 用户关系追踪：自动追踪用户交互并更新关系分
    3. 智能回复决策：基于兴趣度阈值和连续不回复概率的智能决策
    4. 完整的规划流程：生成→筛选→执行的完整三阶段流程
    """

    def __init__(self, chat_id: str, action_manager: "ChatterActionManager"):
        """
        初始化增强版ActionPlanner。

        Args:
            chat_id (str): 当前聊天的 ID。
            action_manager (ChatterActionManager): 一个 ChatterActionManager 实例。
        """
        self.chat_id = chat_id
        self.action_manager = action_manager
        self.generator = ChatterPlanGenerator(chat_id, action_manager)
        self.executor = ChatterPlanExecutor(action_manager)

        # 使用新的统一兴趣度管理系统

        # 规划器统计
        self.planner_stats = {
            "total_plans": 0,
            "successful_plans": 0,
            "failed_plans": 0,
            "replies_generated": 0,
            "other_actions_executed": 0,
        }
        self._background_tasks: set[asyncio.Task] = set()

    async def plan(self, context: "StreamContext | None" = None) -> tuple[list[dict[str, Any]], Any | None]:
        """
        执行完整的增强版规划流程。

        Args:
            context (StreamContext): 包含聊天流消息的上下文对象。

        Returns:
            Tuple[List[Dict], Optional[Dict]]: 一个元组，包含：
                - final_actions_dict (List[Dict]): 最终确定的动作列表（字典格式）。
                - final_target_message_dict (Optional[Dict]): 最终的目标消息（字典格式）。
        """
        try:
            self.planner_stats["total_plans"] += 1

            return await self._enhanced_plan_flow(context)

        except asyncio.CancelledError:
            logger.info(f"规划流程被取消: {self.chat_id}")
            self.planner_stats["failed_plans"] += 1
            raise
        except Exception as e:
            logger.error(f"规划流程出错: {e}")
            self.planner_stats["failed_plans"] += 1
            return [], None

    async def _enhanced_plan_flow(self, context: "StreamContext | None") -> tuple[list[dict[str, Any]], Any | None]:
        """执行增强版规划流程，根据模式分发到对应的处理函数"""
        try:
            # 1. 确定当前模式
            chat_mode = context.chat_mode if context else ChatMode.FOCUS

            # 2. 如果禁用了Normal模式，则强制切换回Focus模式
            if not global_config.affinity_flow.enable_normal_mode and chat_mode == ChatMode.NORMAL:
                logger.info("Normal模式已禁用，强制切换回Focus模式")
                chat_mode = ChatMode.FOCUS
                if context:
                    context.chat_mode = ChatMode.FOCUS
                    await self._sync_chat_mode_to_stream(context)

            # 3. 根据模式分发到对应的处理流程
            if chat_mode == ChatMode.NORMAL:
                return await self._normal_mode_flow(context)
            else:
                return await self._focus_mode_flow(context)

        except Exception as e:
            logger.error(f"增强版规划流程出错: {e}")
            self.planner_stats["failed_plans"] += 1
            # 清理处理标记
            if context:
                context.processing_message_id = None
            return [], None

    async def _focus_mode_flow(self, context: "StreamContext | None") -> tuple[list[dict[str, Any]], Any | None]:
        """Focus模式下的完整plan流程

        执行完整的生成→筛选→执行流程，支持所有类型的动作，包括非回复动作。
        """
        try:
            unread_messages = context.get_unread_messages() if context else []

            # 1. 使用新的兴趣度管理系统进行评分
            max_message_interest = 0.0
            reply_not_available = True
            aggregate_should_act = False

            if unread_messages:
                # 直接使用消息中已计算的标志，无需重复计算兴趣值
                for message in unread_messages:
                    try:
                        raw_interest = getattr(message, "interest_value", 0.3)
                        if raw_interest is None:
                            raw_interest = 0.0

                        message_interest = float(raw_interest)
                        max_message_interest = max(max_message_interest, message_interest)
                        message_should_reply = getattr(message, "should_reply", False)
                        message_should_act = getattr(message, "should_act", False)

                        logger.debug(
                            f"Focus模式 - 消息 {message.message_id} 预计算标志: interest={message_interest:.3f}, "
                            f"should_reply={message_should_reply}, should_act={message_should_act}"
                        )

                        if message_should_reply:
                            aggregate_should_act = True
                            reply_not_available = False
                            break

                        if message_should_act:
                            aggregate_should_act = True

                    except Exception as e:
                        logger.warning(f"Focus模式 - 处理消息 {message.message_id} 失败: {e}")

            # 2. 检查兴趣度是否达到非回复动作阈值
            non_reply_action_interest_threshold = global_config.affinity_flow.non_reply_action_interest_threshold
            if not aggregate_should_act:
                logger.info("Focus模式 - 所有未读消息低于兴趣度阈值，不执行动作")
                # 直接返回 no_action
                from src.common.data_models.info_data_model import ActionPlannerInfo

                no_action = ActionPlannerInfo(
                    action_type="no_action",
                    reasoning=(
                        "Focus模式 - 所有未读消息兴趣度未达阈值 "
                        f"{non_reply_action_interest_threshold:.3f}"
                        f"（最高兴趣度 {max_message_interest:.3f}）"
                    ),
                    action_data={},
                    action_message=None,
                )

                # 更新连续不回复计数
                await self._update_interest_calculator_state(replied=False)

                initial_plan = await self.generator.generate(ChatMode.FOCUS)
                filtered_plan = initial_plan
                filtered_plan.decided_actions = [no_action]
            else:
                # 3. 在规划前，先进行动作修改
                from src.chat.planner_actions.action_modifier import ActionModifier
                action_modifier = ActionModifier(self.action_manager, self.chat_id)
                await action_modifier.modify_actions()

                # 4. 生成初始计划
                initial_plan = await self.generator.generate(ChatMode.FOCUS)

                # 5. 过滤回复动作（如果未达到回复阈值）
                if reply_not_available:
                    initial_plan.available_actions = {
                        action_name: action_info
                        for action_name, action_info in initial_plan.available_actions.items()
                        if action_name not in ["reply", "respond"]
                    }
                # 6. 筛选 Plan
                available_actions = list(initial_plan.available_actions.keys())
                plan_filter = ChatterPlanFilter(self.chat_id, available_actions)
                filtered_plan = await plan_filter.filter(initial_plan)

                # 检查reply动作是否可用
                has_reply_action = "reply" in available_actions or "respond" in available_actions
                if filtered_plan.decided_actions and has_reply_action and reply_not_available:
                    logger.info("Focus模式 - 未达到回复动作阈值，移除所有回复相关动作")
                    filtered_plan.decided_actions = [
                        action for action in filtered_plan.decided_actions
                        if action.action_type not in ["reply", "respond"]
                    ]

            # 7. 检查是否正在处理相同的目标消息，防止重复回复
            target_message_id = None
            if filtered_plan and filtered_plan.decided_actions:
                for action in filtered_plan.decided_actions:
                    if action.action_type in ["reply", "proactive_reply"] and action.action_message:
                        # 提取目标消息ID
                        if hasattr(action.action_message, "message_id"):
                            target_message_id = action.action_message.message_id
                        elif isinstance(action.action_message, dict):
                            target_message_id = action.action_message.get("message_id")
                        break

            # 8. 如果找到目标消息ID，检查是否已经在处理中
            if target_message_id and context:
                if context.processing_message_id == target_message_id:
                    logger.warning(
                        f"Focus模式 - 目标消息 {target_message_id} 已经在处理中，跳过本次规划以防止重复回复"
                    )
                    # 返回 no_action，避免重复处理
                    from src.common.data_models.info_data_model import ActionPlannerInfo
                    no_action = ActionPlannerInfo(
                        action_type="no_action",
                        reasoning=f"Focus模式 - 目标消息 {target_message_id} 已经在处理中，跳过以防止重复回复",
                        action_data={},
                        action_message=None,
                    )
                    return [asdict(no_action)], None
                else:
                    # 记录当前正在处理的消息ID
                    context.processing_message_id = target_message_id
                    logger.debug(f"Focus模式 - 开始处理目标消息: {target_message_id}")

            # 9. 使用 PlanExecutor 执行 Plan
            execution_result = await self.executor.execute(filtered_plan)

            # 10. 根据执行结果更新统计信息
            self._update_stats_from_execution_result(execution_result)

            # 11. 更新兴趣计算器状态
            if filtered_plan.decided_actions:
                has_reply = any(
                    action.action_type in ["reply", "proactive_reply"]
                    for action in filtered_plan.decided_actions
                )
            else:
                has_reply = False
            await self._update_interest_calculator_state(replied=has_reply)

            # 12. Focus模式下如果执行了reply动作，根据focus_energy概率切换到Normal模式
            if has_reply and context and global_config.affinity_flow.enable_normal_mode:
                await self._check_enter_normal_mode(context)

            # 13. 清理处理标记
            if context:
                context.processing_message_id = None
                logger.debug("Focus模式 - 已清理处理标记，完成规划流程")

            # 14. 返回结果
            return self._build_return_result(filtered_plan)

        except asyncio.CancelledError:
            logger.info(f"Focus模式流程被取消: {self.chat_id}")
            self.planner_stats["failed_plans"] += 1
            # 清理处理标记
            if context:
                context.processing_message_id = None
            raise
        except Exception as e:
            logger.error(f"Focus模式流程出错: {e}")
            self.planner_stats["failed_plans"] += 1
            # 清理处理标记
            if context:
                context.processing_message_id = None
            return [], None

    async def _normal_mode_flow(self, context: "StreamContext | None") -> tuple[list[dict[str, Any]], Any | None]:
        """Normal模式下的简化plan流程

        只计算兴趣值并判断是否达到reply阈值，不执行完整的plan流程。
        根据focus_energy决定退出normal模式回到focus模式的概率。
        """
        # 安全检查：确保Normal模式已启用
        if not global_config.affinity_flow.enable_normal_mode:
            logger.warning("Normal模式 - 意外进入了Normal模式流程，但该模式已被禁用！将强制切换回Focus模式进行完整规划。")
            if context:
                context.chat_mode = ChatMode.FOCUS
                await self._sync_chat_mode_to_stream(context)
            # 重新运行主规划流程，这次将正确使用Focus模式
            return await self._enhanced_plan_flow(context)

        try:
            unread_messages = context.get_unread_messages() if context else []

            # 1. 检查是否有未读消息
            if not unread_messages:
                logger.debug("Normal模式 - 没有未读消息")
                from src.common.data_models.info_data_model import ActionPlannerInfo
                no_action = ActionPlannerInfo(
                    action_type="no_action",
                    reasoning="Normal模式 - 没有未读消息",
                    action_data={},
                    action_message=None,
                )
                return [asdict(no_action)], None

            # 2. 检查是否有消息达到reply阈值
            should_reply = False
            target_message = None

            for message in unread_messages:
                message_should_reply = getattr(message, "should_reply", False)
                if message_should_reply:
                    should_reply = True
                    target_message = message
                    logger.info(f"Normal模式 - 消息 {message.message_id} 达到reply阈值，准备回复")
                    break

            if should_reply and target_message:
                # 3. 防重复检查：检查是否正在处理相同的目标消息
                target_message_id = target_message.message_id
                if context and context.processing_message_id == target_message_id:
                    logger.warning(
                        f"Normal模式 - 目标消息 {target_message_id} 已经在处理中，跳过本次规划以防止重复回复"
                    )
                    # 返回 no_action，避免重复处理
                    from src.common.data_models.info_data_model import ActionPlannerInfo
                    no_action = ActionPlannerInfo(
                        action_type="no_action",
                        reasoning=f"Normal模式 - 目标消息 {target_message_id} 已经在处理中，跳过以防止重复回复",
                        action_data={},
                        action_message=None,
                    )
                    return [asdict(no_action)], None

                # 记录当前正在处理的消息ID
                if context:
                    context.processing_message_id = target_message_id
                    logger.debug(f"Normal模式 - 开始处理目标消息: {target_message_id}")

                # 4. 构建回复动作（Normal模式使用respond动作）
                from src.common.data_models.info_data_model import ActionPlannerInfo, Plan
                from src.plugin_system.base.component_types import ChatType

                # Normal模式使用respond动作，表示统一回应未读消息
                # respond动作不需要target_message_id和action_message，因为它是统一回应所有未读消息
                respond_action = ActionPlannerInfo(
                    action_type="respond",
                    reasoning="Normal模式 - 兴趣度达到阈值，使用respond动作统一回应未读消息",
                    action_data={},  # respond动作不需要参数
                    action_message=None,  # respond动作不针对特定消息
                )

                # Normal模式下直接构建最小化的Plan，跳过generator和action_modifier
                # 这样可以显著降低延迟
                minimal_plan = Plan(
                    chat_id=self.chat_id,
                    chat_type=ChatType.PRIVATE if not context else context.chat_type,
                    mode=ChatMode.NORMAL,
                    decided_actions=[respond_action],
                )

                # 5. 执行respond动作
                execution_result = await self.executor.execute(minimal_plan)
                self._update_stats_from_execution_result(execution_result)

                logger.info("Normal模式 - 执行respond动作完成")

                # 6. 更新兴趣计算器状态（回复成功，重置不回复计数）
                await self._update_interest_calculator_state(replied=True)

                # 7. 清理处理标记
                if context:
                    context.processing_message_id = None
                    logger.debug("Normal模式 - 已清理处理标记")

                # respond动作不返回目标消息，因为它是统一回应所有未读消息
                return [asdict(respond_action)], None
            else:
                # 未达到reply阈值
                logger.debug("Normal模式 - 未达到reply阈值，不执行回复")
                from src.common.data_models.info_data_model import ActionPlannerInfo
                no_action = ActionPlannerInfo(
                    action_type="no_action",
                    reasoning="Normal模式 - 兴趣度未达到阈值",
                    action_data={},
                    action_message=None,
                )

                # 更新连续不回复计数
                await self._update_interest_calculator_state(replied=False)

                return [asdict(no_action)], None

        except asyncio.CancelledError:
            logger.info(f"Normal模式流程被取消: {self.chat_id}")
            self.planner_stats["failed_plans"] += 1
            raise
        except Exception as e:
            logger.error(f"Normal模式 - 流程出错: {e}")
            self.planner_stats["failed_plans"] += 1
            return [], None
        finally:
            # 检查是否需要退出Normal模式
            await self._check_exit_normal_mode(context)

    async def _check_enter_normal_mode(self, context: "StreamContext | None") -> None:
        """检查并执行进入Normal模式的判定

        Args:
            context: 流上下文
        """
        if not context:
            return

        try:
            from src.chat.message_receive.chat_stream import get_chat_manager

            chat_manager = get_chat_manager()
            chat_stream = await chat_manager.get_stream(self.chat_id) if chat_manager else None

            if not chat_stream:
                return

            focus_energy = chat_stream.focus_energy
            # focus_energy越高，进入normal模式的概率越高
            # 使用正比例函数: 进入概率 = focus_energy
            # 当focus_energy = 0.1时，进入概率 = 10%
            # 当focus_energy = 0.5时，进入概率 = 50%
            # 当focus_energy = 0.9时，进入概率 = 90%
            enter_probability = focus_energy

            import random
            if random.random() < enter_probability:
                logger.info(f"Focus模式: focus_energy={focus_energy:.3f}, 进入概率={enter_probability:.3f}, 切换到Normal模式")
                # 切换到normal模式
                context.chat_mode = ChatMode.NORMAL
                await self._sync_chat_mode_to_stream(context)
            else:
                logger.debug(f"Focus模式: focus_energy={focus_energy:.3f}, 进入概率={enter_probability:.3f}, 保持Focus模式")

        except Exception as e:
            logger.warning(f"检查进入Normal模式失败: {e}")

    async def _check_exit_normal_mode(self, context: "StreamContext | None") -> None:
        """检查并执行退出Normal模式的判定

        Args:
            context: 流上下文
        """
        if not context:
            return

        try:
            from src.chat.message_receive.chat_stream import get_chat_manager

            chat_manager = get_chat_manager()
            chat_stream = await chat_manager.get_stream(self.chat_id) if chat_manager else None

            if not chat_stream:
                return

            focus_energy = chat_stream.focus_energy
            # focus_energy越低，退出normal模式的概率越高
            # 使用反比例函数: 退出概率 = 1 - focus_energy
            # 当focus_energy = 0.1时，退出概率 = 90%
            # 当focus_energy = 0.5时，退出概率 = 50%
            # 当focus_energy = 0.9时，退出概率 = 10%
            exit_probability = 1.0 - focus_energy

            import random
            if random.random() < exit_probability:
                logger.info(f"Normal模式: focus_energy={focus_energy:.3f}, 退出概率={exit_probability:.3f}, 切换回Focus模式")
                # 切换回focus模式
                context.chat_mode = ChatMode.FOCUS
                await self._sync_chat_mode_to_stream(context)
            else:
                logger.debug(f"Normal模式: focus_energy={focus_energy:.3f}, 退出概率={exit_probability:.3f}, 保持Normal模式")

        except Exception as e:
            logger.warning(f"检查退出Normal模式失败: {e}")

    async def _update_interest_calculator_state(self, replied: bool) -> None:
        """更新兴趣计算器状态（连续不回复计数和回复后降低机制）

        Args:
            replied: 是否回复了消息
        """
        try:
            from src.chat.interest_system.interest_manager import get_interest_manager
            from src.plugins.built_in.affinity_flow_chatter.core.affinity_interest_calculator import (
                AffinityInterestCalculator,
            )

            interest_manager = get_interest_manager()
            calculator = interest_manager.get_current_calculator()

            if calculator and isinstance(calculator, AffinityInterestCalculator):
                calculator.on_message_processed(replied)
                logger.debug(f"已更新兴趣计算器状态: replied={replied}")
            else:
                logger.debug("未找到 AffinityInterestCalculator，跳过状态更新")

        except Exception as e:
            logger.warning(f"更新兴趣计算器状态失败: {e}")

    async def _sync_chat_mode_to_stream(self, context: "StreamContext") -> None:
        """同步chat_mode到ChatStream"""
        try:
            from src.chat.message_receive.chat_stream import get_chat_manager

            chat_manager = get_chat_manager()
            if chat_manager:
                chat_stream = await chat_manager.get_stream(context.stream_id)
                if chat_stream:
                    chat_stream.context_manager.context.chat_mode = context.chat_mode
                    chat_stream.saved = False  # 标记需要保存
                    logger.debug(f"已同步chat_mode {context.chat_mode.value} 到ChatStream {context.stream_id}")
        except Exception as e:
            logger.warning(f"同步chat_mode到ChatStream失败: {e}")

    def _update_stats_from_execution_result(self, execution_result: dict[str, Any]):
        """根据执行结果更新规划器统计"""
        if not execution_result:
            return

        successful_count = execution_result.get("successful_count", 0)

        # 更新成功执行计数
        self.planner_stats["successful_plans"] += successful_count

        # 统计回复动作和其他动作
        reply_count = 0
        other_count = 0

        for result in execution_result.get("results", []):
            action_type = result.get("action_type", "")
            if action_type in ["reply", "proactive_reply"]:
                reply_count += 1
            else:
                other_count += 1

        self.planner_stats["replies_generated"] += reply_count
        self.planner_stats["other_actions_executed"] += other_count

    def _build_return_result(self, plan: "Plan") -> tuple[list[dict[str, Any]], Any | None]:
        """构建返回结果"""
        final_actions = plan.decided_actions or []
        final_target_message = next((act.action_message for act in final_actions if act.action_message), None)

        final_actions_dict = [asdict(act) for act in final_actions]

        if final_target_message:
            if hasattr(final_target_message, "__dataclass_fields__"):
                final_target_message_dict = asdict(final_target_message)
            else:
                final_target_message_dict = final_target_message
        else:
            final_target_message_dict = None

        return final_actions_dict, final_target_message_dict

    def get_planner_stats(self) -> dict[str, Any]:
        """获取规划器统计"""
        return self.planner_stats.copy()

    def get_current_mood_state(self) -> str:
        """获取当前聊天的情绪状态"""
        chat_mood = mood_manager.get_mood_by_chat_id(self.chat_id)
        return chat_mood.mood_state

    def get_mood_stats(self) -> dict[str, Any]:
        """获取情绪状态统计"""
        chat_mood = mood_manager.get_mood_by_chat_id(self.chat_id)
        return {
            "current_mood": chat_mood.mood_state,
            "regression_count": getattr(chat_mood, "regression_count", 0),
            "last_change_time": getattr(chat_mood, "last_change_time", 0),
        }


# 全局兴趣度评分系统实例 - 在 individuality 模块中创建
