import asyncio
import copy
import re
from typing import Awaitable, Callable

from src.chat.utils.prompt import global_prompt_manager
from src.chat.utils.prompt_params import PromptParameters
from src.common.logger import get_logger
from src.plugin_system.base.base_prompt import BasePrompt
from src.plugin_system.base.component_types import ComponentType, InjectionRule, InjectionType, PromptInfo
from src.plugin_system.core.component_registry import component_registry

logger = get_logger("prompt_component_manager")



class PromptComponentManager:
    """
    统一的、动态的、可观测的提示词管理中心。

    该管理器负责：
    1. 在启动时，将所有 `BasePrompt` 组件的静态 `injection_rules` 加载为默认的动态规则。
    2. 提供 API 以在运行时动态地添加、更新、移除注入规则。
    3. 提供查询 API 以观测系统当前的完整注入状态。
    4. 在构建核心 Prompt 时，根据统一的规则集应用注入，修改模板。
    """

    def __init__(self):
        """初始化管理器。"""
        # _dynamic_rules 存储统一的注入规则
        # 结构: { "target_prompt_name": { "prompt_component_name": (InjectionRule, content_provider, source) } }
        self._dynamic_rules: dict[str, dict[str, tuple[InjectionRule, Callable[..., Awaitable[str]], str]]] = {}
        self._lock = asyncio.Lock()
        self._initialized = False

    def load_static_rules(self):
        """
        在系统启动时被调用，扫描所有已注册的 Prompt 组件，
        将其静态的 `injection_rules` 转换为动态规则并加载到管理器中。
        """
        if self._initialized:
            return
        logger.info("正在加载静态 Prompt 注入规则...")
        enabled_prompts = component_registry.get_enabled_components_by_type(ComponentType.PROMPT)

        for prompt_name, prompt_info in enabled_prompts.items():
            if not isinstance(prompt_info, PromptInfo):
                continue

            component_class = component_registry.get_component_class(prompt_name, ComponentType.PROMPT)
            if not (component_class and issubclass(component_class, BasePrompt)):
                logger.warning(f"无法为 '{prompt_name}' 加载静态规则，因为它不是一个有效的 Prompt 组件。")
                continue

            def create_provider(cls: type[BasePrompt]) -> Callable[[PromptParameters], Awaitable[str]]:
                """为静态组件创建一个内容提供者闭包。"""

                async def content_provider(params: PromptParameters) -> str:
                    try:
                        p_info = component_registry.get_component_info(cls.prompt_name, ComponentType.PROMPT)
                        plugin_config = {}
                        if isinstance(p_info, PromptInfo):
                            plugin_config = component_registry.get_plugin_config(p_info.plugin_name)

                        instance = cls(params=params, plugin_config=plugin_config)
                        result = await instance.execute()
                        return str(result) if result is not None else ""
                    except Exception as e:
                        logger.error(f"执行静态规则提供者 '{cls.prompt_name}' 时出错: {e}", exc_info=True)
                        return ""

                return content_provider

            for rule in prompt_info.injection_rules:
                provider = create_provider(component_class)
                target_rules = self._dynamic_rules.setdefault(rule.target_prompt, {})
                target_rules[prompt_name] = (rule, provider, "static_default")
        
        self._initialized = True
        logger.info("静态 Prompt 注入规则加载完成。")


    async def add_injection_rule(
        self,
        prompt_name: str,
        rule: InjectionRule,
        content_provider: Callable[..., Awaitable[str]] | None = None,
        source: str = "runtime",
    ) -> bool:
        """
        动态添加或更新一条注入规则。

        Args:
            prompt_name (str): 动态注入组件名称。
            rule (InjectionRule): 注入规则。
            content_provider (Callable | None, optional): 动态内容提供者。
                如果提供，apply_injections 时会调用此函数获取注入内容。
                函数签名应为: async def provider(params: "PromptParameters") -> str
            source (str, optional): 规则来源，默认为 "runtime"。
        """
        if not content_provider:
            logger.error(f"为 '{prompt_name}' 添加动态注入规则失败：必须提供 content_provider。")
            return False

        async with self._lock:
            target_rules = self._dynamic_rules.setdefault(rule.target_prompt, {})
            target_rules[prompt_name] = (rule, content_provider, source)
        logger.info(f"成功添加/更新注入规则: '{prompt_name}' -> '{rule.target_prompt}'")
        return True

    async def remove_injection_rule(self, prompt_name: str, target_prompt: str) -> bool:
        """移除一条动态注入规则。"""
        async with self._lock:
            if target_prompt in self._dynamic_rules and prompt_name in self._dynamic_rules[target_prompt]:
                del self._dynamic_rules[target_prompt][prompt_name]
                if not self._dynamic_rules[target_prompt]:
                    del self._dynamic_rules[target_prompt]
                logger.info(f"成功移除注入规则: '{prompt_name}' from '{target_prompt}'")
                return True
        logger.warning(f"尝试移除注入规则失败: 未找到 '{prompt_name}' on '{target_prompt}'")
        return False

    async def apply_injections(
        self, target_prompt_name: str, original_template: str, params: PromptParameters
    ) -> str:
        """
        【核心方法】根据目标名称，应用所有匹配的注入规则，返回修改后的模板。
        """
        if not self._initialized:
            self.load_static_rules()

        # 1. 从 _dynamic_rules 中获取所有相关的规则
        rules_for_target = list(self._dynamic_rules.get(target_prompt_name, {}).values())
        if not rules_for_target:
            return original_template

        # 2. 按优先级排序
        rules_for_target.sort(key=lambda x: x[0].priority)

        # 3. 依次执行内容提供者 (content_provider) 并拼接模板
        modified_template = original_template
        for rule, provider, source in rules_for_target:
            content = ""
            if rule.injection_type != InjectionType.REMOVE:
                try:
                    content = await provider(params)
                except Exception as e:
                    logger.error(f"执行规则 '{rule}' (来源: {source}) 的内容提供者时失败: {e}")
                    continue  # 跳过失败的 provider

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
                logger.error(f"应用注入规则 '{rule}' 失败: {e}")

        return modified_template

    # --- 状态查询API ---

    def get_core_prompts(self) -> list[str]:
        """获取所有已注册的核心提示词模板名称列表（即所有可注入的目标）。"""
        return list(global_prompt_manager._prompts.keys())

    def get_core_prompt_contents(self) -> dict[str, str]:
        """获取所有核心提示词模板的原始内容。"""
        return {name: prompt.template for name, prompt in global_prompt_manager._prompts.items()}

    def get_registered_prompt_components(self) -> list[PromptInfo]:
        """获取所有在 ComponentRegistry 中注册的 Prompt 组件信息。"""
        components = component_registry.get_components_by_type(ComponentType.PROMPT).values()
        return [info for info in components if isinstance(info, PromptInfo)]

    async def get_full_injection_map(self) -> dict[str, list[dict]]:
        """获取当前完整的注入映射图。"""
        injection_map = {}
        async with self._lock:
            all_targets = set(self._dynamic_rules.keys()) | set(self.get_core_prompts())
            for target in all_targets:
                rules = self._dynamic_rules.get(target, {})
                if not rules:
                    injection_map[target] = []
                    continue

                info_list = []
                for prompt_name, (rule, _, source) in rules.items():
                    info_list.append(
                        {"name": prompt_name, "priority": rule.priority, "source": source}
                    )
                info_list.sort(key=lambda x: x["priority"])
                injection_map[target] = info_list
        return injection_map

    async def get_injections_for_prompt(self, target_prompt_name: str) -> list[dict]:
        """获取指定核心提示词模板的所有注入信息。"""
        full_map = await self.get_full_injection_map()
        return full_map.get(target_prompt_name, [])

    def get_all_dynamic_rules(self) -> dict[str, dict[str, 'InjectionRule']]:
        """获取所有当前的动态注入规则，以 InjectionRule 对象形式返回。"""
        rules_copy = {}
        # 只返回规则对象，隐藏 provider 实现细节
        for target, rules in self._dynamic_rules.items():
            target_copy = {}
            for name, (rule, _, _) in rules.items():
                target_copy[name] = rule
            rules_copy[target] = target_copy
        return copy.deepcopy(rules_copy)

# 创建全局单例
prompt_component_manager = PromptComponentManager()
