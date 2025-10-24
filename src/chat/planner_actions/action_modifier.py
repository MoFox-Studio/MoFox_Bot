import asyncio
import hashlib
import random
import time
from typing import TYPE_CHECKING, Any

import orjson

from src.chat.message_receive.chat_stream import get_chat_manager
from src.chat.planner_actions.action_manager import ChatterActionManager
from src.chat.utils.chat_message_builder import build_readable_messages, get_raw_msg_before_timestamp_with_chat
from src.common.data_models.message_manager_data_model import StreamContext
from src.common.logger import get_logger
from src.config.config import global_config, model_config
from src.llm_models.utils_model import LLMRequest
from src.plugin_system.base.component_types import ActionInfo
from src.plugin_system.core.global_announcement_manager import global_announcement_manager

if TYPE_CHECKING:
    pass

logger = get_logger("action_manager")


class ActionModifier:
    """动作处理器

    用于处理Observation对象和根据激活类型处理actions。
    集成了原有的modify_actions功能和新的激活类型处理功能。
    支持并行判定和智能缓存优化。
    """

    def __init__(self, action_manager: ChatterActionManager, chat_id: str):
        """初始化动作处理器"""
        self.chat_id = chat_id
        # chat_stream 和 log_prefix 将在异步方法中初始化
        self.chat_stream = None  # type: ignore
        self.log_prefix = f"[{chat_id}]"

        self.action_manager = action_manager

        # 用于LLM判定的小模型
        self.llm_judge = LLMRequest(model_set=model_config.model_task_config.utils_small, request_type="action.judge")

        # 缓存相关属性
        self._llm_judge_cache = {}  # 缓存LLM判定结果
        self._cache_expiry_time = 30  # 缓存过期时间（秒）
        self._last_context_hash = None  # 上次上下文的哈希值
        self._log_prefix_initialized = False

    async def _initialize_log_prefix(self):
        """异步初始化log_prefix和chat_stream"""
        if not self._log_prefix_initialized:
            self.chat_stream = await get_chat_manager().get_stream(self.chat_id)
            stream_name = await get_chat_manager().get_stream_name(self.chat_id)
            self.log_prefix = f"[{stream_name or self.chat_id}]"
            self._log_prefix_initialized = True

    async def modify_actions(
        self,
        message_content: str = "",
    ):  # sourcery skip: use-named-expression
        """
        动作修改流程，整合传统观察处理和新的激活类型判定

        这个方法处理完整的动作管理流程：
        1. 基于观察的传统动作修改（循环历史分析、类型匹配等）
        2. 基于激活类型的智能动作判定，最终确定可用动作集

        处理后，ActionManager 将包含最终的可用动作集，供规划器直接使用
        """
        # 初始化log_prefix
        await self._initialize_log_prefix()

        logger.debug(f"{self.log_prefix}开始完整动作修改流程")

        removals_s1: list[tuple[str, str]] = []
        removals_s2: list[tuple[str, str]] = []
        removals_s3: list[tuple[str, str]] = []

        self.action_manager.restore_actions()
        all_actions = self.action_manager.get_using_actions()

        # === 第0阶段：根据聊天类型过滤动作 ===
        from src.chat.utils.utils import get_chat_type_and_target_info
        from src.plugin_system.base.component_types import ChatType, ComponentType
        from src.plugin_system.core.component_registry import component_registry

        # 获取聊天类型
        is_group_chat, _ = await get_chat_type_and_target_info(self.chat_id)
        all_registered_actions = component_registry.get_components_by_type(ComponentType.ACTION)

        chat_type_removals = []
        for action_name in list(all_actions.keys()):
            if action_name in all_registered_actions:
                action_info = all_registered_actions[action_name]
                chat_type_allow = getattr(action_info, "chat_type_allow", ChatType.ALL)

                # 检查是否符合聊天类型限制
                should_keep = (
                    chat_type_allow == ChatType.ALL
                    or (chat_type_allow == ChatType.GROUP and is_group_chat)
                    or (chat_type_allow == ChatType.PRIVATE and not is_group_chat)
                )

                if not should_keep:
                    chat_type_removals.append((action_name, f"不支持{'群聊' if is_group_chat else '私聊'}"))
                    self.action_manager.remove_action_from_using(action_name)

        if chat_type_removals:
            logger.info(f"{self.log_prefix} 第0阶段：根据聊天类型过滤 - 移除了 {len(chat_type_removals)} 个动作")
            for action_name, reason in chat_type_removals:
                logger.debug(f"{self.log_prefix} - 移除 {action_name}: {reason}")

        message_list_before_now_half = await get_raw_msg_before_timestamp_with_chat(
            chat_id=self.chat_stream.stream_id,
            timestamp=time.time(),
            limit=min(int(global_config.chat.max_context_size * 0.33), 10),
        )
        chat_content = await build_readable_messages(
            message_list_before_now_half,
            replace_bot_name=True,
            merge_messages=False,
            timestamp_mode="relative",
            read_mark=0.0,
            show_actions=True,
        )

        if message_content:
            chat_content = chat_content + "\n" + f"现在，最新的消息是：{message_content}"

        # === 第一阶段：去除用户自行禁用的 ===
        disabled_actions = global_announcement_manager.get_disabled_chat_actions(self.chat_id)
        if disabled_actions:
            for disabled_action_name in disabled_actions:
                if disabled_action_name in all_actions:
                    removals_s1.append((disabled_action_name, "用户自行禁用"))
                    self.action_manager.remove_action_from_using(disabled_action_name)
                    logger.debug(f"{self.log_prefix}阶段一移除动作: {disabled_action_name}，原因: 用户自行禁用")

        # === 第二阶段：检查动作的关联类型 ===
        chat_context = self.chat_stream.stream_context
        current_actions_s2 = self.action_manager.get_using_actions()
        type_mismatched_actions = self._check_action_associated_types(current_actions_s2, chat_context)

        if type_mismatched_actions:
            removals_s2.extend(type_mismatched_actions)

        # 应用第二阶段的移除
        for action_name, reason in removals_s2:
            self.action_manager.remove_action_from_using(action_name)
            logger.debug(f"{self.log_prefix}阶段二移除动作: {action_name}，原因: {reason}")

        # === 第三阶段：激活类型判定 ===
        if chat_content is not None:
            logger.debug(f"{self.log_prefix}开始激活类型判定阶段")

            # 获取当前使用的动作集（经过第一阶段处理）
            # 在第三阶段开始前，再次获取最新的动作列表
            current_actions_s3 = self.action_manager.get_using_actions()

            # 获取因激活类型判定而需要移除的动作
            removals_s3 = await self._get_deactivated_actions_by_type(
                current_actions_s3,
                chat_content,
            )

            # 应用第三阶段的移除
            for action_name, reason in removals_s3:
                self.action_manager.remove_action_from_using(action_name)
                logger.debug(f"{self.log_prefix}阶段三移除动作: {action_name}，原因: {reason}")

        # === 统一日志记录 ===
        all_removals = chat_type_removals + removals_s1 + removals_s2 + removals_s3
        removals_summary: str = ""
        if all_removals:
            removals_summary = " | ".join([f"{name}({reason})" for name, reason in all_removals])

        available_actions = list(self.action_manager.get_using_actions().keys())
        available_actions_text = "、".join(available_actions) if available_actions else "无"

        logger.info(f"{self.log_prefix} 当前可用动作: {available_actions_text}||移除: {removals_summary}")

    def _check_action_associated_types(self, all_actions: dict[str, ActionInfo], chat_context: StreamContext):
        type_mismatched_actions: list[tuple[str, str]] = []
        for action_name, action_info in all_actions.items():
            if action_info.associated_types and not self._check_action_output_types(action_info.associated_types, chat_context):
                associated_types_str = ", ".join(action_info.associated_types)
                reason = f"适配器不支持（需要: {associated_types_str}）"
                type_mismatched_actions.append((action_name, reason))
                logger.debug(f"{self.log_prefix}决定移除动作: {action_name}，原因: {reason}")
        return type_mismatched_actions

    def _check_action_output_types(self, output_types: list[str], chat_context: StreamContext) -> bool:
        """
        检查Action的输出类型是否被当前适配器支持

        Args:
            output_types: Action需要输出的消息类型列表
            chat_context: 聊天上下文

        Returns:
            bool: 如果所有输出类型都支持则返回True
        """
        # 获取当前适配器支持的输出类型
        adapter_supported_types = self._get_adapter_supported_output_types(chat_context)

        # 检查所有需要的输出类型是否都被支持
        for output_type in output_types:
            if output_type not in adapter_supported_types:
                logger.debug(f"适配器不支持输出类型 '{output_type}'，支持的类型: {adapter_supported_types}")
                return False
        return True

    def _get_adapter_supported_output_types(self, chat_context: StreamContext) -> list[str]:
        """
        获取当前适配器支持的输出类型列表

        Args:
            chat_context: 聊天上下文

        Returns:
            list[str]: 支持的输出类型列表
        """
        # 检查additional_config是否存在且不为空
        if (chat_context.current_message
            and hasattr(chat_context.current_message, "additional_config")
            and chat_context.current_message.additional_config):

            try:
                additional_config = chat_context.current_message.additional_config
                format_info = None

                # 处理additional_config可能是字符串或字典的情况
                if isinstance(additional_config, str):
                    # 如果是字符串，尝试解析为JSON
                    try:
                        config = orjson.loads(additional_config)
                        format_info = config.get("format_info")
                    except (orjson.JSONDecodeError, AttributeError, TypeError):
                        logger.debug("无法解析additional_config JSON字符串")
                        format_info = None

                elif isinstance(additional_config, dict):
                    # 如果是字典，直接获取format_info
                    format_info = additional_config.get("format_info")

                # 如果找到了format_info，从中提取支持的类型
                if format_info:
                    # 优先检查accept_format字段
                    if "accept_format" in format_info:
                        accept_format = format_info["accept_format"]
                        if isinstance(accept_format, str):
                            accept_format = [accept_format]
                        elif isinstance(accept_format, list):
                            pass
                        else:
                            accept_format = list(accept_format) if hasattr(accept_format, "__iter__") else []

                        # 合并基础类型和适配器特定类型
                        return list(set(accept_format))

                    # 备用检查content_format字段
                    elif "content_format" in format_info:
                        content_format = format_info["content_format"]
                        if isinstance(content_format, str):
                            content_format = [content_format]
                        elif isinstance(content_format, list):
                            pass
                        else:
                            content_format = list(content_format) if hasattr(content_format, "__iter__") else []

                        return list(set(content_format))

            except Exception as e:
                logger.debug(f"解析适配器格式信息失败，使用默认支持类型: {e}")


    async def _get_deactivated_actions_by_type(
        self,
        actions_with_info: dict[str, ActionInfo],
        chat_content: str = "",
    ) -> list[tuple[str, str]]:
        """
        根据激活类型过滤，返回需要停用的动作列表及原因
        
        新的实现：调用每个 Action 类的 go_activate 方法来判断是否激活

        Args:
            actions_with_info: 带完整信息的动作字典
            chat_content: 聊天内容

        Returns:
            List[Tuple[str, str]]: 需要停用的 (action_name, reason) 元组列表
        """
        deactivated_actions = []

        # 获取 Action 类注册表
        from src.plugin_system.base.component_types import ComponentType
        from src.plugin_system.core.component_registry import component_registry

        actions_to_check = list(actions_with_info.items())
        random.shuffle(actions_to_check)

        # 创建并行任务列表
        activation_tasks = []
        task_action_names = []

        for action_name, action_info in actions_to_check:
            # 获取 Action 类
            action_class = component_registry.get_component_class(action_name, ComponentType.ACTION)
            if not action_class:
                logger.warning(f"{self.log_prefix}未找到 Action 类: {action_name}，默认不激活")
                deactivated_actions.append((action_name, "未找到 Action 类"))
                continue

            # 创建一个临时实例来调用 go_activate 方法
            # 注意：这里只是为了调用 go_activate，不需要完整的初始化
            try:
                # 创建一个最小化的实例
                action_instance = object.__new__(action_class)
                # 设置必要的属性
                action_instance.action_name = action_name
                action_instance.log_prefix = self.log_prefix
                # 设置聊天内容，用于激活判断
                action_instance._activation_chat_content = chat_content

                # 调用 go_activate 方法（不再需要传入 chat_content）
                task = action_instance.go_activate(
                    llm_judge_model=self.llm_judge,
                )
                activation_tasks.append(task)
                task_action_names.append(action_name)

            except Exception as e:
                logger.error(f"{self.log_prefix}创建 Action 实例 {action_name} 失败: {e}")
                deactivated_actions.append((action_name, f"创建实例失败: {e}"))

        # 并行执行所有激活判断
        if activation_tasks:
            logger.debug(f"{self.log_prefix}并行执行激活判断，任务数: {len(activation_tasks)}")
            try:
                task_results = await asyncio.gather(*activation_tasks, return_exceptions=True)

                # 处理结果
                for action_name, result in zip(task_action_names, task_results, strict=False):
                    if isinstance(result, Exception):
                        logger.error(f"{self.log_prefix}激活判断 {action_name} 时出错: {result}")
                        deactivated_actions.append((action_name, f"激活判断出错: {result}"))
                    elif not result:
                        # go_activate 返回 False，不激活
                        deactivated_actions.append((action_name, "go_activate 返回 False"))
                        logger.debug(f"{self.log_prefix}未激活动作: {action_name}，原因: go_activate 返回 False")
                    else:
                        # go_activate 返回 True，激活
                        logger.debug(f"{self.log_prefix}激活动作: {action_name}")

            except Exception as e:
                logger.error(f"{self.log_prefix}并行激活判断失败: {e}")
                # 如果并行执行失败，为所有任务默认不激活
                for action_name in task_action_names:
                    deactivated_actions.append((action_name, f"并行判断失败: {e}"))

        return deactivated_actions

    @staticmethod
    def _generate_context_hash(chat_content: str) -> str:
        """生成上下文的哈希值用于缓存"""
        context_content = f"{chat_content}"
        return hashlib.md5(context_content.encode("utf-8")).hexdigest()

    async def _process_llm_judge_actions_parallel(
        self,
        llm_judge_actions: dict[str, Any],
        chat_content: str = "",
    ) -> dict[str, bool]:
        """
        并行处理LLM判定actions，支持智能缓存

        Args:
            llm_judge_actions: 需要LLM判定的actions
            chat_content: 聊天内容

        Returns:
            Dict[str, bool]: action名称到激活结果的映射
        """

        # 生成当前上下文的哈希值
        current_context_hash = self._generate_context_hash(chat_content)
        current_time = time.time()

        results = {}
        tasks_to_run = {}

        # 检查缓存
        for action_name, action_info in llm_judge_actions.items():
            cache_key = f"{action_name}_{current_context_hash}"

            # 检查是否有有效的缓存
            if (
                cache_key in self._llm_judge_cache
                and current_time - self._llm_judge_cache[cache_key]["timestamp"] < self._cache_expiry_time
            ):
                results[action_name] = self._llm_judge_cache[cache_key]["result"]
                logger.debug(
                    f"{self.log_prefix}使用缓存结果 {action_name}: {'激活' if results[action_name] else '未激活'}"
                )
            else:
                # 需要进行LLM判定
                tasks_to_run[action_name] = action_info

        # 如果有需要运行的任务，并行执行
        if tasks_to_run:
            logger.debug(f"{self.log_prefix}并行执行LLM判定，任务数: {len(tasks_to_run)}")

            # 创建并行任务
            tasks = []
            task_names = []

            for action_name, action_info in tasks_to_run.items():
                task = self._llm_judge_action(
                    action_name,
                    action_info,
                    chat_content,
                )
                tasks.append(task)
                task_names.append(action_name)

            # 并行执行所有任务
            try:
                task_results = await asyncio.gather(*tasks, return_exceptions=True)

                # 处理结果并更新缓存
                for action_name, result in zip(task_names, task_results, strict=False):
                    if isinstance(result, Exception):
                        logger.error(f"{self.log_prefix}LLM判定action {action_name} 时出错: {result}")
                        results[action_name] = False
                    else:
                        results[action_name] = result

                        # 更新缓存
                        cache_key = f"{action_name}_{current_context_hash}"
                        self._llm_judge_cache[cache_key] = {"result": result, "timestamp": current_time}

                logger.debug(f"{self.log_prefix}并行LLM判定完成，耗时: {time.time() - current_time:.2f}s")

            except Exception as e:
                logger.error(f"{self.log_prefix}并行LLM判定失败: {e}")
                # 如果并行执行失败，为所有任务返回False
                for action_name in tasks_to_run:
                    results[action_name] = False

        # 清理过期缓存
        self._cleanup_expired_cache(current_time)

        return results

    def _cleanup_expired_cache(self, current_time: float):
        """清理过期的缓存条目"""
        expired_keys = []
        expired_keys.extend(
            cache_key
            for cache_key, cache_data in self._llm_judge_cache.items()
            if current_time - cache_data["timestamp"] > self._cache_expiry_time
        )
        for key in expired_keys:
            del self._llm_judge_cache[key]

        if expired_keys:
            logger.debug(f"{self.log_prefix}清理了 {len(expired_keys)} 个过期缓存条目")

    async def _llm_judge_action(
        self,
        action_name: str,
        action_info: ActionInfo,
        chat_content: str = "",
    ) -> bool:  # sourcery skip: move-assign-in-block, use-named-expression
        """
        使用LLM判定是否应该激活某个action

        Args:
            action_name: 动作名称
            action_info: 动作信息
            observed_messages_str: 观察到的聊天消息
            chat_context: 聊天上下文
            extra_context: 额外上下文

        Returns:
            bool: 是否应该激活此action
        """

        try:
            # 构建判定提示词
            action_description = action_info.description
            action_require = action_info.action_require
            custom_prompt = action_info.llm_judge_prompt

            # 构建基础判定提示词
            base_prompt = f"""
你需要判断在当前聊天情况下，是否应该激活名为"{action_name}"的动作。

动作描述：{action_description}

动作使用场景：
"""
            for req in action_require:
                base_prompt += f"- {req}\n"

            if custom_prompt:
                base_prompt += f"\n额外判定条件：\n{custom_prompt}\n"

            if chat_content:
                base_prompt += f"\n当前聊天记录：\n{chat_content}\n"

            base_prompt += """
请根据以上信息判断是否应该激活这个动作。
只需要回答"是"或"否"，不要有其他内容。
"""

            # 调用LLM进行判定
            response, _ = await self.llm_judge.generate_response_async(prompt=base_prompt)

            # 解析响应
            response = response.strip().lower()

            # print(base_prompt)
            # print(f"LLM判定动作 {action_name}：响应='{response}'")

            should_activate = "是" in response or "yes" in response or "true" in response

            logger.debug(
                f"{self.log_prefix}LLM判定动作 {action_name}：响应='{response}'，结果={'激活' if should_activate else '不激活'}"
            )
            return should_activate

        except Exception as e:
            logger.error(f"{self.log_prefix}LLM判定动作 {action_name} 时出错: {e}")
            # 出错时默认不激活
            return False

    def _check_keyword_activation(
        self,
        action_name: str,
        action_info: ActionInfo,
        chat_content: str = "",
    ) -> bool:
        """
        检查是否匹配关键词触发条件

        Args:
            action_name: 动作名称
            action_info: 动作信息
            observed_messages_str: 观察到的聊天消息
            chat_context: 聊天上下文
            extra_context: 额外上下文

        Returns:
            bool: 是否应该激活此action
        """

        activation_keywords = action_info.activation_keywords
        case_sensitive = action_info.keyword_case_sensitive

        if not activation_keywords:
            logger.warning(f"{self.log_prefix}动作 {action_name} 设置为关键词触发但未配置关键词")
            return False

        # 构建检索文本
        search_text = ""
        if chat_content:
            search_text += chat_content
        # if chat_context:
        # search_text += f" {chat_context}"
        # if extra_context:
        # search_text += f" {extra_context}"

        # 如果不区分大小写，转换为小写
        if not case_sensitive:
            search_text = search_text.lower()

        # 检查每个关键词
        matched_keywords = []
        for keyword in activation_keywords:
            check_keyword = keyword if case_sensitive else keyword.lower()
            if check_keyword in search_text:
                matched_keywords.append(keyword)

        if matched_keywords:
            logger.debug(f"{self.log_prefix}动作 {action_name} 匹配到关键词: {matched_keywords}")
            return True
        else:
            logger.debug(f"{self.log_prefix}动作 {action_name} 未匹配到任何关键词: {activation_keywords}")
            return False
