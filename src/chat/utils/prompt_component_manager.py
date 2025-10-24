import asyncio
import re
from typing import Type

from src.chat.utils.prompt_params import PromptParameters
from src.common.logger import get_logger
from src.plugin_system.base.base_prompt import BasePrompt
from src.plugin_system.base.component_types import ComponentType, InjectionRule, InjectionType, PromptInfo
from src.plugin_system.core.component_registry import component_registry

logger = get_logger("prompt_component_manager")


class PromptComponentManager:
    """
    管理所有 `BasePrompt` 组件的单例类。

    该管理器负责：
    1. 从 `component_registry` 中查询 `BasePrompt` 子类。
    2. 根据注入点（目标Prompt名称）对它们进行筛选。
    3. 提供一个接口，以便在构建核心Prompt时，能够获取并执行所有相关的组件。
    """

    def _get_rules_for(self, target_prompt_name: str) -> list[tuple[InjectionRule, Type[BasePrompt]]]:
        """
        获取指定目标Prompt的所有注入规则及其关联的组件类。

        Args:
            target_prompt_name (str): 目标 Prompt 的名称。

        Returns:
            list[tuple[InjectionRule, Type[BasePrompt]]]: 一个元组列表，
                每个元组包含一个注入规则和其对应的 Prompt 组件类，并已根据优先级排序。
        """
        # 从注册表中获取所有已启用的 PROMPT 类型的组件
        enabled_prompts = component_registry.get_enabled_components_by_type(ComponentType.PROMPT)
        matching_rules = []

        # 遍历所有启用的 Prompt 组件，查找与目标 Prompt 相关的注入规则
        for prompt_name, prompt_info in enabled_prompts.items():
            if not isinstance(prompt_info, PromptInfo):
                continue

            # prompt_info.injection_rules 已经经过了后向兼容处理，确保总是列表
            for rule in prompt_info.injection_rules:
                # 如果规则的目标是当前指定的 Prompt
                if rule.target_prompt == target_prompt_name:
                    # 获取该规则对应的组件类
                    component_class = component_registry.get_component_class(prompt_name, ComponentType.PROMPT)
                    # 确保获取到的确实是一个 BasePrompt 的子类
                    if component_class and issubclass(component_class, BasePrompt):
                        matching_rules.append((rule, component_class))

        # 根据规则的优先级进行排序，数字越小，优先级越高，越先应用
        matching_rules.sort(key=lambda x: x[0].priority)
        return matching_rules

    async def apply_injections(
        self, target_prompt_name: str, original_template: str, params: PromptParameters
    ) -> str:
        """
        获取、实例化并执行所有相关组件，然后根据注入规则修改原始模板。

        这是一个三步走的过程：
        1. 实例化所有需要执行的组件。
        2. 并行执行它们的 `execute` 方法以获取注入内容。
        3. 按照优先级顺序，将内容注入到原始模板中。

        Args:
            target_prompt_name (str): 目标 Prompt 的名称。
            original_template (str): 原始的、未经修改的 Prompt 模板字符串。
            params (PromptParameters): 传递给 Prompt 组件实例的参数。

        Returns:
            str: 应用了所有注入规则后，修改过的 Prompt 模板字符串。
        """
        rules_with_classes = self._get_rules_for(target_prompt_name)
        # 如果没有找到任何匹配的规则，就直接返回原始模板，啥也不干
        if not rules_with_classes:
            return original_template

        # --- 第一步: 实例化所有需要执行的组件 ---
        instance_map = {}  # 存储组件实例，虽然目前没直接用，但留着总没错
        tasks = []  # 存放所有需要并行执行的 execute 异步任务
        components_to_execute = []  # 存放需要执行的组件类，用于后续结果映射

        for rule, component_class in rules_with_classes:
            # 如果注入类型是 REMOVE，那就不需要执行组件了，因为它不产生内容
            if rule.injection_type != InjectionType.REMOVE:
                try:
                    # 获取组件的元信息，主要是为了拿到插件名称来读取插件配置
                    prompt_info = component_registry.get_component_info(
                        component_class.prompt_name, ComponentType.PROMPT
                    )
                    if not isinstance(prompt_info, PromptInfo):
                        plugin_config = {}
                    else:
                        # 从注册表获取该组件所属插件的配置
                        plugin_config = component_registry.get_plugin_config(prompt_info.plugin_name)

                    # 实例化组件，并传入参数和插件配置
                    instance = component_class(params=params, plugin_config=plugin_config)
                    instance_map[component_class.prompt_name] = instance
                    # 将组件的 execute 方法作为一个任务添加到列表中
                    tasks.append(instance.execute())
                    components_to_execute.append(component_class)
                except Exception as e:
                    logger.error(f"实例化 Prompt 组件 '{component_class.prompt_name}' 失败: {e}")
                    # 即使失败，也添加一个立即完成的空任务，以保持与其他任务的索引同步
                    tasks.append(asyncio.create_task(asyncio.sleep(0, result=e)))  # type: ignore

        # --- 第二步: 并行执行所有组件的 execute 方法 ---
        # 使用 asyncio.gather 来同时运行所有任务，提高效率
        results = await asyncio.gather(*tasks, return_exceptions=True)
        # 创建一个从组件名到执行结果的映射，方便后续查找
        result_map = {
            components_to_execute[i].prompt_name: res
            for i, res in enumerate(results)
            if not isinstance(res, Exception)  # 只包含成功的结果
        }
        # 单独处理并记录执行失败的组件
        for i, res in enumerate(results):
            if isinstance(res, Exception):
                logger.error(f"执行 Prompt 组件 '{components_to_execute[i].prompt_name}' 失败: {res}")

        # --- 第三步: 按优先级顺序应用注入规则 ---
        modified_template = original_template
        for rule, component_class in rules_with_classes:
            # 从结果映射中获取该组件生成的内容
            content = result_map.get(component_class.prompt_name)

            try:
                if rule.injection_type == InjectionType.PREPEND:
                    if content:
                        modified_template = f"{content}\n{modified_template}"
                elif rule.injection_type == InjectionType.APPEND:
                    if content:
                        modified_template = f"{modified_template}\n{content}"
                elif rule.injection_type == InjectionType.REPLACE:
                    # 使用正则表达式替换目标内容
                    if content and rule.target_content:
                        modified_template = re.sub(rule.target_content, str(content), modified_template)
                elif rule.injection_type == InjectionType.INSERT_AFTER:
                    # 在匹配到的内容后面插入
                    if content and rule.target_content:
                        # re.sub a little trick: \g<0> represents the entire matched string
                        replacement = f"\\g<0>\n{content}"
                        modified_template = re.sub(rule.target_content, replacement, modified_template)
                elif rule.injection_type == InjectionType.REMOVE:
                    # 使用正则表达式移除目标内容
                    if rule.target_content:
                        modified_template = re.sub(rule.target_content, "", modified_template)
            except re.error as e:
                logger.error(
                    f"在为 '{component_class.prompt_name}' 应用规则时发生正则错误: {e} (pattern: '{rule.target_content}')"
                )
            except Exception as e:
                logger.error(f"应用 Prompt 注入规则 '{rule}' 失败: {e}")

        return modified_template


# 创建全局单例
prompt_component_manager = PromptComponentManager()
