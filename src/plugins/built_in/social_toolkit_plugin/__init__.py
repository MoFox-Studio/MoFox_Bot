from src.plugin_system.base.plugin_metadata import PluginMetadata

# 定义插件元数据
__plugin_meta__ = PluginMetadata(
    name="MoFox-Bot工具箱",
    description="一个集合多种实用功能的插件，旨在提升聊天体验和效率。",
    usage="该插件提供多种命令，详情请查阅文档。",
    version="1.0.0",
    author="MoFox-Studio",
    license="GPL-v3.0-or-later",
    repository_url="https://github.com/MoFox-Studio",
    keywords=["emoji", "reaction", "like", "表情", "回应", "点赞"],
    categories=["Chat", "Integration"],
    extra={"is_built_in": "true", "plugin_type": "functional"},
    dependencies=[],
    python_dependencies=["httpx", "Pillow"],
)
