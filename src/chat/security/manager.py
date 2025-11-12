"""
安全管理器

负责管理和协调多个安全检测器。
"""

import asyncio
import time
from typing import Any

from src.common.logger import get_logger

from .interfaces import SecurityAction, SecurityChecker, SecurityCheckResult, SecurityLevel

logger = get_logger("security.manager")


class SecurityManager:
    """安全管理器"""

    def __init__(self):
        """初始化安全管理器"""
        self._checkers: list[SecurityChecker] = []
        self._checker_cache: dict[str, SecurityChecker] = {}
        self._enabled = True

    def register_checker(self, checker: SecurityChecker):
        """注册安全检测器

        Args:
            checker: 安全检测器实例
        """
        if checker.name in self._checker_cache:
            logger.warning(f"检测器 '{checker.name}' 已存在，将被替换")
            self.unregister_checker(checker.name)

        self._checkers.append(checker)
        self._checker_cache[checker.name] = checker

        # 按优先级排序
        self._checkers.sort(key=lambda x: x.priority, reverse=True)

        logger.info(f"已注册安全检测器: {checker.name} (优先级: {checker.priority})")

    def unregister_checker(self, name: str):
        """注销安全检测器

        Args:
            name: 检测器名称
        """
        if name in self._checker_cache:
            checker = self._checker_cache[name]
            self._checkers.remove(checker)
            del self._checker_cache[name]
            logger.info(f"已注销安全检测器: {name}")

    def get_checker(self, name: str) -> SecurityChecker | None:
        """获取指定的检测器

        Args:
            name: 检测器名称

        Returns:
            SecurityChecker | None: 检测器实例，不存在则返回None
        """
        return self._checker_cache.get(name)

    def list_checkers(self) -> list[str]:
        """列出所有已注册的检测器名称

        Returns:
            list[str]: 检测器名称列表
        """
        return [checker.name for checker in self._checkers]

    async def check_message(
        self, message: str, context: dict | None = None, mode: str = "sequential"
    ) -> SecurityCheckResult:
        """检测消息安全性

        Args:
            message: 待检测的消息内容
            context: 上下文信息
            mode: 检测模式
                - "sequential": 顺序执行，遇到不安全结果立即返回
                - "parallel": 并行执行所有检测器
                - "all": 顺序执行所有检测器

        Returns:
            SecurityCheckResult: 综合检测结果
        """
        if not self._enabled:
            return SecurityCheckResult(
                is_safe=True,
                level=SecurityLevel.SAFE,
                action=SecurityAction.ALLOW,
                reason="安全管理器已禁用",
                checker_name="SecurityManager",
            )

        if not self._checkers:
            return SecurityCheckResult(
                is_safe=True,
                level=SecurityLevel.SAFE,
                action=SecurityAction.ALLOW,
                reason="未注册任何检测器",
                checker_name="SecurityManager",
            )

        start_time = time.time()
        context = context or {}

        try:
            if mode == "parallel":
                return await self._check_parallel(message, context, start_time)
            elif mode == "all":
                return await self._check_all(message, context, start_time)
            else:  # sequential
                return await self._check_sequential(message, context, start_time)

        except Exception as e:
            logger.error(f"安全检测失败: {e}", exc_info=True)
            return SecurityCheckResult(
                is_safe=True,  # 异常情况下默认允许通过，避免阻断正常消息
                level=SecurityLevel.SAFE,
                action=SecurityAction.ALLOW,
                reason=f"检测异常: {e}",
                checker_name="SecurityManager",
                processing_time=time.time() - start_time,
            )

    async def _check_sequential(
        self, message: str, context: dict, start_time: float
    ) -> SecurityCheckResult:
        """顺序检测模式（快速失败）"""
        for checker in self._checkers:
            if not checker.enabled:
                continue

            # 预检查
            if not await checker.pre_check(message, context):
                continue

            # 执行完整检查
            result = await checker.check(message, context)
            result.checker_name = checker.name

            # 如果检测到不安全，立即返回
            if not result.is_safe:
                result.processing_time = time.time() - start_time
                logger.warning(
                    f"检测器 '{checker.name}' 发现风险: {result.level.value}, "
                    f"置信度: {result.confidence:.2f}, 原因: {result.reason}"
                )
                return result

        # 所有检测器都通过
        return SecurityCheckResult(
            is_safe=True,
            level=SecurityLevel.SAFE,
            action=SecurityAction.ALLOW,
            reason="所有检测器检查通过",
            checker_name="SecurityManager",
            processing_time=time.time() - start_time,
        )

    async def _check_parallel(self, message: str, context: dict, start_time: float) -> SecurityCheckResult:
        """并行检测模式"""
        enabled_checkers = [c for c in self._checkers if c.enabled]

        # 执行预检查
        pre_check_tasks = [c.pre_check(message, context) for c in enabled_checkers]
        pre_check_results = await asyncio.gather(*pre_check_tasks, return_exceptions=True)

        # 筛选需要完整检查的检测器
        checkers_to_run = [
            c for c, need_check in zip(enabled_checkers, pre_check_results) if need_check is True
        ]

        if not checkers_to_run:
            return SecurityCheckResult(
                is_safe=True,
                level=SecurityLevel.SAFE,
                action=SecurityAction.ALLOW,
                reason="预检查全部跳过",
                checker_name="SecurityManager",
                processing_time=time.time() - start_time,
            )

        # 并行执行检查
        check_tasks = [c.check(message, context) for c in checkers_to_run]
        results = await asyncio.gather(*check_tasks, return_exceptions=True)

        # 过滤异常结果
        valid_results = []
        for checker, result in zip(checkers_to_run, results):
            if isinstance(result, Exception):
                logger.error(f"检测器 '{checker.name}' 执行失败: {result}")
                continue
            result.checker_name = checker.name
            valid_results.append(result)

        # 合并结果
        return self._merge_results(valid_results, time.time() - start_time)

    async def _check_all(self, message: str, context: dict, start_time: float) -> SecurityCheckResult:
        """检测所有模式（顺序执行所有检测器）"""
        results = []

        for checker in self._checkers:
            if not checker.enabled:
                continue

            # 预检查
            if not await checker.pre_check(message, context):
                continue

            # 执行完整检查
            try:
                result = await checker.check(message, context)
                result.checker_name = checker.name
                results.append(result)
            except Exception as e:
                logger.error(f"检测器 '{checker.name}' 执行失败: {e}")

        if not results:
            return SecurityCheckResult(
                is_safe=True,
                level=SecurityLevel.SAFE,
                action=SecurityAction.ALLOW,
                reason="无有效检测结果",
                checker_name="SecurityManager",
                processing_time=time.time() - start_time,
            )

        # 合并结果
        return self._merge_results(results, time.time() - start_time)

    def _merge_results(self, results: list[SecurityCheckResult], total_time: float) -> SecurityCheckResult:
        """合并多个检测结果

        策略：
        - 如果有任何 CRITICAL 级别，返回最严重的
        - 如果有任何 HIGH_RISK，返回最高风险的
        - 否则返回置信度最高的结果
        """
        if not results:
            return SecurityCheckResult(
                is_safe=True,
                level=SecurityLevel.SAFE,
                action=SecurityAction.ALLOW,
                reason="无检测结果",
                processing_time=total_time,
            )

        # 按风险级别和置信度排序
        level_priority = {
            SecurityLevel.CRITICAL: 5,
            SecurityLevel.HIGH_RISK: 4,
            SecurityLevel.MEDIUM_RISK: 3,
            SecurityLevel.LOW_RISK: 2,
            SecurityLevel.SAFE: 1,
        }

        results.sort(key=lambda r: (level_priority.get(r.level, 0), r.confidence), reverse=True)

        highest_risk = results[0]

        # 收集所有不安全的检测器信息
        unsafe_checkers = [r.checker_name for r in results if not r.is_safe]
        all_patterns = []
        for r in results:
            all_patterns.extend(r.matched_patterns)

        return SecurityCheckResult(
            is_safe=highest_risk.is_safe,
            level=highest_risk.level,
            confidence=highest_risk.confidence,
            action=highest_risk.action,
            reason=f"{highest_risk.reason} (检测器: {', '.join(unsafe_checkers) if unsafe_checkers else highest_risk.checker_name})",
            details={
                "total_checkers": len(results),
                "unsafe_count": len(unsafe_checkers),
                "all_results": [
                    {
                        "checker": r.checker_name,
                        "level": r.level.value,
                        "confidence": r.confidence,
                        "reason": r.reason,
                    }
                    for r in results
                ],
            },
            matched_patterns=list(set(all_patterns)),
            checker_name="SecurityManager",
            processing_time=total_time,
        )

    def enable(self):
        """启用安全管理器"""
        self._enabled = True
        logger.info("安全管理器已启用")

    def disable(self):
        """禁用安全管理器"""
        self._enabled = False
        logger.info("安全管理器已禁用")

    @property
    def is_enabled(self) -> bool:
        """是否已启用"""
        return self._enabled

    def get_stats(self) -> dict[str, Any]:
        """获取统计信息"""
        return {
            "enabled": self._enabled,
            "total_checkers": len(self._checkers),
            "enabled_checkers": sum(1 for c in self._checkers if c.enabled),
            "checkers": [
                {"name": c.name, "priority": c.priority, "enabled": c.enabled} for c in self._checkers
            ],
        }


# 全局单例
_global_security_manager: SecurityManager | None = None


def get_security_manager() -> SecurityManager:
    """获取全局安全管理器实例"""
    global _global_security_manager
    if _global_security_manager is None:
        _global_security_manager = SecurityManager()
    return _global_security_manager
