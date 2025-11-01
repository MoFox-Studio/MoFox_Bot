"""纯异步权限API定义。所有外部调用方必须使用 await。"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any

from src.common.logger import get_logger

logger = get_logger(__name__)


class PermissionLevel(Enum):
    MASTER = "master"


@dataclass
class PermissionNode:
    node_name: str
    description: str
    plugin_name: str
    default_granted: bool = False


@dataclass
class UserInfo:
    platform: str
    user_id: str

    def __post_init__(self):
        self.user_id = str(self.user_id)


class IPermissionManager(ABC):
    @abstractmethod
    async def check_permission(self, user: UserInfo, permission_node: str) -> bool: ...

    @abstractmethod
    async def is_master(self, user: UserInfo) -> bool: ...  # 同步快速判断

    @abstractmethod
    async def register_permission_node(self, node: PermissionNode) -> bool: ...

    @abstractmethod
    async def grant_permission(self, user: UserInfo, permission_node: str) -> bool: ...

    @abstractmethod
    async def revoke_permission(self, user: UserInfo, permission_node: str) -> bool: ...

    @abstractmethod
    async def get_user_permissions(self, user: UserInfo) -> list[str]: ...

    @abstractmethod
    async def get_all_permission_nodes(self) -> list[PermissionNode]: ...

    @abstractmethod
    async def get_plugin_permission_nodes(self, plugin_name: str) -> list[PermissionNode]: ...


class PermissionAPI:
    def __init__(self):
        self._permission_manager: IPermissionManager | None = None
        # 需要保留的前缀（视为绝对节点名，不再自动加 plugins.<plugin>. 前缀）
        self.RESERVED_PREFIXES: tuple[str, ...] = ("system.",)
        # 系统节点列表 (name, description, default_granted)
        self._SYSTEM_NODES: list[tuple[str, str, bool]] = [
            ("system.superuser", "系统超级管理员：拥有所有权限", False),
            ("system.permission.manage", "系统权限管理：可管理所有权限节点", False),
            ("system.permission.view", "系统权限查看：可查看所有权限节点", True),
        ]
        self._system_nodes_initialized: bool = False

    def set_permission_manager(self, manager: IPermissionManager):
        self._permission_manager = manager
        logger.info("权限管理器已设置")

    def _ensure_manager(self):
        if self._permission_manager is None:
            raise RuntimeError("权限管理器未设置，请先调用 set_permission_manager")

    async def check_permission(self, platform: str, user_id: str, permission_node: str) -> bool:
        self._ensure_manager()
        if not self._permission_manager:
            return False
        return await self._permission_manager.check_permission(UserInfo(platform, user_id), permission_node)

    async def is_master(self, platform: str, user_id: str) -> bool:
        self._ensure_manager()
        if not self._permission_manager:
            return False
        return await self._permission_manager.is_master(UserInfo(platform, user_id))

    async def register_permission_node(
        self,
        node_name: str,
        description: str,
        plugin_name: str,
        default_granted: bool = False,
        *,
        system: bool = False,
        allow_relative: bool = True,
    ) -> bool:
        self._ensure_manager()
        original_name = node_name
        if system:
            # 系统节点必须以 system./sys./core. 等保留前缀开头
            if not node_name.startswith(("system.", "sys.", "core.")):
                node_name = f"system.{node_name}"  # 自动补 system.
        else:
            # 普通插件节点：若不以保留前缀开头，并允许相对，则自动加前缀
            if allow_relative and not node_name.startswith(self.RESERVED_PREFIXES):
                node_name = f"plugins.{plugin_name}.{node_name}"
        if original_name != node_name:
            logger.debug(f"规范化权限节点 '{original_name}' -> '{node_name}'")
        node = PermissionNode(node_name, description, plugin_name, default_granted)
        if not self._permission_manager:
            return False
        return await self._permission_manager.register_permission_node(node)

    async def register_system_permission_node(
        self, node_name: str, description: str, default_granted: bool = False
    ) -> bool:
        """注册系统级权限节点（不绑定具体插件，前缀保持 system./sys./core.）。"""
        return await self.register_permission_node(
            node_name,
            description,
            plugin_name="__system__",
            default_granted=default_granted,
            system=True,
            allow_relative=True,
        )

    async def init_system_nodes(self) -> None:
        """初始化默认系统权限节点（幂等）。

        在设置 permission_manager 之后且数据库准备好时调用一次即可。
        """
        if self._system_nodes_initialized:
            return
        self._ensure_manager()
        for name, desc, granted in self._SYSTEM_NODES:
            try:
                await self.register_system_permission_node(name, desc, granted)
            except Exception as e:  # 防御性
                logger.warning(f"注册系统权限节点 {name} 失败: {e}")
        self._system_nodes_initialized = True

    async def grant_permission(self, platform: str, user_id: str, permission_node: str) -> bool:
        self._ensure_manager()
        if not self._permission_manager:
            return False
        return await self._permission_manager.grant_permission(UserInfo(platform, user_id), permission_node)

    async def revoke_permission(self, platform: str, user_id: str, permission_node: str) -> bool:
        self._ensure_manager()
        if not self._permission_manager:
            return False
        return await self._permission_manager.revoke_permission(UserInfo(platform, user_id), permission_node)

    async def get_user_permissions(self, platform: str, user_id: str) -> list[str]:
        self._ensure_manager()
        if not self._permission_manager:
            return []
        return await self._permission_manager.get_user_permissions(UserInfo(platform, user_id))

    async def get_all_permission_nodes(self) -> list[dict[str, Any]]:
        self._ensure_manager()
        if not self._permission_manager:
            return []
        nodes = await self._permission_manager.get_all_permission_nodes()
        return [
            {
                "node_name": n.node_name,
                "description": n.description,
                "plugin_name": n.plugin_name,
                "default_granted": n.default_granted,
            }
            for n in nodes
        ]

    async def get_plugin_permission_nodes(self, plugin_name: str) -> list[dict[str, Any]]:
        self._ensure_manager()
        if not self._permission_manager:
            return []
        nodes = await self._permission_manager.get_plugin_permission_nodes(plugin_name)
        return [
            {
                "node_name": n.node_name,
                "description": n.description,
                "plugin_name": n.plugin_name,
                "default_granted": n.default_granted,
            }
            for n in nodes
        ]


permission_api = PermissionAPI()
