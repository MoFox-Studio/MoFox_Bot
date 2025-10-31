from abc import ABC, abstractmethod
from typing import Any, ClassVar

from rich.traceback import install

from src.common.logger import get_logger
from src.plugin_system.base.component_types import ComponentType, ToolInfo, ToolParamType

install(extra_lines=3)

logger = get_logger("base_tool")


class BaseTool(ABC):
    """所有工具的基类"""

    name: str = ""
    """工具的名称"""
    description: str = ""
    """工具的描述"""
    parameters: ClassVar[list[tuple[str, ToolParamType, str, bool, list[str] | None]] ] = []
    """工具的参数定义，为[("param_name", param_type, "description", required, enum_values)]格式
       param_name: 参数名称
       param_type: 参数类型
       description: 参数描述
       required: 是否必填
       enum_values: 枚举值列表
       例如: [("arg1", ToolParamType.STRING, "参数1描述", True, None), ("arg2", ToolParamType.INTEGER, "参数2描述", False, ["1", "2", "3"])]
    """
    available_for_llm: bool = False
    """是否可供LLM使用"""
    history_ttl: int = 5
    """工具调用历史记录的TTL值，默认为5。设为0表示不记录历史"""

    enable_cache: bool = False
    """是否为该工具启用缓存"""
    cache_ttl: int = 3600
    """缓存的TTL值（秒），默认为3600秒（1小时）"""
    semantic_cache_query_key: str | None = None
    """用于语义缓存的查询参数键名。如果设置，将使用此参数的值进行语义相似度搜索"""

    # 二步工具调用相关属性
    is_two_step_tool: bool = False
    """是否为二步工具。如果为True，工具将分两步调用：第一步展示工具信息，第二步执行具体操作"""
    step_one_description: str = ""
    """第一步的描述，用于向LLM展示工具的基本功能"""
    sub_tools: ClassVar[list[tuple[str, str, list[tuple[str, ToolParamType, str, bool, list[str] | None]]]] ] = []
    """子工具列表，格式为[(子工具名, 子工具描述, 子工具参数)]。仅在二步工具中使用"""

    def __init__(self, plugin_config: dict | None = None, chat_stream: Any = None):
        self.plugin_config = plugin_config or {}  # 直接存储插件配置字典
        self.chat_stream = chat_stream  # 存储聊天流信息，可用于获取上下文

    @classmethod
    def get_tool_definition(cls) -> dict[str, Any]:
        """获取工具定义，用于LLM工具调用

        Returns:
            dict: 工具定义字典
        """
        if not cls.name or not cls.description:
            raise NotImplementedError(f"工具类 {cls.__name__} 必须定义 name 和 description 属性")

        # 如果是二步工具，第一步只返回基本信息
        if cls.is_two_step_tool:
            return {
                "name": cls.name,
                "description": cls.step_one_description or cls.description,
                "parameters": [
                    (
                        "action",
                        ToolParamType.STRING,
                        "选择要执行的操作",
                        True,
                        [sub_tool[0] for sub_tool in cls.sub_tools],
                    )
                ],
            }
        else:
            # 普通工具需要parameters
            if not cls.parameters:
                raise NotImplementedError(f"工具类 {cls.__name__} 必须定义 parameters 属性")
            return {"name": cls.name, "description": cls.description, "parameters": cls.parameters}

    @classmethod
    def get_step_two_tool_definition(cls, sub_tool_name: str) -> dict[str, Any]:
        """获取二步工具的第二步定义

        Args:
            sub_tool_name: 子工具名称

        Returns:
            dict: 第二步工具定义字典
        """
        if not cls.is_two_step_tool:
            raise ValueError(f"工具 {cls.name} 不是二步工具")

        # 查找对应的子工具
        for sub_name, sub_desc, sub_params in cls.sub_tools:
            if sub_name == sub_tool_name:
                return {"name": f"{cls.name}_{sub_tool_name}", "description": sub_desc, "parameters": sub_params}

        raise ValueError(f"未找到子工具: {sub_tool_name}")

    @classmethod
    def get_all_sub_tool_definitions(cls) -> list[dict[str, Any]]:
        """获取所有子工具的定义

        Returns:
            List[dict]: 所有子工具定义列表
        """
        if not cls.is_two_step_tool:
            return []

        definitions: ClassVar = []
        for sub_name, sub_desc, sub_params in cls.sub_tools:
            definitions.append({"name": f"{cls.name}_{sub_name}", "description": sub_desc, "parameters": sub_params})
        return definitions

    @classmethod
    def get_tool_info(cls) -> ToolInfo:
        """获取工具信息"""
        if not cls.name or not cls.description or not cls.parameters:
            raise NotImplementedError(f"工具类 {cls.__name__} 必须定义 name, description 和 parameters 属性")

        return ToolInfo(
            name=cls.name,
            tool_description=cls.description,
            enabled=cls.available_for_llm,
            tool_parameters=cls.parameters,
            component_type=ComponentType.TOOL,
        )

    @abstractmethod
    async def execute(self, function_args: dict[str, Any]) -> dict[str, Any]:
        """执行工具函数(供llm调用)
           通过该方法，maicore会通过llm的tool call来调用工具
           传入的是json格式的参数，符合parameters定义的格式

        Args:
            function_args: 工具调用参数

        Returns:
            dict: 工具执行结果
        """
        # 如果是二步工具，处理第一步调用
        if self.is_two_step_tool and "action" in function_args:
            return await self._handle_step_one(function_args)

        raise NotImplementedError("子类必须实现execute方法")

    async def _handle_step_one(self, function_args: dict[str, Any]) -> dict[str, Any]:
        """处理二步工具的第一步调用

        Args:
            function_args: 包含action参数的函数参数

        Returns:
            dict: 第一步执行结果，包含第二步的工具定义
        """
        action = function_args.get("action")
        if not action:
            return {"error": "缺少action参数"}

        # 查找对应的子工具
        sub_tool_found = None
        for sub_name, sub_desc, sub_params in self.sub_tools:
            if sub_name == action:
                sub_tool_found = (sub_name, sub_desc, sub_params)
                break

        if not sub_tool_found:
            available_actions = [sub_tool[0] for sub_tool in self.sub_tools]
            return {"error": f"未知的操作: {action}。可用操作: {available_actions}"}

        sub_name, sub_desc, sub_params = sub_tool_found

        # 返回第二步工具定义
        step_two_definition = {"name": f"{self.name}_{sub_name}", "description": sub_desc, "parameters": sub_params}

        return {
            "type": "two_step_tool_step_one",
            "content": f"已选择操作: {action}。请使用以下工具进行具体调用:",
            "next_tool_definition": step_two_definition,
            "selected_action": action,
        }

    async def execute_step_two(self, sub_tool_name: str, function_args: dict[str, Any]) -> dict[str, Any]:
        """执行二步工具的第二步

        Args:
            sub_tool_name: 子工具名称
            function_args: 工具调用参数

        Returns:
            dict: 工具执行结果
        """
        if not self.is_two_step_tool:
            raise ValueError(f"工具 {self.name} 不是二步工具")

        # 子类需要重写此方法来实现具体的第二步逻辑
        raise NotImplementedError("二步工具必须实现execute_step_two方法")

    async def direct_execute(self, **kwargs: dict[str, Any]) -> dict[str, Any]:
        """直接执行工具函数(供插件调用)
           通过该方法，插件可以直接调用工具，而不需要传入字典格式的参数
           插件可以直接调用此方法，用更加明了的方式传入参数
           示例: result = await tool.direct_execute(arg1="参数",arg2="参数2")

           工具开发者可以重写此方法以实现与llm调用差异化的执行逻辑

        Args:
            **function_args: 工具调用参数

        Returns:
            dict: 工具执行结果
        """
        parameter_required = [param[0] for param in self.parameters if param[3]]  # 获取所有必填参数名
        for param_name in parameter_required:
            if param_name not in kwargs:
                raise ValueError(f"工具类 {self.__class__.__name__} 缺少必要参数: {param_name}")

        return await self.execute(kwargs)

    def get_config(self, key: str, default=None):
        """获取插件配置值，使用嵌套键访问

        Args:
            key: 配置键名，使用嵌套访问如 "section.subsection.key"
            default: 默认值

        Returns:
            Any: 配置值或默认值
        """
        if not self.plugin_config:
            return default

        # 支持嵌套键访问
        keys = key.split(".")
        current = self.plugin_config

        for k in keys:
            if isinstance(current, dict) and k in current:
                current = current[k]
            else:
                return default

        return current
