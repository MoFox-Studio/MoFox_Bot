"""
安全检测接口定义
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum


class SecurityLevel(Enum):
    """安全级别"""

    SAFE = "safe"  # 安全
    LOW_RISK = "low_risk"  # 低风险
    MEDIUM_RISK = "medium_risk"  # 中等风险
    HIGH_RISK = "high_risk"  # 高风险
    CRITICAL = "critical"  # 严重风险


class SecurityAction(Enum):
    """安全处理动作"""

    ALLOW = "allow"  # 允许通过
    MONITOR = "monitor"  # 监控但允许
    SHIELD = "shield"  # 加盾处理
    BLOCK = "block"  # 阻止
    COUNTER = "counter"  # 反击


@dataclass
class SecurityCheckResult:
    """安全检测结果"""

    is_safe: bool = True  # 是否安全
    level: SecurityLevel = SecurityLevel.SAFE  # 风险级别
    confidence: float = 0.0  # 置信度 (0.0-1.0)
    action: SecurityAction = SecurityAction.ALLOW  # 建议动作
    reason: str = ""  # 检测原因
    details: dict = field(default_factory=dict)  # 详细信息
    matched_patterns: list[str] = field(default_factory=list)  # 匹配的模式
    checker_name: str = ""  # 检测器名称
    processing_time: float = 0.0  # 处理时间(秒)

    def __post_init__(self):
        """结果后处理"""
        # 根据风险级别自动设置 is_safe
        if self.level in [SecurityLevel.HIGH_RISK, SecurityLevel.CRITICAL]:
            self.is_safe = False


class SecurityChecker(ABC):
    """安全检测器基类"""

    def __init__(self, name: str, priority: int = 50):
        """初始化检测器

        Args:
            name: 检测器名称
            priority: 优先级 (0-100，数值越大优先级越高)
        """
        self.name = name
        self.priority = priority
        self.enabled = True

    @abstractmethod
    async def check(self, message: str, context: dict | None = None) -> SecurityCheckResult:
        """执行安全检测

        Args:
            message: 待检测的消息内容
            context: 上下文信息（可选），包含用户信息、聊天信息等

        Returns:
            SecurityCheckResult: 检测结果
        """
        pass

    def enable(self):
        """启用检测器"""
        self.enabled = True

    def disable(self):
        """禁用检测器"""
        self.enabled = False

    async def pre_check(self, message: str, context: dict | None = None) -> bool:
        """预检查，快速判断是否需要执行完整检查

        Args:
            message: 待检测的消息内容
            context: 上下文信息

        Returns:
            bool: True表示需要完整检查，False表示可以跳过
        """
        return True  # 默认总是执行完整检查
