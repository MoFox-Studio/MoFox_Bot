from abc import ABC, abstractmethod
from typing import Any

from src.chat.utils.prompt_params import PromptParameters
from src.common.logger import get_logger
from src.plugin_system.base.component_types import ComponentType, PromptInfo

logger = get_logger("base_prompt")


class BasePrompt(ABC):
    """Prompt组件基类

    Prompt是插件的一种组件类型，用于动态地向现有的核心Prompt模板中注入额外的上下文信息。
    它的主要作用是在不修改核心代码的情况下，扩展和定制模型的行为。

    子类可以通过类属性定义其行为：
    - prompt_name: Prompt组件的唯一名称。
    - injection_point: 指定要注入的目标Prompt名称（或名称列表）。
    """

    prompt_name: str = ""
    """Prompt组件的名称"""
    prompt_description: str = ""
    """Prompt组件的描述"""

    # 定义此组件希望注入到哪个或哪些核心Prompt中
    # 可以是一个字符串（单个目标）或字符串列表（多个目标）
    # 例如: "planner_prompt" 或 ["s4u_style_prompt", "normal_style_prompt"]
    injection_point: str | list[str] = ""
    """要注入的目标Prompt名称或列表"""

    def __init__(self, params: PromptParameters, plugin_config: dict | None = None):
        """初始化Prompt组件

        Args:
            params: 统一提示词参数，包含所有构建提示词所需的上下文信息。
            plugin_config: 插件配置字典。
        """
        self.params = params
        self.plugin_config = plugin_config or {}
        self.log_prefix = "[PromptComponent]"

        logger.debug(f"{self.log_prefix} Prompt组件 '{self.prompt_name}' 初始化完成")

    @abstractmethod
    async def execute(self) -> str:
        """执行Prompt生成的抽象方法，子类必须实现。

        此方法应根据初始化时传入的 `self.params` 来构建并返回一个字符串。
        返回的字符串将被拼接到目标Prompt的最前面。

        Returns:
            str: 生成的文本内容。
        """
        pass

    def get_config(self, key: str, default: Any = None) -> Any:
        """获取插件配置值，支持嵌套键访问。

        Args:
            key: 配置键名，使用点号进行嵌套访问，如 "section.subsection.key"。
            default: 未找到键时返回的默认值。

        Returns:
            Any: 配置值或默认值。
        """
        if not self.plugin_config:
            return default

        keys = key.split(".")
        current = self.plugin_config
        for k in keys:
            if isinstance(current, dict) and k in current:
                current = current[k]
            else:
                return default
        return current

    @classmethod
    def get_prompt_info(cls) -> "PromptInfo":
        """从类属性生成PromptInfo，用于组件注册和管理。

        Returns:
            PromptInfo: 生成的Prompt信息对象。
        """
        if not cls.prompt_name:
            raise ValueError("Prompt组件必须定义 'prompt_name' 类属性。")

        return PromptInfo(
            name=cls.prompt_name,
            component_type=ComponentType.PROMPT,
            description=cls.prompt_description,
            injection_point=cls.injection_point,
        )
