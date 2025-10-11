from src.plugin_system.base.plugin_metadata import PluginMetadata

__plugin_meta__ = PluginMetadata(
    name="插件和组件管理 (Plugin and Component Management)",
    description="通过系统API管理插件和组件的生命周期，包括加载、卸载、启用和禁用等操作。",
    usage="该插件提供 `plugin_management` command。",
    version="1.0.0",
    author="MoFox-Bot团队",
    license="GPL-v3.0-or-later",
    repository_url="https://github.com/MaiM-with-u/maibot",
    keywords=["plugins", "components", "management", "built-in"],
    categories=["Core System", "Plugin Management"],
    extra={
        "is_built_in": True,
        "plugin_type": "plugin_management",
    },
)
