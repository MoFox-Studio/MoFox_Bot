"""
安全模块

提供消息安全检测和过滤的核心接口。
插件可以通过实现这些接口来扩展安全功能。
"""

from .interfaces import SecurityChecker, SecurityCheckResult
from .manager import SecurityManager, get_security_manager

__all__ = [
    "SecurityCheckResult",
    "SecurityChecker",
    "SecurityManager",
    "get_security_manager",
]
