import asyncio
from typing import ClassVar

from src.plugin_system import (
    BasePlugin,
    ComponentInfo,
    ComponentType,
    ConfigField,
    component_manage_api,
    plugin_manage_api,
    register_plugin,
)
from src.plugin_system.apis.permission_api import permission_api
from src.plugin_system.base.command_args import CommandArgs
from src.plugin_system.base.component_types import ChatType, PlusCommandInfo
from src.plugin_system.base.plus_command import PlusCommand
from src.plugin_system.utils.permission_decorators import require_permission


class ManagementCommand(PlusCommand):
    """插件管理命令 - 使用PlusCommand系统"""

    command_name = "pm"
    command_description = "插件管理命令，支持插件和组件的管理操作"
    command_aliases: ClassVar[list[str]] = ["pluginmanage", "插件管理"]
    priority = 10
    chat_type_allow = ChatType.ALL
    intercept_message = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    @require_permission("plugin.management.admin", "❌ 你没有插件管理的权限")
    async def execute(self, args: CommandArgs) -> tuple[bool, str, bool]:
        """执行插件管理命令"""
        if args.is_empty:
            await self._show_help("all")
            return True, "显示帮助信息", True

        subcommand = args.get_first.lower()
        remaining_args = args.get_args()[1:]  # 获取除第一个参数外的所有参数

        if subcommand in ["plugin", "插件"]:
            return await self._handle_plugin_commands(remaining_args)
        elif subcommand in ["component", "组件", "comp"]:
            return await self._handle_component_commands(remaining_args)
        elif subcommand in ["help", "帮助"]:
            await self._show_help("all")
            return True, "显示帮助信息", True
        else:
            await self.send_text(f"❌ 未知的子命令: {subcommand}\n使用 /pm help 查看帮助")
            return True, "未知子命令", True

    async def _handle_plugin_commands(self, args: list[str]) -> tuple[bool, str, bool]:
        """处理插件相关命令"""
        if not args:
            await self._show_help("plugin")
            return True, "显示插件帮助", True

        action = args[0].lower()

        if action in ["help", "帮助"]:
            await self._show_help("plugin")
        elif action in ["list", "列表"]:
            await self._list_registered_plugins()
        elif action in ["list_enabled", "已启用"]:
            await self._list_loaded_plugins()
        elif action in ["rescan", "重扫"]:
            await self._rescan_plugin_dirs()
        elif action in ["load", "加载"] and len(args) > 1:
            await self._load_plugin(args[1])
        elif action in ["unload", "卸载"] and len(args) > 1:
            await self._unload_plugin(args[1])
        elif action in ["reload", "重载"] and len(args) > 1:
            await self._reload_plugin(args[1])
        elif action in ["force_reload", "强制重载"] and len(args) > 1:
            await self._force_reload_plugin(args[1])
        elif action in ["add_dir", "添加目录"] and len(args) > 1:
            await self._add_dir(args[1])
        else:
            await self.send_text("❌ 插件管理命令不合法\n使用 /pm plugin help 查看帮助")
            return False, "命令不合法", True

        return True, "插件命令执行完成", True

    async def _handle_component_commands(self, args: list[str]) -> tuple[bool, str, bool]:
        """处理组件相关命令"""
        if not args:
            await self._show_help("component")
            return True, "显示组件帮助", True

        action = args[0].lower()

        if action in ["help", "帮助"]:
            await self._show_help("component")
        elif action in ["list", "列表"]:
            if len(args) == 1:
                await self._list_all_registered_components()
            elif len(args) == 2:
                if args[1] in ["enabled", "启用"]:
                    await self._list_enabled_components()
                elif args[1] in ["disabled", "禁用"]:
                    await self._list_disabled_components()
                else:
                    await self.send_text("❌ 组件列表命令不合法")
                    return False, "命令不合法", True
            elif len(args) == 3:
                if args[1] in ["enabled", "启用"]:
                    await self._list_enabled_components(target_type=args[2])
                elif args[1] in ["disabled", "禁用"]:
                    await self._list_disabled_components(target_type=args[2])
                elif args[1] in ["type", "类型"]:
                    await self._list_registered_components_by_type(args[2])
                else:
                    await self.send_text("❌ 组件列表命令不合法")
                    return False, "命令不合法", True
        elif action in ["enable", "启用"] and len(args) >= 4:
            scope = args[1].lower()
            component_name = args[2]
            component_type = args[3]
            if scope in ["global", "全局"]:
                await self._globally_enable_component(component_name, component_type)
            elif scope in ["local", "本地"]:
                await self._locally_enable_component(component_name, component_type)
            else:
                await self.send_text("❌ 组件启用命令不合法，范围应为 global 或 local")
                return False, "命令不合法", True
        elif action in ["disable", "禁用"] and len(args) >= 4:
            scope = args[1].lower()
            component_name = args[2]
            component_type = args[3]
            if scope in ["global", "全局"]:
                await self._globally_disable_component(component_name, component_type)
            elif scope in ["local", "本地"]:
                await self._locally_disable_component(component_name, component_type)
            else:
                await self.send_text("❌ 组件禁用命令不合法，范围应为 global 或 local")
                return False, "命令不合法", True
        else:
            await self.send_text("❌ 组件管理命令不合法\n使用 /pm component help 查看帮助")
            return False, "命令不合法", True

        return True, "组件命令执行完成", True

    async def _show_help(self, target: str):
        """显示帮助信息"""
        help_msg = ""
        if target == "all":
            help_msg = """📋 插件管理命令帮助

🔧 主要功能：
• `/pm help` - 显示此帮助
• `/pm plugin` - 插件管理命令
• `/pm component` - 组件管理命令

📝 使用示例：
• `/pm plugin help` - 查看插件管理帮助
• `/pm component help` - 查看组件管理帮助

🔄 别名：可以使用 `/pluginmanage` 或 `/插件管理` 代替 `/pm`"""
        elif target == "plugin":
            help_msg = """🔌 插件管理命令帮助

📋 基本操作：
• `/pm plugin help` - 显示插件管理帮助
• `/pm plugin list` - 列出所有注册的插件
• `/pm plugin list_enabled` - 列出所有加载（启用）的插件
• `/pm plugin rescan` - 重新扫描所有插件目录

⚙️ 插件控制：
• `/pm plugin load <插件名>` - 加载指定插件
• `/pm plugin unload <插件名>` - 卸载指定插件
• `/pm plugin reload <插件名>` - 重新加载指定插件
• `/pm plugin force_reload <插件名>` - 强制重载指定插件（深度清理）
• `/pm plugin add_dir <目录路径>` - 添加插件目录

�📝 示例：
• `/pm plugin load echo_example`
• `/pm plugin force_reload permission_manager_plugin`"""
        elif target == "component":
            help_msg = """🧩 组件管理命令帮助

📋 基本查看：
• `/pm component help` - 显示组件管理帮助
• `/pm component list` - 列出所有注册的组件
• `/pm component list enabled [类型]` - 列出启用的组件
• `/pm component list disabled [类型]` - 列出禁用的组件
• `/pm component list type <组件类型>` - 列出指定类型的组件

⚙️ 组件控制：
• `/pm component enable global <组件名> <类型>` - 全局启用组件
• `/pm component enable local <组件名> <类型>` - 本聊天启用组件
• `/pm component disable global <组件名> <类型>` - 全局禁用组件
• `/pm component disable local <组件名> <类型>` - 本聊天禁用组件

📝 组件类型：
• `action` - 动作组件
• `command` - 命令组件
• `event_handler` - 事件处理组件
• `plus_command` - 增强命令组件

💡 示例：
• `/pm component list type plus_command`
• `/pm component enable global echo_command command`"""

        await self.send_text(help_msg)

    async def _list_loaded_plugins(self):
        """列出已加载的插件"""
        plugins = plugin_manage_api.list_loaded_plugins()
        await self.send_text(f"📦 已加载的插件: {', '.join(plugins) if plugins else '无'}")

    async def _list_registered_plugins(self):
        """列出已注册的插件"""
        plugins = plugin_manage_api.list_registered_plugins()
        await self.send_text(f"📋 已注册的插件: {', '.join(plugins) if plugins else '无'}")

    async def _rescan_plugin_dirs(self):
        """重新扫描插件目录"""
        plugin_manage_api.rescan_plugin_directory()
        await self.send_text("🔄 插件目录重新扫描已启动")

    async def _load_plugin(self, plugin_name: str):
        """加载指定插件"""
        success, count = plugin_manage_api.load_plugin(plugin_name)
        if success:
            await self.send_text(f"✅ 插件加载成功: `{plugin_name}`")
        else:
            if count == 0:
                await self.send_text(f"⚠️ 插件 `{plugin_name}` 为禁用状态")
            else:
                await self.send_text(f"❌ 插件加载失败: `{plugin_name}`")

    async def _unload_plugin(self, plugin_name: str):
        """卸载指定插件"""
        success = await plugin_manage_api.remove_plugin(plugin_name)
        if success:
            await self.send_text(f"✅ 插件卸载成功: `{plugin_name}`")
        else:
            await self.send_text(f"❌ 插件卸载失败: `{plugin_name}`")

    async def _reload_plugin(self, plugin_name: str):
        """重新加载指定插件"""
        success = await plugin_manage_api.reload_plugin(plugin_name)
        if success:
            await self.send_text(f"✅ 插件重新加载成功: `{plugin_name}`")
        else:
            await self.send_text(f"❌ 插件重新加载失败: `{plugin_name}`")

    async def _force_reload_plugin(self, plugin_name: str):
        """强制重载指定插件（深度清理）"""
        await self.send_text(f"🔄 开始强制重载插件: `{plugin_name}`...")

        try:
            success = plugin_manage_api.force_reload_plugin(plugin_name)
            if success:
                await self.send_text(f"✅ 插件强制重载成功: `{plugin_name}`")
            else:
                await self.send_text(f"❌ 插件强制重载失败: `{plugin_name}`")
        except Exception as e:
            await self.send_text(f"❌ 强制重载过程中发生错误: {e!s}")

    async def _add_dir(self, dir_path: str):
        """添加插件目录"""
        await self.send_text(f"📁 正在添加插件目录: `{dir_path}`")
        success = plugin_manage_api.add_plugin_directory(dir_path)
        await asyncio.sleep(0.5)  # 防止乱序发送
        if success:
            await self.send_text(f"✅ 插件目录添加成功: `{dir_path}`")
        else:
            await self.send_text(f"❌ 插件目录添加失败: `{dir_path}`")

    @staticmethod
    def _fetch_all_registered_components() -> list[ComponentInfo]:
        all_plugin_info = component_manage_api.get_all_plugin_info()
        if not all_plugin_info:

            return []

        components_info: list[ComponentInfo] = []
        for plugin_info in all_plugin_info.values():
            components_info.extend(plugin_info.components)
        return components_info

    def _fetch_locally_disabled_components(self) -> list[str]:
        """获取本地禁用的组件列表"""
        stream_id = self.message.chat_stream.stream_id
        locally_disabled_components_actions = component_manage_api.get_locally_disabled_components(
            stream_id, ComponentType.ACTION
        )
        locally_disabled_components_commands = component_manage_api.get_locally_disabled_components(
            stream_id, ComponentType.COMMAND
        )
        locally_disabled_components_plus_commands = component_manage_api.get_locally_disabled_components(
            stream_id, ComponentType.PLUS_COMMAND
        )
        locally_disabled_components_event_handlers = component_manage_api.get_locally_disabled_components(
            stream_id, ComponentType.EVENT_HANDLER
        )
        return (
            locally_disabled_components_actions
            + locally_disabled_components_commands
            + locally_disabled_components_plus_commands
            + locally_disabled_components_event_handlers
        )

    async def _list_all_registered_components(self):
        """列出所有已注册的组件"""
        components_info = self._fetch_all_registered_components()
        if not components_info:
            await self.send_text("📋 没有注册的组件")
            return

        all_components_str = ", ".join(
            f"`{component.name}` ({component.component_type})" for component in components_info
        )
        await self.send_text(f"📋 已注册的组件:\n{all_components_str}")

    async def _list_enabled_components(self, target_type: str = "global"):
        """列出启用的组件"""
        components_info = self._fetch_all_registered_components()
        if not components_info:
            await self.send_text("📋 没有注册的组件")
            return

        if target_type == "global":
            enabled_components = [component for component in components_info if component.enabled]
            if not enabled_components:
                await self.send_text("📋 没有满足条件的已启用全局组件")
                return
            enabled_components_str = ", ".join(
                f"`{component.name}` ({component.component_type})" for component in enabled_components
            )
            await self.send_text(f"✅ 满足条件的已启用全局组件:\n{enabled_components_str}")
        elif target_type == "local":
            locally_disabled_components = self._fetch_locally_disabled_components()
            enabled_components = [
                component
                for component in components_info
                if (component.name not in locally_disabled_components and component.enabled)
            ]
            if not enabled_components:
                await self.send_text("📋 本聊天没有满足条件的已启用组件")
                return
            enabled_components_str = ", ".join(
                f"`{component.name}` ({component.component_type})" for component in enabled_components
            )
            await self.send_text(f"✅ 本聊天满足条件的已启用组件:\n{enabled_components_str}")

    async def _list_disabled_components(self, target_type: str = "global"):
        """列出禁用的组件"""
        components_info = self._fetch_all_registered_components()
        if not components_info:
            await self.send_text("📋 没有注册的组件")
            return

        if target_type == "global":
            disabled_components = [component for component in components_info if not component.enabled]
            if not disabled_components:
                await self.send_text("📋 没有满足条件的已禁用全局组件")
                return
            disabled_components_str = ", ".join(
                f"`{component.name}` ({component.component_type})" for component in disabled_components
            )
            await self.send_text(f"❌ 满足条件的已禁用全局组件:\n{disabled_components_str}")
        elif target_type == "local":
            locally_disabled_components = self._fetch_locally_disabled_components()
            disabled_components = [
                component
                for component in components_info
                if (component.name in locally_disabled_components or not component.enabled)
            ]
            if not disabled_components:
                await self.send_text("📋 本聊天没有满足条件的已禁用组件")
                return
            disabled_components_str = ", ".join(
                f"`{component.name}` ({component.component_type})" for component in disabled_components
            )
            await self.send_text(f"❌ 本聊天满足条件的已禁用组件:\n{disabled_components_str}")

    async def _list_registered_components_by_type(self, target_type: str):
        """按类型列出已注册的组件"""
        type_mapping = {
            "action": ComponentType.ACTION,
            "command": ComponentType.COMMAND,
            "event_handler": ComponentType.EVENT_HANDLER,
            "plus_command": ComponentType.PLUS_COMMAND,
        }

        component_type = type_mapping.get(target_type.lower())
        if not component_type:
            await self.send_text(
                f"❌ 未知组件类型: `{target_type}`\n支持的类型: action, command, event_handler, plus_command"
            )
            return

        components_info = component_manage_api.get_components_info_by_type(component_type)
        if not components_info:
            await self.send_text(f"📋 没有注册的 `{target_type}` 组件")
            return

        components_str = ", ".join(
            f"`{name}` ({component.component_type})" for name, component in components_info.items()
        )
        await self.send_text(f"📋 注册的 `{target_type}` 组件:\n{components_str}")

    async def _globally_enable_component(self, component_name: str, component_type: str):
        """全局启用组件"""
        type_mapping = {
            "action": ComponentType.ACTION,
            "command": ComponentType.COMMAND,
            "event_handler": ComponentType.EVENT_HANDLER,
            "plus_command": ComponentType.PLUS_COMMAND,
        }

        target_component_type = type_mapping.get(component_type.lower())
        if not target_component_type:
            await self.send_text(f"❌ 未知组件类型: `{component_type}`")
            return

        if component_manage_api.globally_enable_component(component_name, target_component_type):
            await self.send_text(f"✅ 全局启用组件成功: `{component_name}`")
        else:
            await self.send_text(f"❌ 全局启用组件失败: `{component_name}`")

    async def _globally_disable_component(self, component_name: str, component_type: str):
        """全局禁用组件"""
        type_mapping = {
            "action": ComponentType.ACTION,
            "command": ComponentType.COMMAND,
            "event_handler": ComponentType.EVENT_HANDLER,
            "plus_command": ComponentType.PLUS_COMMAND,
        }

        target_component_type = type_mapping.get(component_type.lower())
        if not target_component_type:
            await self.send_text(f"❌ 未知组件类型: `{component_type}`")
            return

        success = await component_manage_api.globally_disable_component(component_name, target_component_type)
        if success:
            await self.send_text(f"✅ 全局禁用组件成功: `{component_name}`")
        else:
            await self.send_text(f"❌ 全局禁用组件失败: `{component_name}`")

    async def _locally_enable_component(self, component_name: str, component_type: str):
        """本地启用组件"""
        type_mapping = {
            "action": ComponentType.ACTION,
            "command": ComponentType.COMMAND,
            "event_handler": ComponentType.EVENT_HANDLER,
            "plus_command": ComponentType.PLUS_COMMAND,
        }

        target_component_type = type_mapping.get(component_type.lower())
        if not target_component_type:
            await self.send_text(f"❌ 未知组件类型: `{component_type}`")
            return

        stream_id = self.message.chat_stream.stream_id
        if component_manage_api.locally_enable_component(component_name, target_component_type, stream_id):
            await self.send_text(f"✅ 本地启用组件成功: `{component_name}`")
        else:
            await self.send_text(f"❌ 本地启用组件失败: `{component_name}`")

    async def _locally_disable_component(self, component_name: str, component_type: str):
        """本地禁用组件"""
        type_mapping = {
            "action": ComponentType.ACTION,
            "command": ComponentType.COMMAND,
            "event_handler": ComponentType.EVENT_HANDLER,
            "plus_command": ComponentType.PLUS_COMMAND,
        }

        target_component_type = type_mapping.get(component_type.lower())
        if not target_component_type:
            await self.send_text(f"❌ 未知组件类型: `{component_type}`")
            return

        stream_id = self.message.chat_stream.stream_id
        if component_manage_api.locally_disable_component(component_name, target_component_type, stream_id):
            await self.send_text(f"✅ 本地禁用组件成功: `{component_name}`")
        else:
            await self.send_text(f"❌ 本地禁用组件失败: `{component_name}`")


@register_plugin
class PluginManagementPlugin(BasePlugin):
    plugin_name: str = "plugin_management_plugin"
    enable_plugin: bool = True
    dependencies: ClassVar[list[str]] = []
    python_dependencies: ClassVar[list[str]] = []
    config_file_name: str = "config.toml"
    config_schema: ClassVar[dict] = {
        "plugin": {
            "enabled": ConfigField(bool, default=False, description="是否启用插件"),
            "config_version": ConfigField(type=str, default="1.1.0", description="配置文件版本"),
        },
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 注册权限节点

    async def on_plugin_loaded(self):
        await permission_api.register_permission_node(
            "plugin.management.admin",
            "插件管理：可以管理插件和组件的加载、卸载、启用、禁用等操作",
            "plugin_management",
            False,
        )

    def get_plugin_components(self) -> list[tuple[PlusCommandInfo, type[PlusCommand]]]:
        """返回插件的PlusCommand组件"""
        components = []
        if self.get_config("plugin.enabled", True):
            components.append((ManagementCommand.get_plus_command_info(), ManagementCommand))
        return components
