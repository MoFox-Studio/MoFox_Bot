from src.plugin_system.base.plugin_metadata import PluginMetadata

__plugin_meta__ = PluginMetadata(
    name="Web Search Tool",
    description="A tool for searching the web.",
    usage="This plugin provides a `web_search` tool.",
    version="1.0.0",
    author="MoFox-Studio",
    license="GPL-v3.0-or-later",
    repository_url="https://github.com/MoFox-Studio",
    keywords=["web", "search", "tool"],
    categories=["Tools"],
    extra={
        "is_built_in": True,
    },
)
