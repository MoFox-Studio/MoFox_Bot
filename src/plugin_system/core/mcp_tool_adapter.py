"""
MCP Tool Adapter

将 MCP 工具适配为 BaseTool，使其能够被插件系统识别和调用
"""

from typing import Any

import mcp.types

from src.common.logger import get_logger
from src.plugin_system.base.base_tool import BaseTool
from src.plugin_system.base.component_types import ToolParamType

from .mcp_client_manager import mcp_client_manager

logger = get_logger("mcp_tool_adapter")


class MCPToolAdapter(BaseTool):
    """
    MCP 工具适配器

    将 MCP 协议的工具适配为 BaseTool，使其能够：
    1. 被插件系统识别和注册
    2. 被 LLM 调用
    3. 参与工具缓存机制
    """

    def __init__(self, server_name: str, mcp_tool: mcp.types.Tool, plugin_config: dict | None = None):
        """
        初始化 MCP 工具适配器

        Args:
            server_name: MCP 服务器名称
            mcp_tool: MCP 工具对象
            plugin_config: 插件配置（可选）
        """
        super().__init__(plugin_config)

        self.server_name = server_name
        self.mcp_tool = mcp_tool

        # 设置实例属性
        self.name = f"mcp_{server_name}_{mcp_tool.name}"
        self.description = mcp_tool.description or f"MCP tool from {server_name}"
        self.available_for_llm = True  # MCP 工具默认可供 LLM 使用

        # 转换参数定义
        self.parameters = self._convert_parameters(mcp_tool.inputSchema)

        logger.debug(f"创建 MCP 工具适配器: {self.name}")

    def _convert_parameters(
        self, input_schema: dict[str, Any] | None
    ) -> list[tuple[str, ToolParamType, str, bool, list[str] | None]]:
        """
        将 MCP 工具的 JSON Schema 参数转换为 BaseTool 参数格式

        Args:
            input_schema: MCP 工具的 inputSchema (JSON Schema)

        Returns:
            List[Tuple]: BaseTool 参数格式列表
        """
        if not input_schema:
            return []

        parameters = []

        # JSON Schema 通常有 properties 和 required 字段
        properties = input_schema.get("properties", {})
        required_fields = input_schema.get("required", [])

        for param_name, param_def in properties.items():
            # 获取参数类型
            param_type_str = param_def.get("type", "string")
            param_type = self._map_json_type_to_tool_param_type(param_type_str)

            # 获取参数描述
            param_desc = param_def.get("description", f"Parameter {param_name}")

            # 判断是否必填
            is_required = param_name in required_fields

            # 获取枚举值（如果有）
            enum_values = param_def.get("enum")

            parameters.append((param_name, param_type, param_desc, is_required, enum_values))

        return parameters

    @staticmethod
    def _map_json_type_to_tool_param_type(json_type: str) -> ToolParamType:
        """
        将 JSON Schema 类型映射到 ToolParamType

        Args:
            json_type: JSON Schema 类型字符串

        Returns:
            ToolParamType: 对应的工具参数类型
        """
        type_mapping = {
            "string": ToolParamType.STRING,
            "integer": ToolParamType.INTEGER,
            "number": ToolParamType.FLOAT,
            "boolean": ToolParamType.BOOLEAN,
        }
        return type_mapping.get(json_type, ToolParamType.STRING)

    async def execute(self, function_args: dict[str, Any]) -> dict[str, Any]:
        """
        执行 MCP 工具调用

        Args:
            function_args: 工具调用参数

        Returns:
            Dict: 工具执行结果
        """
        try:
            logger.debug(f"执行 MCP 工具: {self.name} | 服务器: {self.server_name} | 参数: {function_args}")

            # 移除 llm_called 标记（这是内部使用的）
            clean_args = {k: v for k, v in function_args.items() if k != "llm_called"}

            # 调用 MCP 客户端管理器执行工具
            result = await mcp_client_manager.call_tool(
                server_name=self.server_name, tool_name=self.mcp_tool.name, arguments=clean_args
            )

            # 解析结果
            return self._format_result(result)

        except Exception as e:
            logger.error(f"MCP 工具执行失败: {self.name} | 错误: {e}")
            return {
                "type": "error",
                "content": f"MCP 工具调用失败: {e!s}",
                "id": self.name,
            }

    def _format_result(self, result: mcp.types.CallToolResult) -> dict[str, Any]:
        """
        格式化 MCP 工具执行结果为标准格式

        Args:
            result: MCP CallToolResult 对象

        Returns:
            Dict: 标准化的工具执行结果
        """
        # MCP 结果包含 content 列表
        if not result.content:
            return {
                "type": "mcp_result",
                "content": "",
                "id": self.name,
            }

        # 提取所有内容
        content_parts = []
        for content_item in result.content:
            # 根据内容类型提取文本
            content_type = getattr(content_item, "type", None)

            if content_type == "text":
                # TextContent 类型
                text = getattr(content_item, "text", "")
                content_parts.append(text)
            elif content_type == "image":
                # ImageContent 类型
                data = getattr(content_item, "data", b"")
                content_parts.append(f"[Image data: {len(data)} bytes]")
            elif content_type == "audio":
                # AudioContent 类型
                data = getattr(content_item, "data", b"")
                content_parts.append(f"[Audio data: {len(data)} bytes]")
            else:
                # 尝试提取 text 或 data 属性
                text = getattr(content_item, "text", None)
                if text is not None:
                    content_parts.append(text)
                else:
                    data = getattr(content_item, "data", None)
                    if data is not None:
                        data_len = len(data) if hasattr(data, "__len__") else "unknown"
                        content_parts.append(f"[Binary data: {data_len} bytes]")
                    else:
                        content_parts.append(str(content_item))

        return {
            "type": "mcp_result",
            "content": "\n".join(content_parts),
            "id": self.name,
            "is_error": getattr(result, "isError", False),
        }

    @classmethod
    def from_mcp_tool(cls, server_name: str, mcp_tool: mcp.types.Tool) -> "MCPToolAdapter":
        """
        从 MCP 工具对象创建适配器实例

        Args:
            server_name: MCP 服务器名称
            mcp_tool: MCP 工具对象

        Returns:
            MCPToolAdapter: 工具适配器实例
        """
        return cls(server_name, mcp_tool)


async def load_mcp_tools_as_adapters() -> list[MCPToolAdapter]:
    """
    加载所有 MCP 工具并转换为适配器

    Returns:
        List[MCPToolAdapter]: 工具适配器列表
    """
    logger.info("开始加载 MCP 工具...")

    # 初始化 MCP 客户端管理器
    await mcp_client_manager.initialize()

    # 获取所有工具
    all_tools_dict = await mcp_client_manager.get_all_tools()

    adapters = []
    total_tools = 0

    for server_name, tools in all_tools_dict.items():
        logger.debug(f"处理服务器 '{server_name}' 的 {len(tools)} 个工具")
        total_tools += len(tools)

        for mcp_tool in tools:
            try:
                adapter = MCPToolAdapter.from_mcp_tool(server_name, mcp_tool)
                adapters.append(adapter)
                logger.debug(f"  ✓ 加载工具: {adapter.name}")
            except Exception as e:
                logger.error(f"  ✗ 创建工具适配器失败: {mcp_tool.name} | 错误: {e}")
                continue

    logger.info(f"MCP 工具加载完成: 成功 {len(adapters)}/{total_tools} 个")
    return adapters
