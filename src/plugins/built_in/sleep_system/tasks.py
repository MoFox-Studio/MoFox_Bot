import asyncio
import logging
from typing import Optional
from .sleep_logic import SleepLogic

logger = logging.getLogger(__name__)

class SleepCycleTask:
    """
    负责周期性地更新睡眠状态的后台任务。
    """
    def __init__(self, sleep_logic: SleepLogic, interval_seconds: int = 30):
        self.sleep_logic = sleep_logic
        self.interval_seconds = interval_seconds
        self._task: Optional[asyncio.Task] = None
        self._is_running = False

    async def _run(self):
        """任务的内部循环"""
        logger.info("睡眠系统周期性更新任务已启动。")
        while self._is_running:
            try:
                await self.sleep_logic.update_state()
                await asyncio.sleep(self.interval_seconds)
            except asyncio.CancelledError:
                logger.info("睡眠系统任务被取消。")
                break
            except Exception as e:
                logger.error(f"睡眠系统任务在执行期间发生错误: {e}", exc_info=True)
                # 发生错误后，等待一段时间再继续，避免快速失败循环
                await asyncio.sleep(self.interval_seconds * 2)

    def start(self):
        """启动后台任务"""
        if not self._is_running:
            self._is_running = True
            self._task = asyncio.create_task(self._run())
        else:
            logger.warning("尝试启动一个已经在运行的睡眠系统任务。")

    def stop(self):
        """停止后台任务"""
        if self._is_running and self._task:
            self._is_running = False
            self._task.cancel()
            logger.info("睡眠系统周期性更新任务已请求停止。")
        else:
            logger.warning("尝试停止一个尚未启动的睡眠系统任务。")
