from src.common.logger import get_logger
from src.manager.local_store_manager import local_storage

logger = get_logger("wakeup_context")


class WakeUpContext:
    """
    唤醒上下文，负责封装和管理所有与唤醒相关的状态，并处理其持久化。
    """

    def __init__(self):
        """初始化唤醒上下文，并从本地存储加载初始状态。"""
        self.wakeup_value: float = 0.0
        self.is_angry: bool = False
        self.angry_start_time: float = 0.0
        self.sleep_pressure: float = 100.0  # 新增：睡眠压力
        self.load()

    def _get_storage_key(self) -> str:
        """获取本地存储键"""
        return "global_wakeup_manager_state"

    def load(self):
        """从本地存储加载状态"""
        state = local_storage[self._get_storage_key()]
        if state and isinstance(state, dict):
            self.wakeup_value = state.get("wakeup_value", 0.0)
            self.is_angry = state.get("is_angry", False)
            self.angry_start_time = state.get("angry_start_time", 0.0)
            self.sleep_pressure = state.get("sleep_pressure", 100.0)
            logger.info(f"成功从本地存储加载唤醒上下文: {state}")
        else:
            logger.info("未找到本地唤醒上下文，将使用默认值初始化。")

    def save(self):
        """将当前状态保存到本地存储"""
        state = {
            "wakeup_value": self.wakeup_value,
            "is_angry": self.is_angry,
            "angry_start_time": self.angry_start_time,
            "sleep_pressure": self.sleep_pressure,
        }
        local_storage[self._get_storage_key()] = state
        logger.debug(f"已将唤醒上下文保存到本地存储: {state}")
