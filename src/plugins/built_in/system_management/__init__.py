from src.plugin_system.base.plugin_metadata import PluginMetadata

__plugin_meta__ = PluginMetadata(
    name="SystemManagement",
    description="一个统一的系统管理插件，集成了权限、插件和定时任务的管理功能。",
    usage="/system <permission|plugin|schedule> [...]",
    version="1.0.0",
    author="MoFox-Studio",
    license="GPL-v3.0-or-later",
    repository_url="https://github.com/MoFox-Studio",
    keywords=["plugins", "permission", "management", "built-in"],
    extra={
        "is_built_in": True,
        "plugin_type": "permission",
    },
)
