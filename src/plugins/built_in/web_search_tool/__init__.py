from src.plugin_system.base.component_types import PythonDependency
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
    # Python包依赖列表
    python_dependencies = [  # noqa: RUF012
        PythonDependency(package_name="asyncddgs", description="异步DuckDuckGo搜索库", optional=False),
        PythonDependency(
            package_name="exa_py",
            description="Exa搜索API客户端库",
            optional=True,  # 如果没有API密钥，这个是可选的
        ),
        PythonDependency(
            package_name="tavily",
            install_name="tavily-python",  # 安装时使用这个名称
            description="Tavily搜索API客户端库",
            optional=True,  # 如果没有API密钥，这个是可选的
        ),
        PythonDependency(
            package_name="httpx",
            version=">=0.20.0",
            install_name="httpx[socks]",  # 安装时使用这个名称（包含可选依赖）
            description="支持SOCKS代理的HTTP客户端库",
            optional=False,
        ),
    ]
)
