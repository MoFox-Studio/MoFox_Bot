from src.common.logger import get_logger
from src.manager.async_task_manager import AsyncTask, async_task_manager
from .sleep_logic import sleep_logic

logger = get_logger("sleep_tasks")


class SleepSystemCheckTask(AsyncTask):
    """
    睡眠系统周期性检查任务。
    继承自 AsyncTask，由 async_task_manager 统一管理。
    """

    def __init__(self, run_interval: int = 60):
        """
        初始化任务。
        Args:
            run_interval (int): 任务运行的时间间隔（秒）。默认为60秒检查一次。
        """
        super().__init__(task_name="SleepSystemCheckTask", run_interval=run_interval)

    async def run(self):
        """
        任务的核心执行过程。
        每次运行时，调用 sleep_logic 的主函数来检查和更新状态。
        """
        logger.debug("睡眠系统定时任务触发，开始检查状态...")
        try:
            # 调用“大脑”进行一次思考和判断
            sleep_logic.check_and_update_sleep_state()
        except Exception as e:
            logger.error(f"周期性检查睡眠状态时发生未知错误: {e}", exc_info=True)


async def start_sleep_system_tasks():
    """
    启动睡眠系统的后台定时检查任务。
    这个函数应该在程序启动时（例如 main.py）被调用。
    """
    logger.info("正在启动睡眠系统后台任务...")
    check_task = SleepSystemCheckTask()
    await async_task_manager.add_task(check_task)
    logger.info("睡眠系统后台任务已成功启动。")
