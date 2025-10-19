import asyncio
from collections import defaultdict
from typing import Type

from src.chat.utils.prompt_params import PromptParameters
from src.common.logger import get_logger
from src.plugin_system.base.base_prompt import BasePrompt
from src.plugin_system.base.component_types import PromptInfo

logger = get_logger("prompt_component_manager")


class PromptComponentManager:
    """
    管理所有 `BasePrompt` 组件的单例类。

    该管理器负责：
    1. 注册由插件定义的 `BasePrompt` 子类。
    2. 根据注入点（目标Prompt名称）对它们进行分类存储。
    3. 提供一个接口，以便在构建核心Prompt时，能够获取并执行所有相关的组件。
    """

    def __init__(self):
        self._registry: dict[str, list[Type[BasePrompt]]] = defaultdict(list)
        self._prompt_infos: dict[str, PromptInfo] = {}

    def register(self, component_class: Type[BasePrompt]):
        """
        注册一个 `BasePrompt` 组件类。

        Args:
            component_class: 要注册的 `BasePrompt` 子类。
        """
        if not issubclass(component_class, BasePrompt):
            logger.warning(f"尝试注册一个非 BasePrompt 的类: {component_class.__name__}")
            return

        try:
            prompt_info = component_class.get_prompt_info()
            if prompt_info.name in self._prompt_infos:
                logger.warning(f"重复注册 Prompt 组件: {prompt_info.name}。将覆盖旧组件。")

            injection_points = prompt_info.injection_point
            if isinstance(injection_points, str):
                injection_points = [injection_points]

            if not injection_points or not all(injection_points):
                logger.debug(f"Prompt 组件 '{prompt_info.name}' 未指定有效的 injection_point，将不会被自动注入。")
                return

            for point in injection_points:
                self._registry[point].append(component_class)

            self._prompt_infos[prompt_info.name] = prompt_info
            logger.info(f"成功注册 Prompt 组件 '{prompt_info.name}' 到注入点: {injection_points}")

        except ValueError as e:
            logger.error(f"注册 Prompt 组件失败 {component_class.__name__}: {e}")

    def get_components_for(self, injection_point: str) -> list[Type[BasePrompt]]:
        """
        获取指定注入点的所有已注册组件类。

        Args:
            injection_point: 目标Prompt的名称。

        Returns:
            list[Type[BasePrompt]]: 与该注入点关联的组件类列表。
        """
        return self._registry.get(injection_point, [])

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
        from src.plugin_system.core.component_registry import component_registry

        for component_class in component_classes:
            try:
                prompt_info = self._prompt_infos.get(component_class.prompt_name)
                if not prompt_info:
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