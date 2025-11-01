"""
反注入系统统计模块

负责统计数据的收集、更新和查询
"""

import datetime
from typing import Any, TypeVar, cast

from sqlalchemy import delete, select

from src.common.database.core import get_db_session
from src.common.database.core.models import AntiInjectionStats
from src.common.logger import get_logger
from src.config.config import global_config

logger = get_logger("anti_injector.statistics")


TNum = TypeVar("TNum", int, float)


def _add_optional(a: TNum | None, b: TNum) -> TNum:
    """安全相加：左值可能为 None。

    Args:
        a: 可能为 None 的当前值
        b: 要累加的增量（非 None）
    Returns:
        新的累加结果（与 b 同类型）
    """
    if a is None:
        return b
    return cast(TNum, a + b)  # a 不为 None，此处显式 cast 便于类型检查


class AntiInjectionStatistics:
    """反注入系统统计管理类

    主要改进：
    - 对 "可能为 None" 的数值字段做集中安全处理，减少在业务逻辑里反复判空。
    - 补充类型注解，便于静态检查器（Pylance/Pyright）识别。
    """

    def __init__(self):
        """初始化统计管理器"""
        self.session_start_time = datetime.datetime.now()
        """当前会话开始时间"""

    @staticmethod
    async def get_or_create_stats() -> AntiInjectionStats:
        """获取或创建统计记录

        Returns:
            AntiInjectionStats | None: 成功返回模型实例，否则 None
        """
        async with get_db_session() as session:
                # 获取最新的统计记录，如果没有则创建
            stats = (
                    (await session.execute(select(AntiInjectionStats).order_by(AntiInjectionStats.id.desc())))
                    .scalars()
                    .first()
                )
            if not stats:
                stats = AntiInjectionStats()
                session.add(stats)
                await session.commit()
                await session.refresh(stats)
            return stats


    @staticmethod
    async def update_stats(**kwargs: Any) -> None:
        """更新统计数据（批量可选字段）

        支持字段：
            - processing_time_delta: float 累加到 processing_time_total
            - last_processing_time: float 设置 last_process_time
            - total_messages / detected_injections / blocked_messages / shielded_messages / error_count: 累加
            - 其他任意字段：直接赋值（若模型存在该属性）
        """
        try:
            async with get_db_session() as session:
                stats = (
                    (await session.execute(select(AntiInjectionStats).order_by(AntiInjectionStats.id.desc())))
                    .scalars()
                    .first()
                )
                if not stats:
                    stats = AntiInjectionStats()
                    session.add(stats)

                # 更新统计字段
                for key, value in kwargs.items():
                    if key == "processing_time_delta":
                        # 处理时间累加 - 确保不为 None
                        delta = float(value)
                        stats.processing_time_total = _add_optional(stats.processing_time_total, delta)
                        continue
                    elif key == "last_processing_time":
                        # 直接设置最后处理时间
                        stats.last_process_time = float(value)
                        continue
                    elif hasattr(stats, key):
                        if key in [
                            "total_messages",
                            "detected_injections",
                            "blocked_messages",
                            "shielded_messages",
                            "error_count",
                        ]:
                            # 累加类型的字段 - 统一用辅助函数
                            current_value = cast(int | None, getattr(stats, key))
                            increment = int(value)
                            setattr(stats, key, _add_optional(current_value, increment))
                        else:
                            # 直接设置的字段
                            setattr(stats, key, value)

                await session.commit()
        except Exception as e:
            logger.error(f"更新统计数据失败: {e}")

    async def get_stats(self) -> dict[str, Any]:
        """获取统计信息"""
        try:
            # 检查反注入系统是否启用
            if not global_config.anti_prompt_injection.enabled:
                return {
                    "status": "disabled",
                    "message": "反注入系统未启用",
                    "uptime": "N/A",
                    "total_messages": 0,
                    "detected_injections": 0,
                    "blocked_messages": 0,
                    "shielded_messages": 0,
                    "detection_rate": "N/A",
                    "average_processing_time": "N/A",
                    "last_processing_time": "N/A",
                    "error_count": 0,
                }

            stats = await self.get_or_create_stats()


            # 计算派生统计信息 - 处理 None 值
            total_messages = stats.total_messages or 0
            detected_injections = stats.detected_injections or 0  # type: ignore[attr-defined]
            processing_time_total = stats.processing_time_total or 0.0  # type: ignore[attr-defined]

            detection_rate = (detected_injections / total_messages * 100) if total_messages > 0 else 0
            avg_processing_time = (processing_time_total / total_messages) if total_messages > 0 else 0

            # 使用当前会话开始时间计算运行时间，而不是数据库中的start_time
            # 这样可以避免重启后显示错误的运行时间
            current_time = datetime.datetime.now()
            uptime = current_time - self.session_start_time

            last_proc = stats.last_process_time  # type: ignore[attr-defined]
            blocked_messages = stats.blocked_messages or 0  # type: ignore[attr-defined]
            shielded_messages = stats.shielded_messages or 0  # type: ignore[attr-defined]
            error_count = stats.error_count or 0  # type: ignore[attr-defined]

            return {
                "status": "enabled",
                "uptime": str(uptime),
                "total_messages": total_messages,
                "detected_injections": detected_injections,
                "blocked_messages": blocked_messages,
                "shielded_messages": shielded_messages,
                "detection_rate": f"{detection_rate:.2f}%",
                "average_processing_time": f"{avg_processing_time:.3f}s",
                "last_processing_time": f"{last_proc:.3f}s" if last_proc else "0.000s",
                "error_count": error_count,
            }
        except Exception as e:
            logger.error(f"获取统计信息失败: {e}")
            return {"error": f"获取统计信息失败: {e}"}

    @staticmethod
    async def reset_stats():
        """重置统计信息"""
        try:
            async with get_db_session() as session:
                # 删除现有统计记录
                await session.execute(delete(AntiInjectionStats))
                await session.commit()
                logger.info("统计信息已重置")
        except Exception as e:
            logger.error(f"重置统计信息失败: {e}")
