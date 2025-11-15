import asyncio
import copy
import re
from collections.abc import Awaitable, Callable

from src.chat.utils.prompt_params import PromptParameters
from src.common.logger import get_logger
from src.plugin_system.base.base_prompt import BasePrompt
from src.plugin_system.base.component_types import ComponentType, InjectionRule, InjectionType, PromptInfo
from src.plugin_system.core.component_registry import component_registry

logger = get_logger("prompt_component_manager")


class PromptComponentManager:
    """
    一个统一的、动态的、可观测的提示词组件管理中心。

    该管理器是整个提示词动态注入系统的核心，它负责：
    1.  **规则加载**: 在系统启动时，自动扫描所有已注册的 `BasePrompt` 组件，
        并将其静态定义的 `injection_rules` 加载为默认的动态规则。
    2.  **动态管理**: 提供线程安全的 API，允许在运行时动态地添加、更新或移除注入规则，
        使得提示词的结构可以被实时调整。
    3.  **状态观测**: 提供丰富的查询 API，用于观测系统当前完整的注入状态，
        例如查询所有注入到特定目标的规则、或查询某个组件定义的所有规则。
    4.  **注入应用**: 在构建核心 Prompt 时，根据统一的、按优先级排序的规则集，
        动态地修改和装配提示词模板，实现灵活的提示词组合。
    """

    def __init__(self):
        """初始化管理器实例。"""
        # _dynamic_rules 是管理器的核心状态，存储所有注入规则。
        # 结构: {
        #   "target_prompt_name": {
        #     "prompt_component_name": (InjectionRule, content_provider, source)
        #   }
        # }
        # content_provider 是一个异步函数，用于在应用规则时动态生成注入内容。
        # source 记录了规则的来源（例如 "static_default" 或 "runtime"）。
        self._dynamic_rules: dict[str, dict[str, tuple[InjectionRule, Callable[..., Awaitable[str]], str]]] = {}
        self._lock = asyncio.Lock()  # 使用异步锁确保对 _dynamic_rules 的并发访问安全。
        self._initialized = False  # 标记静态规则是否已加载，防止重复加载。

    # --- 核心生命周期与初始化 ---

    def load_static_rules(self):
        """
        在系统启动时加载所有静态注入规则。

        该方法会扫描所有已在 `component_registry` 中注册并启用的 Prompt 组件，
        将其类变量 `injection_rules` 转换为管理器的动态规则。
        这确保了所有插件定义的默认注入行为在系统启动时就能生效。
        此操作是幂等的，一旦初始化完成就不会重复执行。
        """
        if self._initialized:
            return
        logger.info("正在加载静态 Prompt 注入规则...")

        # 从组件注册表中获取所有已启用的 Prompt 组件
        enabled_prompts = component_registry.get_enabled_components_by_type(ComponentType.PROMPT)

        for prompt_name, prompt_info in enabled_prompts.items():
            if not isinstance(prompt_info, PromptInfo):
                continue

            component_class = component_registry.get_component_class(prompt_name, ComponentType.PROMPT)
            if not (component_class and issubclass(component_class, BasePrompt)):
                logger.warning(f"无法为 '{prompt_name}' 加载静态规则，因为它不是一个有效的 Prompt 组件。")
                continue

            def create_provider(
                cls: type[BasePrompt],
            ) -> Callable[[PromptParameters, str], Awaitable[str]]:
                """
                为静态组件创建一个内容提供者闭包 (Content Provider Closure)。

                这个闭包捕获了组件的类 `cls`，并返回一个标准的 `content_provider` 异步函数。
                当 `apply_injections` 需要内容时，它会调用这个函数。
                函数内部会实例化组件，并执行其 `execute` 方法来获取注入内容。

                Args:
                    cls (type[BasePrompt]): 需要为其创建提供者的 Prompt 组件类。

                Returns:
                    Callable[[PromptParameters, str], Awaitable[str]]: 一个符合管理器标准的异步内容提供者。
                """

                async def content_provider(params: PromptParameters, target_prompt_name: str) -> str:
                    """实际执行内容生成的异步函数。"""
                    try:
                        # 从注册表获取最新的组件信息，包括插件配置
                        p_info = component_registry.get_component_info(cls.prompt_name, ComponentType.PROMPT)
                        plugin_config = {}
                        if isinstance(p_info, PromptInfo):
                            plugin_config = component_registry.get_plugin_config(p_info.plugin_name)

                        # 实例化组件并执行，传入 target_prompt_name
                        instance = cls(params=params, plugin_config=plugin_config, target_prompt_name=target_prompt_name)
                        result = await instance.execute()
                        return str(result) if result is not None else ""
                    except Exception as e:
                        logger.error(f"执行静态规则提供者 '{cls.prompt_name}' 时出错: {e}", exc_info=True)
                        return ""  # 出错时返回空字符串，避免影响主流程

                return content_provider

            # 为该组件的每条静态注入规则创建并注册一个动态规则
            for rule in prompt_info.injection_rules:
                provider = create_provider(component_class)
                target_rules = self._dynamic_rules.setdefault(rule.target_prompt, {})
                target_rules[prompt_name] = (rule, provider, "static_default")

        self._initialized = True
        logger.info(f"静态 Prompt 注入规则加载完成，共处理 {len(enabled_prompts)} 个组件。")

    # --- 运行时规则管理 API ---

    async def add_injection_rule(
        self,
        prompt_name: str,
        rules: list[InjectionRule],
        content_provider: Callable[..., Awaitable[str]],
        source: str = "runtime",
    ) -> bool:
        """
        动态添加或更新注入规则。

        此方法允许在系统运行时，由外部逻辑（如插件、命令）向管理器中添加新的注入行为。
        如果已存在同名组件针对同一目标的规则，此方法会覆盖旧规则。

        Args:
            prompt_name (str): 动态注入组件的唯一名称。
            rules (List[InjectionRule]): 描述注入行为的规则对象列表。
            content_provider (Callable[..., Awaitable[str]]):
                一个异步函数，用于在应用注入时动态生成内容。
                函数签名应为: `async def provider(params: "PromptParameters", target_prompt_name: str) -> str`
            source (str, optional): 规则的来源标识，默认为 "runtime"。

        Returns:
            bool: 如果成功添加或更新，则返回 True。
        """
        async with self._lock:
            for rule in rules:
                target_rules = self._dynamic_rules.setdefault(rule.target_prompt, {})
                target_rules[prompt_name] = (rule, content_provider, source)
                logger.info(f"成功添加/更新注入规则: '{prompt_name}' -> '{rule.target_prompt}' (来源: {source})")
        return True

    async def add_rule_for_component(self, prompt_name: str, rule: InjectionRule) -> bool:
        """
        为一个已存在的组件添加单条注入规则，自动复用其内容提供者和来源。

        此方法首先会查找指定 `prompt_name` 的组件当前是否已有注入规则。
        如果存在，则复用其 content_provider 和 source 为新的规则进行注册。
        这对于为一个组件动态添加多个注入目标非常有用，无需重复提供 provider 或 source。

        Args:
            prompt_name (str): 已存在的注入组件的名称。
            rule (InjectionRule): 要为该组件添加的新注入规则。

        Returns:
            bool: 如果成功添加规则，则返回 True；
                  如果未找到该组件的任何现有规则（无法复用），则返回 False。
        """
        async with self._lock:
            # 步骤 1: 查找现有的 content_provider 和 source
            found_provider: Callable[..., Awaitable[str]] | None = None
            found_source: str | None = None
            for target_rules in self._dynamic_rules.values():
                if prompt_name in target_rules:
                    _, found_provider, found_source = target_rules[prompt_name]
                    break

            # 步骤 2: 如果找不到 provider，则操作失败
            if not found_provider:
                logger.warning(
                    f"尝试为组件 '{prompt_name}' 添加规则失败: "
                    f"未找到该组件的任何现有规则，无法复用 content_provider 和 source。"
                )
                return False

            # 步骤 3: 使用找到的 provider 和 source 添加新规则
            source_to_use = found_source or "runtime"  # 提供一个默认值以防万一
            target_rules = self._dynamic_rules.setdefault(rule.target_prompt, {})
            target_rules[prompt_name] = (rule, found_provider, source_to_use)
            logger.info(
                f"成功为组件 '{prompt_name}' 添加新注入规则 -> "
                f"'{rule.target_prompt}' (来源: {source_to_use})"
            )
            return True

    async def remove_injection_rule(self, prompt_name: str, target_prompt: str) -> bool:
        """
        移除一条动态注入规则。

        Args:
            prompt_name (str): 要移除的注入组件的名称。
            target_prompt (str): 该组件注入的目标核心提示词名称。

        Returns:
            bool: 如果成功移除，则返回 True；如果规则不存在，则返回 False。
        """
        async with self._lock:
            if target_prompt in self._dynamic_rules and prompt_name in self._dynamic_rules[target_prompt]:
                del self._dynamic_rules[target_prompt][prompt_name]
                # 如果目标下已无任何规则，则清理掉这个键
                if not self._dynamic_rules[target_prompt]:
                    del self._dynamic_rules[target_prompt]
                logger.info(f"成功移除注入规则: '{prompt_name}' from '{target_prompt}'")
                return True
        logger.warning(f"尝试移除注入规则失败: 未找到 '{prompt_name}' on '{target_prompt}'")
        return False

    async def remove_all_rules_by_component_name(self, prompt_name: str) -> bool:
        """
        按组件名称移除其所有相关的注入规则。

        此方法会遍历管理器中所有的目标提示词，并移除所有与给定的 `prompt_name`
        相关联的注入规则。这对于清理或禁用某个组件的所有注入行为非常有用。

        Args:
            prompt_name (str): 要移除规则的组件的名称。

        Returns:
            bool: 如果至少移除了一条规则，则返回 True；否则返回 False。
        """
        removed = False
        async with self._lock:
            # 创建一个目标列表的副本进行迭代，因为我们可能会在循环中修改字典
            for target_prompt in list(self._dynamic_rules.keys()):
                if prompt_name in self._dynamic_rules[target_prompt]:
                    del self._dynamic_rules[target_prompt][prompt_name]
                    removed = True
                    logger.info(f"成功移除注入规则: '{prompt_name}' from '{target_prompt}'")
                    # 如果目标下已无任何规则，则清理掉这个键
                    if not self._dynamic_rules[target_prompt]:
                        del self._dynamic_rules[target_prompt]
                        logger.debug(f"目标 '{target_prompt}' 已空，已被移除。")

        if not removed:
            logger.warning(f"尝试移除组件 '{prompt_name}' 的所有规则失败: 未找到任何相关规则。")

        return removed

    # --- 核心注入逻辑 ---

    async def apply_injections(
        self, target_prompt_name: str, original_template: str, params: PromptParameters
    ) -> str:
        """
        【核心方法】根据目标名称，应用所有匹配的注入规则，返回修改后的模板。

        此方法实现了“意图识别与安全执行”机制，以确保注入操作的鲁棒性：
        1.  **占位符保护**: 首先，扫描模板中的所有 `"{...}"` 占位符，
            并用唯一的、无冲突的临时标记替换它们。这可以防止注入规则意外地修改或删除核心占位符。
        2.  **规则预检与警告**: 在应用规则前，检查所有 `REMOVE` 和 `REPLACE` 类型的规则，
            看它们的 `target_content` 是否可能匹配到被保护的占位符。如果可能，
            会记录一条明确的警告日志，告知开发者该规则有风险，但不会中断流程。
        3.  **安全执行**: 在“净化”过的模板上（即占位符已被替换的模板），
            按优先级顺序安全地应用所有注入规则。
        4.  **占位符恢复**: 所有注入操作完成后，将临时标记恢复为原始的占位符。

        Args:
            target_prompt_name (str): 目标核心提示词的名称。
            original_template (str): 未经修改的原始提示词模板。
            params (PromptParameters): 当前请求的参数，会传递给 `content_provider`。

        Returns:
            str: 应用了所有注入规则后，最终生成的提示词模板字符串。
        """
        if not self._initialized:
            self.load_static_rules()

        rules_for_target = list(self._dynamic_rules.get(target_prompt_name, {}).values())
        if not rules_for_target:
            return original_template

        # --- 占位符保护机制 ---
        placeholders = re.findall(r"({[^{}]+})", original_template)
        placeholder_map: dict[str, str] = {
            f"__PROMPT_PLACEHOLDER_{i}__": p for i, p in enumerate(placeholders)
        }        

        # 1. 保护: 将占位符替换为临时标记
        protected_template = original_template
        for marker, placeholder in placeholder_map.items():
            protected_template = protected_template.replace(placeholder, marker)

        # 2. 预检与警告: 检查危险规则
        for rule, _, source in rules_for_target:
            if rule.injection_type in (InjectionType.REMOVE, InjectionType.REPLACE) and rule.target_content:
                try:
                    for p in placeholders:
                        if re.search(rule.target_content, p):
                            logger.warning(
                                f"注入规则警告 (来源: {source}): "
                                f"规则 `target_content` ('{rule.target_content}') "
                                f"可能会影响核心占位符 '{p}'。为保证系统稳定，该占位符已被保护，不会被此规则修改。"
                            )
                            # 只对每个规则警告一次
                            break
                except re.error:
                    # 正则表达式本身有误，后面执行时会再次捕获，这里可忽略
                    pass

        # 3. 安全执行: 按优先级排序并应用规则
        rules_for_target.sort(key=lambda x: x[0].priority)

        modified_template = protected_template
        for rule, provider, source in rules_for_target:
            content = ""
            if rule.injection_type != InjectionType.REMOVE:
                try:
                    content = await provider(params, target_prompt_name)
                except Exception as e:
                    logger.error(f"执行规则 '{rule}' (来源: {source}) 的内容提供者时失败: {e}", exc_info=True)
                    continue

            try:
                if rule.injection_type == InjectionType.PREPEND:
                    if content:
                        modified_template = f"{content}\n{modified_template}"
                elif rule.injection_type == InjectionType.APPEND:
                    if content:
                        modified_template = f"{modified_template}\n{content}"
                elif rule.injection_type == InjectionType.REPLACE:
                    if content is not None and rule.target_content:
                        modified_template = re.sub(rule.target_content, str(content), modified_template)
                elif rule.injection_type == InjectionType.INSERT_AFTER:
                    if content and rule.target_content:
                        replacement = f"\\g<0>\n{content}"
                        modified_template = re.sub(rule.target_content, replacement, modified_template)
                elif rule.injection_type == InjectionType.REMOVE:
                    if rule.target_content:
                        modified_template = re.sub(rule.target_content, "", modified_template)
            except re.error as e:
                logger.error(f"应用规则时发生正则错误: {e} (pattern: '{rule.target_content}')")
            except Exception as e:
                logger.error(f"应用注入规则 '{rule}' (来源: {source}) 失败: {e}", exc_info=True)

        # 4. 占位符恢复
        final_template = modified_template
        for marker, placeholder in placeholder_map.items():
            final_template = final_template.replace(marker, placeholder)

        return final_template

    async def preview_prompt_injections(
        self, target_prompt_name: str, params: PromptParameters
    ) -> str:
        """
        【预览功能】模拟应用所有注入规则，返回最终生成的模板字符串，而不实际修改任何状态。

        这个方法对于调试和测试非常有用，可以查看在特定参数下，
        一个核心提示词经过所有注入规则处理后会变成什么样子。

        Args:
            target_prompt_name (str): 希望预览的目标核心提示词名称。
            params (PromptParameters): 模拟的请求参数。

        Returns:
            str: 模拟生成的最终提示词模板字符串。如果找不到模板，则返回错误信息。
        """
        try:
            # 从全局提示词管理器获取最原始的模板内容
            from src.chat.utils.prompt import global_prompt_manager
            original_prompt = global_prompt_manager._prompts.get(target_prompt_name)
            if not original_prompt:
                logger.warning(f"无法预览 '{target_prompt_name}'，因为找不到这个核心 Prompt。")
                return f"Error: Prompt '{target_prompt_name}' not found."
            original_template = original_prompt.template
        except KeyError:
            logger.warning(f"无法预览 '{target_prompt_name}'，因为找不到这个核心 Prompt。")
            return f"Error: Prompt '{target_prompt_name}' not found."

        # 直接调用核心注入逻辑来模拟结果
        return await self.apply_injections(target_prompt_name, original_template, params)

    # --- 状态观测与查询 API ---

    def get_core_prompts(self) -> list[str]:
        """获取所有已注册的核心提示词模板名称列表（即所有可注入的目标）。"""
        from src.chat.utils.prompt import global_prompt_manager
        return list(global_prompt_manager._prompts.keys())

    def get_core_prompt_contents(self, prompt_name: str | None = None) -> list[list[str]]:
        """
        获取核心提示词模板的原始内容。

        Args:
            prompt_name (str | None, optional):
                如果指定，则只返回该名称对应的提示词模板。
                如果为 None，则返回所有核心提示词模板。
                默认为 None。

        Returns:
            list[list[str]]: 一个列表，每个子列表包含 [prompt_name, template_content]。
                             如果指定了 prompt_name 但未找到，则返回空列表。
        """
        from src.chat.utils.prompt import global_prompt_manager

        if prompt_name:
            prompt = global_prompt_manager._prompts.get(prompt_name)
            return [[prompt_name, prompt.template]] if prompt else []

        return [[name, prompt.template] for name, prompt in global_prompt_manager._prompts.items()]

    def get_registered_prompt_component_info(self) -> list[PromptInfo]:
        """
        获取所有已注册和动态添加的Prompt组件信息，并反映当前的注入规则状态。

        该方法会合并静态注册的组件信息和运行时的动态注入规则，
        确保返回的 `PromptInfo` 列表能够准确地反映系统当前的完整状态。

        Returns:
            list[PromptInfo]: 一个包含所有静态和动态Prompt组件信息的列表。
                             每个组件的 `injection_rules` 都会被更新为当前实际生效的规则。
        """
        # 步骤 1: 获取所有静态注册的组件信息，并使用深拷贝以避免修改原始数据
        static_components = component_registry.get_components_by_type(ComponentType.PROMPT)
        # 使用深拷贝以避免修改原始注册表数据
        info_dict: dict[str, PromptInfo] = {
            name: copy.deepcopy(info) for name, info in static_components.items() if isinstance(info, PromptInfo)
        }

        # 步骤 2: 遍历动态规则，识别并创建纯动态组件的 PromptInfo
        all_dynamic_component_names = set()
        for target, rules in self._dynamic_rules.items():
            for prompt_name, (rule, _, source) in rules.items():
                all_dynamic_component_names.add(prompt_name)

        for name in all_dynamic_component_names:
            if name not in info_dict:
                # 这是一个纯动态组件，为其创建一个新的 PromptInfo
                info_dict[name] = PromptInfo(
                    name=name,
                    component_type=ComponentType.PROMPT,
                    description="Dynamically added component",
                    plugin_name="runtime",  # 动态组件通常没有插件归属
                    is_built_in=False,
                )

        # 步骤 3: 清空所有组件的注入规则，准备用当前状态重新填充
        for info in info_dict.values():
            info.injection_rules = []

        # 步骤 4: 再次遍历动态规则，为每个组件重建其 injection_rules 列表
        for target, rules in self._dynamic_rules.items():
            for prompt_name, (rule, _, _) in rules.items():
                if prompt_name in info_dict:
                    # 确保规则是 InjectionRule 的实例
                    if isinstance(rule, InjectionRule):
                        info_dict[prompt_name].injection_rules.append(rule)

        # 步骤 5: 返回最终的 PromptInfo 对象列表
        return list(info_dict.values())

    async def get_injection_info(
        self,
        target_prompt: str | None = None,
        detailed: bool = False,
    ) -> dict[str, list[dict]]:
        """
        获取注入信息的映射图，可按目标筛选，并可控制信息的详细程度。

        - `get_injection_info()` 返回所有目标的摘要注入信息。
        - `get_injection_info(target_prompt="...")` 返回指定目标的摘要注入信息。
        - `get_injection_info(detailed=True)` 返回所有目标的详细注入信息。
        - `get_injection_info(target_prompt="...", detailed=True)` 返回指定目标的详细注入信息。

        Args:
            target_prompt (str, optional): 如果指定，仅返回该目标的注入信息。
            detailed (bool, optional): 如果为 True，则返回包含注入类型和内容的详细信息。
                                       默认为 False，返回摘要信息。

        Returns:
            dict[str, list[dict]]: 一个字典，键是目标提示词名称，
                                   值是按优先级排序的注入信息列表。
        """
        info_map = {}
        async with self._lock:
            all_targets = set(self._dynamic_rules.keys()) | set(self.get_core_prompts())

            # 如果指定了目标，则只处理该目标
            targets_to_process = [target_prompt] if target_prompt and target_prompt in all_targets else sorted(all_targets)

            for target in targets_to_process:
                rules = self._dynamic_rules.get(target, {})
                if not rules:
                    info_map[target] = []
                    continue

                info_list = []
                for prompt_name, (rule, _, source) in rules.items():
                    if detailed:
                        info_list.append(
                            {
                                "name": prompt_name,
                                "priority": rule.priority,
                                "source": source,
                                "injection_type": rule.injection_type.value,
                                "target_content": rule.target_content,
                            }
                        )
                    else:
                        info_list.append({"name": prompt_name, "priority": rule.priority, "source": source})

                info_list.sort(key=lambda x: x["priority"])
                info_map[target] = info_list
        return info_map

    def get_injection_rules(
        self,
        target_prompt: str | None = None,
        component_name: str | None = None,
    ) -> dict[str, dict[str, "InjectionRule"]]:
        """
        获取动态注入规则，可通过目标或组件名称进行筛选。

        - 不提供任何参数时，返回所有规则。
        - 提供 `target_prompt` 时，仅返回注入到该目标的规则。
        - 提供 `component_name` 时，仅返回由该组件定义的所有规则。
        - 同时提供 `target_prompt` 和 `component_name` 时，返回满足两个条件的规则。

        Args:
            target_prompt (str, optional): 按目标核心提示词名称筛选。
            component_name (str, optional): 按注入组件名称筛选。

        Returns:
            dict[str, dict[str, InjectionRule]]: 一个深拷贝的规则字典。
            结构: { "target_prompt": { "component_name": InjectionRule } }
        """
        rules_copy = {}
        # 筛选目标
        targets_to_check = [target_prompt] if target_prompt else self._dynamic_rules.keys()

        for target in targets_to_check:
            if target not in self._dynamic_rules:
                continue

            rules_for_target = self._dynamic_rules[target]
            target_copy = {}

            # 筛选组件
            if component_name:
                if component_name in rules_for_target:
                    rule, _, _ = rules_for_target[component_name]
                    target_copy[component_name] = rule
            else:
                for name, (rule, _, _) in rules_for_target.items():
                    target_copy[name] = rule

            if target_copy:
                rules_copy[target] = target_copy

        # 如果是按组件筛选且未指定目标，则需遍历所有目标
        if component_name and not target_prompt:
            found_rules = {}
            for target, rules in self._dynamic_rules.items():
                if component_name in rules:
                    rule, _, _ = rules[component_name]
                    if target not in found_rules:
                        found_rules[target] = {}
                    found_rules[target][component_name] = rule
            return copy.deepcopy(found_rules)

        return copy.deepcopy(rules_copy)


# 创建全局单例 (Singleton)
# 在整个应用程序中，应该只使用这一个 `prompt_component_manager` 实例，
# 以确保所有部分都共享和操作同一份动态规则集。
prompt_component_manager = PromptComponentManager()
