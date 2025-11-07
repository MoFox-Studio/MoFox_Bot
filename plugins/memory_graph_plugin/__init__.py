"""
记忆系统插件

集成记忆管理功能到 Bot 系统中
"""

from src.plugin_system.base.plugin_metadata import PluginMetadata

__plugin_meta__ = PluginMetadata(
    name="记忆图系统 (Memory Graph)",
    description="基于图的记忆管理系统，支持记忆创建、关联和检索",
    usage="LLM 可以通过工具调用创建和管理记忆，系统自动在回复时检索相关记忆",
    version="0.1.0",
    author="MoFox-Studio",
    license="GPL-v3.0",
    repository_url="https://github.com/MoFox-Studio",
    keywords=["记忆", "知识图谱", "RAG", "长期记忆"],
    categories=["AI", "Knowledge Management"],
    extra={"is_built_in": False, "plugin_type": "memory"},
)
