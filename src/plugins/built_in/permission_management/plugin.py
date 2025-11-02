"""
权限管理插件

提供权限系统的管理命令，包括权限授权、撤销、查询等功能。
使用新的PlusCommand系统重构。
"""

import re
from typing import ClassVar

from src.plugin_system.apis.logging_api import get_logger
from src.plugin_system.apis.permission_api import permission_api
from src.plugin_system.apis.plugin_register_api import register_plugin
from src.plugin_system.base.base_plugin import BasePlugin
from src.plugin_system.base.command_args import CommandArgs
from src.plugin_system.base.component_types import (
    ChatType,
    PermissionNodeField,
    PlusCommandInfo,
)
from src.plugin_system.base.config_types import ConfigField
from src.plugin_system.base.plus_command import PlusCommand
from src.plugin_system.utils.permission_decorators import require_permission

logger = get_logger("Permission")


class PermissionCommand(PlusCommand):
    """权限管理命令 - 使用PlusCommand系统"""

    command_name = "permission"
    command_description = "权限管理命令，支持授权、撤销、查询等功能"
    command_aliases: ClassVar[list[str]] = ["perm", "权限"]
    priority = 10
    chat_type_allow = ChatType.ALL
    intercept_message = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    async def execute(self, args: CommandArgs) -> tuple[bool, str | None, bool]:
        """执行权限管理命令"""
        if args.is_empty:
            await self._show_help()
            return True, "显示帮助信息", True

        subcommand = args.get_first.lower()
        remaining_args = args.get_args()[1:]  # 获取除第一个参数外的所有参数
        chat_stream = self.message.chat_info.stream_id

        if subcommand in ["grant", "授权", "give"]:
            await self._grant_permission(chat_stream, remaining_args)
            return True, "执行授权命令", True

        elif subcommand in ["revoke", "撤销", "remove"]:
            await self._revoke_permission(chat_stream, remaining_args)
            return True, "执行撤销命令", True

        elif subcommand in ["list", "列表", "ls"]:
            await self._list_permissions(chat_stream, remaining_args)
            return True, "执行列表命令", True

        elif subcommand in ["check", "检查"]:
            await self._check_permission(chat_stream, remaining_args)
            return True, "执行检查命令", True

        elif subcommand in ["nodes", "节点"]:
            await self._list_nodes(chat_stream, remaining_args)
            return True, "执行节点命令", True

        elif subcommand in ["allnodes", "全部节点", "all"]:
            await self._list_all_nodes_with_description(chat_stream)
            return True, "执行全部节点命令", True

        elif subcommand in ["help", "帮助"]:
            await self._show_help()
            return True, "显示帮助信息", True

        else:
            await self.send_text(f"❌ 未知的子命令: {subcommand}\n使用 /permission help 查看帮助")
            return True, "未知子命令", True

    async def _show_help(self):
        """显示帮助信息"""
        help_text = """📋 权限管理命令帮助

🔐 管理命令（需要管理权限）：
• /permission grant <@用户|QQ号> <权限节点> - 授权用户权限
• /permission revoke <@用户|QQ号> <权限节点> - 撤销用户权限

👀 查看命令（需要查看权限）：
• /permission list [用户] - 查看用户权限列表
• /permission check <@用户|QQ号> <权限节点> - 检查用户是否拥有权限
• /permission nodes [插件名] - 查看权限节点列表
• /permission allnodes - 查看所有插件的权限节点详情

❓ 其他：
• /permission help - 显示此帮助

📝 示例：
• /permission grant @张三 plugin.example.command
• /permission list 123456789
• /permission nodes example_plugin
• /permission allnodes

🔄 别名：可以使用 /perm 或 /权限 代替 /permission"""

        await self.send_text(help_text)

    @staticmethod
    def _parse_user_mention(mention: str) -> str | None:
        """解析用户提及，提取QQ号

        支持的格式:
        - @<用户名:QQ号> 格式
        - [CQ:at,qq=QQ号] 格式
        - 直接的QQ号
        """
        # 匹配 @<用户名:QQ号> 格式，提取QQ号
        at_match = re.search(r"@<[^:]+:(\d+)>", mention)
        if at_match:
            return at_match.group(1)

        # 直接是数字
        if mention.isdigit():
            return mention

        return None

    @staticmethod
    def parse_user_from_args(args: CommandArgs, index: int = 0) -> str | None:
        """从CommandArgs中解析用户ID

        Args:
            args: 命令参数对象
            index: 参数索引，默认为0（第一个参数）

        Returns:
            Optional[str]: 解析出的用户ID，如果解析失败返回None
        """
        if index >= args.count():
            return None

        mention = args.get_arg(index)

        # 匹配 @<用户名:QQ号> 格式，提取QQ号
        at_match = re.search(r"@<[^:]+:(\d+)>", mention)
        if at_match:
            return at_match.group(1)

        # 匹配传统的 [CQ:at,qq=数字] 格式
        cq_match = re.search(r"\[CQ:at,qq=(\d+)\]", mention)
        if cq_match:
            return cq_match.group(1)

        # 直接是数字
        if mention.isdigit():
            return mention

        return None

    @require_permission("plugin.permission.manage", "❌ 你没有权限管理的权限")
    async def _grant_permission(self, chat_stream, args: list[str]):
        """授权用户权限"""
        if len(args) < 2:
            await self.send_text("❌ 用法: /permission grant <@用户|QQ号> <权限节点>")
            return

        # 解析用户ID - 使用新的解析方法
        user_id = self._parse_user_mention(args[0])
        if not user_id:
            await self.send_text("❌ 无效的用户格式，请使用 @<用户名:QQ号> 或直接输入QQ号")
            return

        permission_node = args[1]

        # 执行授权
        success = await permission_api.grant_permission(chat_stream.platform, user_id, permission_node)

        if success:
            await self.send_text(f"✅ 已授权用户 {user_id} 权限节点 `{permission_node}`")
        else:
            await self.send_text("❌ 授权失败，请检查权限节点是否存在")

    @require_permission("plugin.permission.manage", "❌ 你没有权限管理的权限")
    async def _revoke_permission(self, chat_stream, args: list[str]):
        """撤销用户权限"""
        if len(args) < 2:
            await self.send_text("❌ 用法: /permission revoke <@用户|QQ号> <权限节点>")
            return

        # 解析用户ID - 使用新的解析方法
        user_id = self._parse_user_mention(args[0])
        if not user_id:
            await self.send_text("❌ 无效的用户格式，请使用 @<用户名:QQ号> 或直接输入QQ号")
            return

        permission_node = args[1]

        # 执行撤销
        success = await permission_api.revoke_permission(chat_stream.platform, user_id, permission_node)

        if success:
            await self.send_text(f"✅ 已撤销用户 {user_id} 权限节点 `{permission_node}`")
        else:
            await self.send_text("❌ 撤销失败，请检查权限节点是否存在")

    @require_permission("plugin.permission.view", "❌ 你没有查看权限的权限")
    async def _list_permissions(self, chat_stream, args: list[str]):
        """列出用户权限"""
        target_user_id = None

        if args:
            # 指定了用户 - 使用新的解析方法
            target_user_id = self._parse_user_mention(args[0])
            if not target_user_id:
                await self.send_text("❌ 无效的用户格式，请使用 @<用户名:QQ号> 或直接输入QQ号")
                return
        else:
            # 查看自己的权限
            target_user_id = chat_stream.user_info.user_id

        # 检查是否为Master用户
        is_master = await permission_api.is_master(chat_stream.platform, target_user_id)

        # 获取用户权限
        permissions = await permission_api.get_user_permissions(chat_stream.platform, target_user_id)

        if is_master:
            response = f"👑 用户 `{target_user_id}` 是Master用户，拥有所有权限"
        else:
            if permissions:
                perm_list = "\n".join([f"• `{perm}`" for perm in permissions])
                response = f"📋 用户 `{target_user_id}` 拥有的权限：\n{perm_list}"
            else:
                response = f"📋 用户 `{target_user_id}` 没有任何权限"

        await self.send_text(response)

    @require_permission("plugin.permission.view", "❌ 你没有查看权限的权限")
    async def _check_permission(self, chat_stream, args: list[str]):
        """检查用户权限"""
        if len(args) < 2:
            await self.send_text("❌ 用法: /permission check <@用户|QQ号> <权限节点>")
            return

        # 解析用户ID - 使用新的解析方法
        user_id = self._parse_user_mention(args[0])
        if not user_id:
            await self.send_text("❌ 无效的用户格式，请使用 @<用户名:QQ号> 或直接输入QQ号")
            return

        permission_node = args[1]

        # 检查权限
        has_permission = await permission_api.check_permission(chat_stream.platform, user_id, permission_node)
        is_master = permission_api.is_master(chat_stream.platform, user_id)

        if has_permission:
            if is_master:
                response = f"✅ 用户 `{user_id}` 拥有权限 `{permission_node}`（Master用户）"
            else:
                response = f"✅ 用户 `{user_id}` 拥有权限 `{permission_node}`"
        else:
            response = f"❌ 用户 `{user_id}` 没有权限 `{permission_node}`"

        await self.send_text(response)

    @require_permission("plugin.permission.view", "❌ 你没有查看权限的权限")
    async def _list_nodes(self, chat_stream, args: list[str]):
        """列出权限节点"""
        plugin_name = args[0] if args else None

        if plugin_name:
            # 获取指定插件的权限节点
            nodes = await permission_api.get_plugin_permission_nodes(plugin_name)
            title = f"📋 插件 {plugin_name} 的权限节点："
        else:
            # 获取所有权限节点
            nodes = await permission_api.get_all_permission_nodes()
            title = "📋 所有权限节点："

        if not nodes:
            if plugin_name:
                response = f"📋 插件 {plugin_name} 没有注册任何权限节点"
            else:
                response = "📋 系统中没有任何权限节点"
        else:
            node_list = []
            for node in nodes:
                default_text = "（默认授权）" if node["default_granted"] else "（默认拒绝）"
                node_list.append(f"• {node['node_name']} {default_text}")
                node_list.append(f"  📄 {node['description']}")
                if not plugin_name:
                    node_list.append(f"  🔌 插件: {node['plugin_name']}")
                node_list.append("")  # 空行分隔

            response = title + "\n" + "\n".join(node_list)

        await self.send_text(response)

    @require_permission("plugin.permission.view", "❌ 你没有查看权限的权限")
    async def _list_all_nodes_with_description(self, chat_stream):
        """列出所有插件的权限节点（带详细描述）"""
        # 获取所有权限节点
        all_nodes = await permission_api.get_all_permission_nodes()

        if not all_nodes:
            response = "📋 系统中没有任何权限节点"
            await self.send_text(response)
            return

        # 按插件名分组节点
        plugins_dict = {}
        for node in all_nodes:
            plugin_name = node["plugin_name"]
            if plugin_name not in plugins_dict:
                plugins_dict[plugin_name] = []
            plugins_dict[plugin_name].append(node)

        # 构建响应消息
        response_parts = ["📋 所有插件权限节点详情：\n"]

        for plugin_name in sorted(plugins_dict.keys()):
            nodes = plugins_dict[plugin_name]
            response_parts.append(f"🔌 **{plugin_name}** ({len(nodes)}个节点)：")

            for node in nodes:
                default_text = "✅默认授权" if node["default_granted"] else "❌默认拒绝"
                response_parts.append(f"  • `{node['node_name']}` - {default_text}")
                response_parts.append(f"    📄 {node['description']}")

            response_parts.append("")  # 插件间空行分隔

        # 添加统计信息
        total_nodes = len(all_nodes)
        total_plugins = len(plugins_dict)
        response_parts.append(f"📊 统计：共 {total_plugins} 个插件，{total_nodes} 个权限节点")

        response = "\n".join(response_parts)

        # 如果消息太长，分段发送
        if len(response) > 4000:  # 预留一些空间避免超出限制
            await self._send_long_message(response)
        else:
            await self.send_text(response)

    async def _send_long_message(self, message: str):
        """发送长消息，自动分段"""
        lines = message.split("\n")
        current_chunk = []
        current_length = 0

        for line in lines:
            line_length = len(line) + 1  # +1 for newline

            # 如果添加这一行会超出限制，先发送当前块
            if current_length + line_length > 3500 and current_chunk:
                await self.send_text("\n".join(current_chunk))
                current_chunk = []
                current_length = 0

            current_chunk.append(line)
            current_length += line_length

        # 发送最后一块
        if current_chunk:
            await self.send_text("\n".join(current_chunk))


@register_plugin
class PermissionManagerPlugin(BasePlugin):
    plugin_name: str = "permission_manager_plugin"
    enable_plugin: bool = False
    dependencies: ClassVar[list[str]] = []
    python_dependencies: ClassVar[list[str]] = []
    config_file_name: str = "config.toml"
    config_schema: ClassVar[dict] = {
        "plugin": {
            "enabled": ConfigField(bool, default=True, description="是否启用插件"),
            "config_version": ConfigField(type=str, default="1.1.0", description="配置文件版本"),
        }
    }

    def get_plugin_components(self) -> list[tuple[PlusCommandInfo, type[PlusCommand]]]:
        """返回插件的PlusCommand组件"""
        return [(PermissionCommand.get_plus_command_info(), PermissionCommand)]

    permission_nodes: ClassVar[list[PermissionNodeField]] = [
        PermissionNodeField(
            node_name="manage",
            description="权限管理：可以授权和撤销其他用户的权限",
        ),
        PermissionNodeField(
            node_name="view",
            description="权限查看：可以查看权限节点和用户权限信息",
        ),
    ]