"""
AffinityFlow Chatter 核心模块

包含兴趣度计算器和核心对话处理逻辑
"""

from .affinity_chatter import AffinityChatter
from .affinity_interest_calculator import AffinityInterestCalculator

__all__ = ["AffinityChatter", "AffinityInterestCalculator"]
