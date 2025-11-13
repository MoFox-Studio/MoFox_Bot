from abc import abstractmethod

from src.common.logger import get_logger
from src.plugin_system.base.component_types import (
    ActionInfo,
    CommandInfo,
    ComponentType,
    EventHandlerInfo,
    InterestCalculatorInfo,
    PlusCommandInfo,
    PromptInfo,
    ToolInfo,
)

from .base_action import BaseAction
from .base_command import BaseCommand
from .base_events_handler import BaseEventHandler
from .base_interest_calculator import BaseInterestCalculator
from .base_prompt import BasePrompt
from .base_tool import BaseTool
from .plugin_base import PluginBase
from .plus_command import PlusCommand

logger = get_logger("base_plugin")


class BasePlugin(PluginBase):
    """基于Action和Command的插件基类

    所有上述类型的插件都应该继承这个基类，一个插件可以包含多种组件：
    - Action组件：处理聊天中的动作
    - Command组件：处理命令请求
    - 未来可扩展：Scheduler、Listener等
    """

    @classmethod
    def _get_component_info_from_class(cls, component_class: type, component_type: ComponentType):
        """从组件类自动生成组件信息

        Args:
            component_class: 组件类
            component_type: 组件类型

        Returns:
            对应类型的ComponentInfo对象
        """
        if component_type == ComponentType.COMMAND:
            if hasattr(component_class, "get_command_info"):
                return component_class.get_command_info()
            else:
                logger.warning(f"Command类 {component_class.__name__} 缺少 get_command_info 方法")
                return None

        elif component_type == ComponentType.ACTION:
            if hasattr(component_class, "get_action_info"):
                return component_class.get_action_info()
            else:
                logger.warning(f"Action类 {component_class.__name__} 缺少 get_action_info 方法")
                return None

        elif component_type == ComponentType.INTEREST_CALCULATOR:
            if hasattr(component_class, "get_interest_calculator_info"):
                return component_class.get_interest_calculator_info()
            else:
                logger.warning(
                    f"InterestCalculator类 {component_class.__name__} 缺少 get_interest_calculator_info 方法"
                )
                return None

        elif component_type == ComponentType.PLUS_COMMAND:
            # PlusCommand的get_info逻辑可以在这里实现
            logger.warning("PlusCommand的get_info逻辑尚未实现")
            return None

        elif component_type == ComponentType.TOOL:
            # Tool的get_info逻辑可以在这里实现
            logger.warning("Tool的get_info逻辑尚未实现")
            return None

        elif component_type == ComponentType.EVENT_HANDLER:
            # EventHandler的get_info逻辑可以在这里实现
            logger.warning("EventHandler的get_info逻辑尚未实现")
            return None

        elif component_type == ComponentType.PROMPT:
            if hasattr(component_class, "get_prompt_info"):
                return component_class.get_prompt_info()
            else:
                logger.warning(f"Prompt类 {component_class.__name__} 缺少 get_prompt_info 方法")
                return None

        else:
            logger.error(f"不支持的组件类型: {component_type}")
            return None

    @classmethod
    def get_component_info(cls, component_class: type, component_type: ComponentType):
        """获取组件信息的通用方法

        这是一个便捷方法，内部调用_get_component_info_from_class

        Args:
            component_class: 组件类
            component_type: 组件类型

        Returns:
            对应类型的ComponentInfo对象
        """
        return cls._get_component_info_from_class(component_class, component_type)

    @abstractmethod
    def get_plugin_components(
        self,
    ) -> list[
        tuple[ActionInfo, type[BaseAction]]
        | tuple[CommandInfo, type[BaseCommand]]
        | tuple[PlusCommandInfo, type[PlusCommand]]
        | tuple[EventHandlerInfo, type[BaseEventHandler]]
        | tuple[ToolInfo, type[BaseTool]]
        | tuple[InterestCalculatorInfo, type[BaseInterestCalculator]]
        | tuple[PromptInfo, type[BasePrompt]]
    ]:
        """获取插件包含的组件列表

        子类必须实现此方法，返回组件信息和组件类的列表

        Returns:
            List[tuple[ComponentInfo, Type]]: [(组件信息, 组件类), ...]
        """
        ...

    def register_plugin(self) -> bool:
        """注册插件及其所有组件"""
        from src.plugin_system.core.component_registry import component_registry

        components = self.get_plugin_components()

        # 注册所有组件
        registered_components = []
        for component_info, component_class in components:
            component_info.plugin_name = self.plugin_name
            if component_registry.register_component(component_info, component_class):
                registered_components.append(component_info)
            else:
                logger.warning(f"{self.log_prefix} 组件 {component_info.name} 注册失败")

        # 更新插件信息中的组件列表
        self.plugin_info.components = registered_components

        # 注册插件
        if component_registry.register_plugin(self.plugin_info):
            logger.debug(f"{self.log_prefix} 插件注册成功，包含 {len(registered_components)} 个组件")
            return True
        else:
            logger.error(f"{self.log_prefix} 插件注册失败")
            return False
