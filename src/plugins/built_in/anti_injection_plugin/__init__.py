"""
反注入插件

提供提示词注入检测和防护功能。支持规则检测、LLM智能分析、消息加盾等。
"""

from src.plugin_system.base.plugin_metadata import PluginMetadata

# 定义插件元数据（使用标准名称）
__plugin_meta__ = PluginMetadata(
    name="反注入插件",
    description="提供提示词注入检测和防护功能。支持规则检测、LLM智能分析、反击响应、消息拦截等多种安全策略。",
    usage="""
如何使用反注入插件：
1. 在配置文件中启用插件并选择处理模式
2. 配置检测规则（regex patterns）或启用LLM检测
3. 选择处理模式：
   - strict: 严格模式，拦截中风险及以上
   - lenient: 宽松模式，加盾中风险，拦截高风险
   - monitor: 监控模式，仅记录不拦截
   - counter_attack: 反击模式，生成反击响应
4. 可配置白名单用户、缓存策略等
    """,
    author="MoFox Studio",
    version="2.0.0",
    license="MIT",
    keywords=["安全", "注入检测", "提示词保护"],
    categories=["安全", "核心功能"],
)

# 导入插件主类
from .plugin import AntiInjectionPlugin

__all__ = ["AntiInjectionPlugin", "__plugin_meta__"]
