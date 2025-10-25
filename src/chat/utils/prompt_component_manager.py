import asyncio

from src.chat.utils.prompt_params import PromptParameters
from src.common.logger import get_logger
from src.plugin_system.base.base_prompt import BasePrompt
from src.plugin_system.base.component_types import ComponentType, PromptInfo
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

    def get_components_for(self, injection_point: str) -> list[type[BasePrompt]]:
        """
        获取指定注入点的所有已注册组件类。

        Args:
            injection_point: 目标Prompt的名称。

        Returns:
            list[Type[BasePrompt]]: 与该注入点关联的组件类列表。
        """
        # 从组件注册中心获取所有启用的Prompt组件
        enabled_prompts = component_registry.get_enabled_components_by_type(ComponentType.PROMPT)

        matching_components: list[type[BasePrompt]] = []

        for prompt_name, prompt_info in enabled_prompts.items():
            # 确保 prompt_info 是 PromptInfo 类型
            if not isinstance(prompt_info, PromptInfo):
                continue

            # 获取注入点信息
            injection_points = prompt_info.injection_point
            if isinstance(injection_points, str):
                injection_points = [injection_points]

            # 检查当前注入点是否匹配
            if injection_point in injection_points:
                # 获取组件类
                component_class = component_registry.get_component_class(prompt_name, ComponentType.PROMPT)
                if component_class and issubclass(component_class, BasePrompt):
                    matching_components.append(component_class)

        return matching_components

    async def execute_components_for(self, injection_point: str, params: PromptParameters) -> str:
        """
        实例化并执行指定注入点的所有组件，然后将它们的输出拼接成一个字符串。

        Args:
            injection_point: 目标Prompt的名称。
            params: 用于初始化组件的 PromptParameters 对象。

        Returns:
            str: 所有相关组件生成的、用换行符连接的文本内容。
        """
        component_classes = self.get_components_for(injection_point)
        if not component_classes:
            return ""

        tasks = []
        for component_class in component_classes:
            try:
                # 从注册中心获取组件信息
                prompt_info = component_registry.get_component_info(
                    component_class.prompt_name, ComponentType.PROMPT
                )
                if not isinstance(prompt_info, PromptInfo):
                    logger.warning(f"找不到 Prompt 组件 '{component_class.prompt_name}' 的信息，无法获取插件配置")
                    plugin_config = {}
                else:
                    plugin_config = component_registry.get_plugin_config(prompt_info.plugin_name)

                instance = component_class(params=params, plugin_config=plugin_config)
                tasks.append(instance.execute())
            except Exception as e:
                logger.error(f"实例化 Prompt 组件 '{component_class.prompt_name}' 失败: {e}")

        if not tasks:
            return ""

        # 并行执行所有组件
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 过滤掉执行失败的结果和空字符串
        valid_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"执行 Prompt 组件 '{component_classes[i].prompt_name}' 失败: {result}")
            elif result and isinstance(result, str) and result.strip():
                valid_results.append(result.strip())

        # 使用换行符拼接所有有效结果
        return "\n".join(valid_results)


# 创建全局单例
prompt_component_manager = PromptComponentManager()
