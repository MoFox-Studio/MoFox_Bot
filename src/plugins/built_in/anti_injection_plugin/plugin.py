"""
反注入插件主类

定义插件配置、组件和权限
"""

from src.plugin_system import (
    BasePlugin,
    ConfigField,
    register_plugin,
)


@register_plugin
class AntiInjectionPlugin(BasePlugin):
    """反注入插件 - 提供提示词注入检测和防护"""

    # --- 插件基础信息 ---
    plugin_name = "anti_injection_plugin"
    enable_plugin = True
    dependencies = []
    python_dependencies = []
    config_file_name = "config.toml"

    # --- 配置文件定义 ---
    config_section_descriptions = {
        "detection": "检测配置",
        "processing": "处理配置",
        "performance": "性能优化配置",
    }

    config_schema = {
        "detection": {
            "enabled": ConfigField(
                type=bool,
                default=True,
                description="是否启用反注入检测",
            ),
            "enabled_rules": ConfigField(
                type=bool,
                default=True,
                description="是否启用规则检测（基于正则表达式）",
            ),
            "enabled_llm": ConfigField(
                type=bool,
                default=False,
                description="是否启用LLM检测（需要额外的API调用成本）",
            ),
            "max_message_length": ConfigField(
                type=int,
                default=4096,
                description="最大检测消息长度（超过此长度的消息将被截断）",
            ),
            "llm_detection_threshold": ConfigField(
                type=float,
                default=0.7,
                description="LLM检测阈值 (0-1)，置信度超过此值才认为是注入攻击",
            ),
            "whitelist": ConfigField(
                type=list,
                default=[],
                description="白名单用户列表（这些用户的消息不会被检测）",
                example='["user123", "admin456"]',
            ),
        },
        "processing": {
            "process_mode": ConfigField(
                type=str,
                default="lenient",
                description="处理模式: strict-严格拦截 / lenient-宽松加盾 / monitor-仅监控 / counter_attack-反击",
                choices=["strict", "lenient", "monitor", "counter_attack"],
            ),
            "shield_prefix": ConfigField(
                type=str,
                default="[SAFETY_FILTERED]",
                description="加盾时的前缀标记",
            ),
            "shield_suffix": ConfigField(
                type=str,
                default="[/SAFETY_FILTERED]",
                description="加盾时的后缀标记",
            ),
            "counter_attack_use_llm": ConfigField(
                type=bool,
                default=True,
                description="反击模式是否使用LLM生成响应（更智能但消耗资源）",
            ),
            "counter_attack_humor": ConfigField(
                type=bool,
                default=True,
                description="反击响应是否使用幽默风格",
            ),
            "log_blocked_messages": ConfigField(
                type=bool,
                default=True,
                description="是否记录被拦截的消息到日志",
            ),
            "delete_blocked_from_db": ConfigField(
                type=bool,
                default=False,
                description="是否从数据库中删除被拦截的消息",
            ),
        },
        "performance": {
            "cache_enabled": ConfigField(
                type=bool,
                default=True,
                description="是否启用结果缓存（相同消息直接返回缓存结果）",
            ),
            "cache_ttl": ConfigField(
                type=int,
                default=3600,
                description="缓存有效期（秒）",
            ),
            "stats_enabled": ConfigField(
                type=bool,
                default=True,
                description="是否启用检测统计",
            ),
        },
    }

    def get_plugin_components(self):
        """注册插件的所有功能组件"""
        components = []

        # 导入Prompt组件
        from .prompts import AntiInjectionPrompt

        # 总是注册安全提示词（核心功能）
        components.append(
            (AntiInjectionPrompt.get_prompt_info(), AntiInjectionPrompt)
        )

        # 根据配置决定是否注册调试用的状态提示词
        if self.get_config("performance.stats_enabled", False):
            from .prompts import SecurityStatusPrompt

            components.append(
                (SecurityStatusPrompt.get_prompt_info(), SecurityStatusPrompt)
            )

        return components

    async def on_plugin_loaded(self):
        """插件加载完成后的初始化"""
        from src.chat.security import get_security_manager
        from src.common.logger import get_logger

        from .checker import AntiInjectionChecker

        logger = get_logger("anti_injection_plugin")

        # 注册安全检查器到核心系统
        security_manager = get_security_manager()
        checker = AntiInjectionChecker(config=self.config)
        security_manager.register_checker(checker)

        logger.info("反注入检查器已注册到安全管理器")
