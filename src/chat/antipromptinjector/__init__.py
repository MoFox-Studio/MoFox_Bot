"""
MoFox-Bot 反注入系统模块

本模块提供了一个完整的LLM反注入检测和防护系统，用于防止恶意的提示词注入攻击。

主要功能：
1. 基于规则的快速检测
2. 黑白名单机制
3. LLM二次分析
4. 消息处理模式（严格模式/宽松模式/反击模式）

作者: FOX YaNuo
"""

from .anti_injector import AntiPromptInjector, get_anti_injector, initialize_anti_injector
from .core import MessageShield, PromptInjectionDetector
from .decision import CounterAttackGenerator, ProcessingDecisionMaker
from .management import AntiInjectionStatistics, UserBanManager
from .processors.message_processor import MessageProcessor
from .types import DetectionResult, ProcessResult

__all__ = [
    "AntiInjectionStatistics",
    "AntiPromptInjector",
    "CounterAttackGenerator",
    "DetectionResult",
    "MessageProcessor",
    "MessageShield",
    "ProcessResult",
    "ProcessingDecisionMaker",
    "PromptInjectionDetector",
    "UserBanManager",
    "get_anti_injector",
    "initialize_anti_injector",
]


__author__ = "FOX YaNuo"
