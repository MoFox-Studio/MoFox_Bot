"""
MCP Client Manager

管理多个 MCP (Model Context Protocol) 客户端连接，支持动态加载和工具注册
"""

import asyncio
import shutil
from pathlib import Path
from typing import Any

import mcp.types
import orjson
from fastmcp.client import Client, StdioTransport, StreamableHttpTransport

from src.common.logger import get_logger

logger = get_logger("mcp_client_manager")


class MCPServerConfig:
    """单个 MCP 服务器的配置"""

    def __init__(self, name: str, config: dict[str, Any]):
        self.name = name
        self.description = config.get("description", "")
        self.enabled = config.get("enabled", True)
        self.transport_config = config["transport"]
        self.auth_config = config.get("auth")
        self.timeout = config.get("timeout", 30)
        self.retry_config = config.get("retry", {"max_retries": 3, "retry_delay": 1})

    def __repr__(self):
        return f"<MCPServerConfig {self.name} (enabled={self.enabled})>"


class MCPClientManager:
    """
    MCP 客户端管理器

    负责：
    1. 从配置文件加载 MCP 服务器配置
    2. 建立和维护与 MCP 服务器的连接
    3. 获取可用的工具列表
    4. 执行工具调用
    """

    def __init__(self, config_path: str | Path | None = None):
        """
        初始化 MCP 客户端管理器

        Args:
            config_path: mcp.json 配置文件路径，默认为 config/mcp.json
        """
        if config_path is None:
            # 默认配置路径

            config_path = Path(__file__).parent.parent.parent.parent / "config" / "mcp.json"

        self.config_path = Path(config_path)
        self.servers: dict[str, MCPServerConfig] = {}
        self.clients: dict[str, Client] = {}
        self._initialized = False
        self._lock = asyncio.Lock()

        logger.info(f"MCP 客户端管理器初始化，配置文件: {self.config_path}")

    def load_config(self) -> dict[str, MCPServerConfig]:
        """
        从配置文件加载 MCP 服务器配置

        Returns:
            Dict[str, MCPServerConfig]: 服务器名称 -> 配置对象
        """
        if not self.config_path.exists():
            # 尝试从模板创建配置文件
            template_path = Path("template/mcp.json")
            if template_path.exists():
                try:
                    self.config_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy(template_path, self.config_path)
                    logger.info(f"已从模板创建 MCP 配置文件: {self.config_path}")
                except Exception as e:
                    logger.error(f"从模板创建配置文件失败: {e}")
                    return {}
            else:
                logger.warning(f"MCP 配置文件和模板都不存在: {self.config_path}, {template_path}")
                return {}

        try:
            with open(self.config_path, encoding="utf-8") as f:
                config_data = orjson.loads(f.read())

            servers = {}
            mcp_servers = config_data.get("mcpServers", {})

            for server_name, server_config in mcp_servers.items():
                try:
                    server = MCPServerConfig(server_name, server_config)
                    servers[server_name] = server
                    logger.debug(f"加载 MCP 服务器配置: {server}")
                except Exception as e:
                    logger.error(f"加载服务器配置 '{server_name}' 失败: {e}")
                    continue

            logger.info(f"成功加载 {len(servers)} 个 MCP 服务器配置")
            return servers

        except orjson.JSONDecodeError as e:
            logger.error(f"解析 MCP 配置文件失败: {e}")
            return {}
        except Exception as e:
            logger.error(f"读取 MCP 配置文件失败: {e}")
            return {}

    async def initialize(self) -> None:
        """
        初始化所有启用的 MCP 客户端连接

        这个方法会：
        1. 加载配置文件
        2. 为每个启用的服务器创建客户端
        3. 建立连接并验证
        """
        async with self._lock:
            if self._initialized:
                logger.debug("MCP 客户端管理器已初始化，跳过")
                return

            logger.info("开始初始化 MCP 客户端连接...")

            # 加载配置
            self.servers = self.load_config()

            if not self.servers:
                logger.warning("没有找到任何 MCP 服务器配置")
                self._initialized = True
                return

            # 为每个启用的服务器创建客户端
            for server_name, server_config in self.servers.items():
                if not server_config.enabled:
                    logger.debug(f"服务器 '{server_name}' 未启用，跳过")
                    continue

                try:
                    client = await self._create_client(server_config)
                    self.clients[server_name] = client
                    logger.info(f"✅ MCP 服务器 '{server_name}' 连接成功")
                except Exception as e:
                    logger.error(f"❌ 连接 MCP 服务器 '{server_name}' 失败: {e}")
                    continue

            self._initialized = True
            logger.info(f"MCP 客户端管理器初始化完成，成功连接 {len(self.clients)}/{len(self.servers)} 个服务器")

    async def _create_client(self, server_config: MCPServerConfig) -> Client:
        """
        根据配置创建 MCP 客户端

        Args:
            server_config: 服务器配置

        Returns:
            Client: 已连接的 MCP 客户端
        """
        transport_type = server_config.transport_config.get("type", "streamable-http")

        if transport_type == "streamable-http":
            url = server_config.transport_config["url"]
            transport = StreamableHttpTransport(url)

            # 设置认证（如果有）
            if server_config.auth_config:
                auth_type = server_config.auth_config.get("type")
                if auth_type == "bearer":
                    from fastmcp.client.auth import BearerAuth

                    token = server_config.auth_config.get("token", "")
                    transport._set_auth(BearerAuth(token))

            client = Client(transport, timeout=server_config.timeout)

        elif transport_type == "stdio":
            # stdio 传输：通过标准输入输出与本地进程通信
            command = server_config.transport_config.get("command")
            args = server_config.transport_config.get("args", [])

            if not command:
                raise ValueError("stdio 传输需要提供 'command' 参数")

            # 创建 stdio 传输
            transport = StdioTransport(command, args)
            client = Client(transport, timeout=server_config.timeout)

        else:
            raise ValueError(f"不支持的传输类型: {transport_type}")

        # 进入客户端上下文（建立连接）
        await client.__aenter__()

        return client

    async def get_all_tools(self) -> dict[str, list[mcp.types.Tool]]:
        """
        获取所有 MCP 服务器提供的工具列表

        Returns:
            Dict[str, List[mcp.types.Tool]]: 服务器名称 -> 工具列表
        """
        if not self._initialized:
            await self.initialize()

        all_tools = {}

        for server_name, client in self.clients.items():
            try:
                # fastmcp 的 list_tools() 直接返回 List[Tool]，不是包含 tools 属性的对象
                tools = await client.list_tools()
                all_tools[server_name] = tools
                logger.debug(f"从服务器 '{server_name}' 获取到 {len(tools)} 个工具")
            except Exception as e:
                logger.error(f"从服务器 '{server_name}' 获取工具列表失败: {e}")
                all_tools[server_name] = []

        return all_tools

    async def call_tool(
        self, server_name: str, tool_name: str, arguments: dict[str, Any] | None = None
    ) -> Any:
        """
        调用指定 MCP 服务器的工具

        Args:
            server_name: 服务器名称
            tool_name: 工具名称
            arguments: 工具参数

        Returns:
            Any: 工具执行结果（CallToolResult 的兼容类型）
        """
        if not self._initialized:
            await self.initialize()

        if server_name not in self.clients:
            raise ValueError(f"MCP 服务器 '{server_name}' 未连接")

        client = self.clients[server_name]

        try:
            logger.debug(f"调用 MCP 工具: {server_name}.{tool_name} | 参数: {arguments}")
            result = await client.call_tool(tool_name, arguments or {})
            logger.debug(f"MCP 工具调用成功: {server_name}.{tool_name}")
            return result

        except Exception as e:
            logger.error(f"MCP 工具调用失败: {server_name}.{tool_name} | 错误: {e}")
            raise

    async def close(self) -> None:
        """关闭所有 MCP 客户端连接"""
        async with self._lock:
            if not self._initialized:
                return

            logger.info("关闭所有 MCP 客户端连接...")

            for server_name, client in self.clients.items():
                try:
                    await client.__aexit__(None, None, None)
                    logger.debug(f"已关闭 MCP 服务器 '{server_name}' 的连接")
                except Exception as e:
                    logger.error(f"关闭服务器 '{server_name}' 连接失败: {e}")

            self.clients.clear()
            self._initialized = False
            logger.info("所有 MCP 客户端连接已关闭")

    def __repr__(self):
        return f"<MCPClientManager servers={len(self.servers)} clients={len(self.clients)}>"


# 全局单例
mcp_client_manager = MCPClientManager()
