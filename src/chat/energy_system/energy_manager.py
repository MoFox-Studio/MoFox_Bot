"""
重构后的 focus_energy 管理系统
提供稳定、高效的聊天流能量计算和管理功能
"""

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TypedDict

from src.common.logger import get_logger
from src.config.config import global_config

logger = get_logger("energy_system")


class EnergyLevel(Enum):
    """能量等级"""

    VERY_LOW = 0.1  # 非常低
    LOW = 0.3  # 低
    NORMAL = 0.5  # 正常
    HIGH = 0.7  # 高
    VERY_HIGH = 0.9  # 非常高


@dataclass
class EnergyComponent:
    """能量组件"""

    name: str
    value: float
    weight: float = 1.0
    decay_rate: float = 0.05  # 衰减率
    last_updated: float = field(default_factory=time.time)

    def get_current_value(self) -> float:
        """获取当前值（考虑时间衰减）"""
        age = time.time() - self.last_updated
        decay_factor = max(0.1, 1.0 - (age * self.decay_rate / (24 * 3600)))  # 按天衰减
        return self.value * decay_factor

    def update_value(self, new_value: float) -> None:
        """更新值"""
        self.value = max(0.0, min(1.0, new_value))
        self.last_updated = time.time()


class EnergyContext(TypedDict):
    """能量计算上下文"""

    stream_id: str
    messages: list[Any]
    user_id: str | None


class EnergyResult(TypedDict):
    """能量计算结果"""

    energy: float
    level: EnergyLevel
    distribution_interval: float
    component_scores: dict[str, float]
    cached: bool


class EnergyCalculator(ABC):
    """能量计算器抽象基类"""

    @abstractmethod
    def calculate(self, context: dict[str, Any]) -> float:
        """计算能量值"""
        pass

    @abstractmethod
    def get_weight(self) -> float:
        """获取权重"""
        pass


class InterestEnergyCalculator(EnergyCalculator):
    """兴趣度能量计算器"""

    def calculate(self, context: dict[str, Any]) -> float:
        """基于消息兴趣度计算能量"""
        messages = context.get("messages", [])
        if not messages:
            return 0.3

        # 计算平均兴趣度
        total_interest = 0.0
        valid_messages = 0

        for msg in messages:
            interest_value = getattr(msg, "interest_value", None)
            if isinstance(interest_value, int | float):
                if 0.0 <= interest_value <= 1.0:
                    total_interest += interest_value
                    valid_messages += 1

        if valid_messages > 0:
            avg_interest = total_interest / valid_messages
            logger.debug(f"平均消息兴趣度: {avg_interest:.3f} (基于 {valid_messages} 条消息)")
            return avg_interest
        else:
            return 0.3

    def get_weight(self) -> float:
        return 0.5


class ActivityEnergyCalculator(EnergyCalculator):
    """活跃度能量计算器"""

    def __init__(self):
        self.action_weights = {"reply": 0.4, "react": 0.3, "mention": 0.2, "other": 0.1}

    def calculate(self, context: dict[str, Any]) -> float:
        """基于活跃度计算能量"""
        messages = context.get("messages", [])
        if not messages:
            return 0.2

        total_score = 0.0
        max_possible_score = len(messages) * 0.4  # 最高可能分数

        for msg in messages:
            actions = getattr(msg, "actions", [])
            if isinstance(actions, list) and actions:
                for action in actions:
                    weight = self.action_weights.get(action, self.action_weights["other"])
                    total_score += weight

        if max_possible_score > 0:
            activity_score = min(1.0, total_score / max_possible_score)
            logger.debug(f"活跃度分数: {activity_score:.3f}")
            return activity_score
        else:
            return 0.2

    def get_weight(self) -> float:
        return 0.3


class RecencyEnergyCalculator(EnergyCalculator):
    """最近性能量计算器"""

    def calculate(self, context: dict[str, Any]) -> float:
        """基于最近性计算能量"""
        messages = context.get("messages", [])
        if not messages:
            return 0.1

        # 获取最新消息时间
        latest_time = 0.0
        for msg in messages:
            msg_time = getattr(msg, "time", None)
            if msg_time and msg_time > latest_time:
                latest_time = msg_time

        if latest_time == 0.0:
            return 0.1

        # 计算时间衰减
        current_time = time.time()
        age = current_time - latest_time

        # 时间衰减策略：
        # 1小时内：1.0
        # 1-6小时：0.8
        # 6-24小时：0.5
        # 1-7天：0.3
        # 7天以上：0.1
        if age < 3600:  # 1小时内
            recency_score = 1.0
        elif age < 6 * 3600:  # 6小时内
            recency_score = 0.8
        elif age < 24 * 3600:  # 24小时内
            recency_score = 0.5
        elif age < 7 * 24 * 3600:  # 7天内
            recency_score = 0.3
        else:
            recency_score = 0.1

        logger.debug(f"最近性分数: {recency_score:.3f} (年龄: {age / 3600:.1f}小时)")
        return recency_score

    def get_weight(self) -> float:
        return 0.2


class RelationshipEnergyCalculator(EnergyCalculator):
    """关系能量计算器 - 基于聊天流兴趣度"""

    async def calculate(self, context: dict[str, Any]) -> float:
        """基于聊天流兴趣度计算能量"""
        stream_id = context.get("stream_id")
        if not stream_id:
            return 0.3

        # 从数据库获取聊天流兴趣分数
        try:
            from sqlalchemy import select

            from src.common.database.sqlalchemy_database_api import get_db_session
            from src.common.database.sqlalchemy_models import ChatStreams

            async with get_db_session() as session:
                stmt = select(ChatStreams).where(ChatStreams.stream_id == stream_id)
                result = await session.execute(stmt)
                stream = result.scalar_one_or_none()

                if stream and stream.stream_interest_score is not None:
                    interest_score = float(stream.stream_interest_score)
                    logger.debug(f"使用聊天流兴趣度计算关系能量: {interest_score:.3f}")
                    return interest_score
                else:
                    logger.debug(f"聊天流 {stream_id} 无兴趣分数，使用默认值")
                    return 0.3

        except Exception as e:
            logger.warning(f"获取聊天流兴趣度失败，使用默认值: {e}")
            return 0.3  # 默认基础分

    def get_weight(self) -> float:
        return 0.1


class EnergyManager:
    """能量管理器 - 统一管理所有能量计算"""

    def __init__(self) -> None:
        self.calculators: list[EnergyCalculator] = [
            InterestEnergyCalculator(),
            ActivityEnergyCalculator(),
            RecencyEnergyCalculator(),
            RelationshipEnergyCalculator(),
        ]

        # 能量缓存
        self.energy_cache: dict[str, tuple[float, float]] = {}  # stream_id -> (energy, timestamp)
        self.cache_ttl: int = 60  # 1分钟缓存

        # AFC阈值配置
        self.thresholds: dict[str, float] = {"high_match": 0.8, "reply": 0.4, "non_reply": 0.2}

        # 统计信息
        self.stats: dict[str, int | float | str] = {
            "total_calculations": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "average_calculation_time": 0.0,
            "last_threshold_update": time.time(),
        }

        # 从配置加载阈值
        self._load_thresholds_from_config()

        logger.info("能量管理器初始化完成")

    def _load_thresholds_from_config(self) -> None:
        """从配置加载AFC阈值"""
        try:
            if hasattr(global_config, "affinity_flow") and global_config.affinity_flow is not None:
                self.thresholds["high_match"] = getattr(
                    global_config.affinity_flow, "high_match_interest_threshold", 0.8
                )
                self.thresholds["reply"] = getattr(global_config.affinity_flow, "reply_action_interest_threshold", 0.4)
                self.thresholds["non_reply"] = getattr(
                    global_config.affinity_flow, "non_reply_action_interest_threshold", 0.2
                )

                # 确保阈值关系合理
                self.thresholds["high_match"] = max(self.thresholds["high_match"], self.thresholds["reply"] + 0.1)
                self.thresholds["reply"] = max(self.thresholds["reply"], self.thresholds["non_reply"] + 0.1)

                self.stats["last_threshold_update"] = time.time()
                logger.info(f"加载AFC阈值: {self.thresholds}")
        except Exception as e:
            logger.warning(f"加载AFC阈值失败，使用默认值: {e}")

    async def calculate_focus_energy(self, stream_id: str, messages: list[Any], user_id: str | None = None) -> float:
        """计算聊天流的focus_energy"""
        start_time = time.time()

        # 更新统计
        self.stats["total_calculations"] += 1

        # 检查缓存
        if stream_id in self.energy_cache:
            cached_energy, cached_time = self.energy_cache[stream_id]
            if time.time() - cached_time < self.cache_ttl:
                self.stats["cache_hits"] += 1
                logger.debug(f"使用缓存能量: {stream_id} = {cached_energy:.3f}")
                return cached_energy
        else:
            self.stats["cache_misses"] += 1

        # 构建计算上下文
        context: EnergyContext = {
            "stream_id": stream_id,
            "messages": messages,
            "user_id": user_id,
        }

        # 计算各组件能量
        component_scores: dict[str, float] = {}
        total_weight = 0.0

        for calculator in self.calculators:
            try:
                # 支持同步和异步计算器
                if callable(calculator.calculate):
                    import inspect

                    if inspect.iscoroutinefunction(calculator.calculate):
                        score = await calculator.calculate(context)
                    else:
                        score = calculator.calculate(context)
                else:
                    score = calculator.calculate(context)

                weight = calculator.get_weight()

                # 确保 score 是 float 类型
                if not isinstance(score, int | float):
                    logger.warning(
                        f"计算器 {calculator.__class__.__name__} 返回了非数值类型: {type(score)}，跳过此组件"
                    )
                    continue

                component_scores[calculator.__class__.__name__] = float(score)
                total_weight += weight

                logger.debug(f"{calculator.__class__.__name__} 能量: {score:.3f} (权重: {weight:.3f})")

            except Exception as e:
                logger.warning(f"计算 {calculator.__class__.__name__} 能量失败: {e}")

        # 加权计算总能量
        if total_weight > 0:
            total_energy = 0.0
            for calculator in self.calculators:
                if calculator.__class__.__name__ in component_scores:
                    score = component_scores[calculator.__class__.__name__]
                    weight = calculator.get_weight()
                    total_energy += score * (weight / total_weight)
        else:
            total_energy = 0.5

        # 应用阈值调整和变换
        final_energy = self._apply_threshold_adjustment(total_energy)

        # 缓存结果
        self.energy_cache[stream_id] = (final_energy, time.time())

        # 清理过期缓存
        self._cleanup_cache()

        # 更新平均计算时间
        calculation_time = time.time() - start_time
        total_calculations = self.stats["total_calculations"]
        self.stats["average_calculation_time"] = (
            self.stats["average_calculation_time"] * (total_calculations - 1) + calculation_time
        ) / total_calculations

        logger.debug(
            f"聊天流 {stream_id} 最终能量: {final_energy:.3f} (原始: {total_energy:.3f}, 耗时: {calculation_time:.3f}s)"
        )
        return final_energy

    def _apply_threshold_adjustment(self, energy: float) -> float:
        """应用阈值调整和变换"""
        # 获取参考阈值
        high_threshold = self.thresholds["high_match"]
        reply_threshold = self.thresholds["reply"]

        # 计算与阈值的相对位置
        if energy >= high_threshold:
            # 高能量区域：指数增强
            adjusted = 0.7 + max(0, energy - 0.7) ** 0.8
        elif energy >= reply_threshold:
            # 中等能量区域：线性保持
            adjusted = energy
        else:
            # 低能量区域：对数压缩
            adjusted = 0.4 * (energy / 0.4) ** 1.2

        # 确保在合理范围内
        return max(0.1, min(1.0, adjusted))

    def get_energy_level(self, energy: float) -> EnergyLevel:
        """获取能量等级"""
        if energy >= EnergyLevel.VERY_HIGH.value:
            return EnergyLevel.VERY_HIGH
        elif energy >= EnergyLevel.HIGH.value:
            return EnergyLevel.HIGH
        elif energy >= EnergyLevel.NORMAL.value:
            return EnergyLevel.NORMAL
        elif energy >= EnergyLevel.LOW.value:
            return EnergyLevel.LOW
        else:
            return EnergyLevel.VERY_LOW

    def get_distribution_interval(self, energy: float) -> float:
        """基于能量等级获取分发周期"""
        energy_level = self.get_energy_level(energy)

        # 根据能量等级确定基础分发周期
        if energy_level == EnergyLevel.VERY_HIGH:
            base_interval = 1.0  # 1秒
        elif energy_level == EnergyLevel.HIGH:
            base_interval = 3.0  # 3秒
        elif energy_level == EnergyLevel.NORMAL:
            base_interval = 8.0  # 8秒
        elif energy_level == EnergyLevel.LOW:
            base_interval = 15.0  # 15秒
        else:
            base_interval = 30.0  # 30秒

        # 添加随机扰动避免同步
        import random

        jitter = random.uniform(0.8, 1.2)
        final_interval = base_interval * jitter

        # 确保在配置范围内
        min_interval = getattr(global_config.chat, "dynamic_distribution_min_interval", 1.0)
        max_interval = getattr(global_config.chat, "dynamic_distribution_max_interval", 60.0)

        return max(min_interval, min(max_interval, final_interval))

    def invalidate_cache(self, stream_id: str) -> None:
        """失效指定流的缓存"""
        if stream_id in self.energy_cache:
            del self.energy_cache[stream_id]
            logger.debug(f"已清除聊天流 {stream_id} 的能量缓存")

    def _cleanup_cache(self) -> None:
        """清理过期缓存"""
        current_time = time.time()
        expired_keys = [
            stream_id
            for stream_id, (_, timestamp) in self.energy_cache.items()
            if current_time - timestamp > self.cache_ttl
        ]

        for key in expired_keys:
            del self.energy_cache[key]

        if expired_keys:
            logger.debug(f"清理了 {len(expired_keys)} 个过期能量缓存")

    def get_statistics(self) -> dict[str, Any]:
        """获取统计信息"""
        return {
            "cache_size": len(self.energy_cache),
            "calculators": [calc.__class__.__name__ for calc in self.calculators],
            "thresholds": self.thresholds,
            "performance_stats": self.stats.copy(),
        }

    def update_thresholds(self, new_thresholds: dict[str, float]) -> None:
        """更新阈值"""
        self.thresholds.update(new_thresholds)

        # 确保阈值关系合理
        self.thresholds["high_match"] = max(self.thresholds["high_match"], self.thresholds["reply"] + 0.1)
        self.thresholds["reply"] = max(self.thresholds["reply"], self.thresholds["non_reply"] + 0.1)

        self.stats["last_threshold_update"] = time.time()
        logger.info(f"更新AFC阈值: {self.thresholds}")

    def add_calculator(self, calculator: EnergyCalculator) -> None:
        """添加计算器"""
        self.calculators.append(calculator)
        logger.info(f"添加能量计算器: {calculator.__class__.__name__}")

    def remove_calculator(self, calculator: EnergyCalculator) -> None:
        """移除计算器"""
        if calculator in self.calculators:
            self.calculators.remove(calculator)
            logger.info(f"移除能量计算器: {calculator.__class__.__name__}")

    def clear_cache(self) -> None:
        """清空缓存"""
        self.energy_cache.clear()
        logger.info("清空能量缓存")

    def get_cache_hit_rate(self) -> float:
        """获取缓存命中率"""
        total_requests = self.stats.get("cache_hits", 0) + self.stats.get("cache_misses", 0)
        if total_requests == 0:
            return 0.0
        return self.stats["cache_hits"] / total_requests


# 全局能量管理器实例
energy_manager = EnergyManager()
