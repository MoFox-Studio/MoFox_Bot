"""兴趣值计算组件管理器

管理兴趣值计算组件的生命周期，确保系统只能有一个兴趣值计算组件实例运行
"""

import asyncio
import time
from typing import TYPE_CHECKING

from src.common.logger import get_logger
from src.plugin_system.base.base_interest_calculator import BaseInterestCalculator, InterestCalculationResult

if TYPE_CHECKING:
    from src.common.data_models.database_data_model import DatabaseMessages

logger = get_logger("interest_manager")


class InterestManager:
    """兴趣值计算组件管理器"""

    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not self._initialized:
            self._current_calculator: BaseInterestCalculator | None = None
            self._calculator_lock = asyncio.Lock()
            self._last_calculation_time = 0.0
            self._total_calculations = 0
            self._failed_calculations = 0
            self._calculation_queue = asyncio.Queue()
            self._worker_task = None
            self._shutdown_event = asyncio.Event()
            self._initialized = True

    async def initialize(self):
        """初始化管理器"""
        pass

    async def shutdown(self):
        """关闭管理器"""
        self._shutdown_event.set()

        if self._current_calculator:
            await self._current_calculator.cleanup()
            self._current_calculator = None

        logger.info("兴趣值管理器已关闭")

    async def register_calculator(self, calculator: BaseInterestCalculator) -> bool:
        """注册兴趣值计算组件（系统只能有一个活跃的兴趣值计算器）

        Args:
            calculator: 兴趣值计算组件实例

        Returns:
            bool: 注册是否成功
        """
        async with self._calculator_lock:
            try:
                # 检查是否已有相同的计算器
                if self._current_calculator and self._current_calculator.component_name == calculator.component_name:
                    logger.warning(f"兴趣值计算组件 {calculator.component_name} 已经注册，跳过重复注册")
                    return True

                # 如果已有组件在运行，先清理并替换
                if self._current_calculator:
                    logger.info(
                        f"替换现有兴趣值计算组件: {self._current_calculator.component_name} -> {calculator.component_name}"
                    )
                    await self._current_calculator.cleanup()
                else:
                    logger.info(f"注册新的兴趣值计算组件: {calculator.component_name}")

                # 初始化新组件
                if await calculator.initialize():
                    self._current_calculator = calculator
                    logger.info(f"兴趣值计算组件注册成功: {calculator.component_name} v{calculator.component_version}")
                    logger.info("系统现在只有一个活跃的兴趣值计算器")
                    return True
                else:
                    logger.error(f"兴趣值计算组件初始化失败: {calculator.component_name}")
                    return False

            except Exception as e:
                logger.error(f"注册兴趣值计算组件失败: {e}", exc_info=True)
                return False

    async def calculate_interest(self, message: "DatabaseMessages", timeout: float = 2.0) -> InterestCalculationResult:
        """计算消息兴趣值

        Args:
            message: 数据库消息对象
            timeout: 最大等待时间（秒），超时则使用默认值返回

        Returns:
            InterestCalculationResult: 计算结果或默认结果
        """
        if not self._current_calculator:
            # 返回默认结果
            return InterestCalculationResult(
                success=False,
                message_id=getattr(message, "message_id", ""),
                interest_value=0.3,
                error_message="没有可用的兴趣值计算组件",
            )

        # 使用 create_task 异步执行计算
        task = asyncio.create_task(self._async_calculate(message))

        try:
            # 等待计算结果，但有超时限制
            result = await asyncio.wait_for(task, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            # 超时返回默认结果，但计算仍在后台继续
            logger.warning(f"兴趣值计算超时 ({timeout}s)，消息 {getattr(message, 'message_id', '')} 使用默认兴趣值 0.5")
            return InterestCalculationResult(
                success=True,
                message_id=getattr(message, "message_id", ""),
                interest_value=0.5,  # 固定默认兴趣值
                should_reply=False,
                should_act=False,
                error_message=f"计算超时({timeout}s)，使用默认值",
            )
        except Exception as e:
            # 发生异常，返回默认结果
            logger.error(f"兴趣值计算异常: {e}")
            return InterestCalculationResult(
                success=False,
                message_id=getattr(message, "message_id", ""),
                interest_value=0.3,
                error_message=f"计算异常: {e!s}",
            )

    async def _async_calculate(self, message: "DatabaseMessages") -> InterestCalculationResult:
        """异步执行兴趣值计算"""
        start_time = time.time()
        self._total_calculations += 1

        try:
            # 使用组件的安全执行方法
            result = await self._current_calculator._safe_execute(message)

            if result.success:
                self._last_calculation_time = time.time()
                logger.debug(f"兴趣值计算完成: {result.interest_value:.3f} (耗时: {result.calculation_time:.3f}s)")
            else:
                self._failed_calculations += 1
                logger.warning(f"兴趣值计算失败: {result.error_message}")

            return result

        except Exception as e:
            self._failed_calculations += 1
            logger.error(f"兴趣值计算异常: {e}", exc_info=True)
            return InterestCalculationResult(
                success=False,
                message_id=getattr(message, "message_id", ""),
                interest_value=0.0,
                error_message=f"计算异常: {e!s}",
                calculation_time=time.time() - start_time,
            )

    async def _calculation_worker(self):
        """计算工作线程（预留用于批量处理）"""
        while not self._shutdown_event.is_set():
            try:
                # 等待计算任务或关闭信号
                await asyncio.wait_for(self._calculation_queue.get(), timeout=1.0)

                # 处理计算任务
                # 这里可以实现批量处理逻辑

            except asyncio.TimeoutError:
                # 超时继续循环
                continue
            except asyncio.CancelledError:
                # 任务被取消，退出循环
                break
            except Exception as e:
                logger.error(f"计算工作线程异常: {e}", exc_info=True)

    def get_current_calculator(self) -> BaseInterestCalculator | None:
        """获取当前活跃的兴趣值计算组件"""
        return self._current_calculator

    def get_statistics(self) -> dict:
        """获取管理器统计信息"""
        success_rate = 1.0 - (self._failed_calculations / max(1, self._total_calculations))

        stats = {
            "manager_statistics": {
                "total_calculations": self._total_calculations,
                "failed_calculations": self._failed_calculations,
                "success_rate": success_rate,
                "last_calculation_time": self._last_calculation_time,
                "current_calculator": self._current_calculator.component_name if self._current_calculator else None,
            }
        }

        # 添加当前组件的统计信息
        if self._current_calculator:
            stats["calculator_statistics"] = self._current_calculator.get_statistics()

        return stats

    async def health_check(self) -> bool:
        """健康检查"""
        if not self._current_calculator:
            return False

        try:
            # 检查组件是否还活跃
            return self._current_calculator.is_enabled
        except Exception:
            return False

    def has_calculator(self) -> bool:
        """检查是否有可用的计算组件"""
        return self._current_calculator is not None and self._current_calculator.is_enabled


# 全局实例
_interest_manager = None


def get_interest_manager() -> InterestManager:
    """获取兴趣值管理器实例"""
    global _interest_manager
    if _interest_manager is None:
        _interest_manager = InterestManager()
    return _interest_manager
