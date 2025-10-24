import enum
from datetime import datetime, timedelta
from typing import Any

from src.common.logger import get_logger
from src.manager.local_store_manager import local_storage
from . import utils

logger = get_logger("sleep_state_manager")


class SleepState(enum.Enum):
    """
    定义了所有可能的睡眠状态。
    使用枚举可以使状态管理更加清晰和安全。
    """

    AWAKE = "awake"  # 清醒状态，正常活动
    SLEEPING = "sleeping"  # 沉睡状态，此时应拦截消息
    INSOMNIA = "insomnia"  # 失眠状态（为未来功能预留）
    WOKEN_UP_ANGRY = "woken_up_angry"  # 被吵醒后的生气状态（为未来功能预留）


class SleepStateManager:
    """
    睡眠状态管理器 (单例模式)

    这是整个睡眠系统的数据核心，负责：
    1. 管理当前的睡眠状态（如：是否在睡觉、唤醒度等）。
    2. 将状态持久化到本地JSON文件(`local_store.json`)，实现重启后状态不丢失。
    3. 提供统一的接口供其他模块查询和修改睡眠状态。
    """

    _instance = None
    _STATE_KEY = "sleep_system_state"  # 在 local_store.json 中存储的键名

    def __new__(cls, *args, **kwargs):
        # 实现单例模式，确保全局只有一个状态管理器实例
        if not cls._instance:
            cls._instance = super(SleepStateManager, cls).__new__(cls, *args, **kwargs)
        return cls._instance

    def __init__(self):
        """
        初始化状态管理器，定义状态数据结构并从本地加载历史状态。
        """
        self.state: dict[str, Any] = {}
        self._default_state()
        self.load_state()
        self._refresh_sleep_state()

    def _default_state(self):
        """
        定义并重置为默认的“清醒”状态。
        当机器人启动或从睡眠中醒来时调用。
        """
        self.state = {
            "state": SleepState.AWAKE.value,
            "state_until": None,  # 特殊状态（如生气）的自动结束时间
            "sleep_start_time": None,  # 本次睡眠的开始时间
            "wake_up_time": None,  # 预定的起床时间
            "wakefulness": 0.0,  # 唤醒度/清醒值，用于判断是否被吵醒
            "last_checked": None,  # 定时任务最后检查的时间
        }

    def load_state(self):
        """
        程序启动时，从 local_storage 加载上一次的状态。
        如果找不到历史状态，则初始化为默认状态。
        """
        stored_state = local_storage[self._STATE_KEY]
        if isinstance(stored_state, dict):
            # 合并加载的状态，以防新增字段
            self.state.update(stored_state)
            # 确保 state 字段是枚举成员
            if "state" in self.state and not isinstance(self.state["state"], SleepState):
                try:
                    self.state["state"] = SleepState(self.state["state"])
                except ValueError:
                    logger.warning(f"加载了无效的睡眠状态 '{self.state['state']}'，重置为 AWAKE。")
                    self.state["state"] = SleepState.AWAKE
            else:
                 self.state["state"] = SleepState.AWAKE # 兼容旧数据

            logger.info(f"成功加载睡眠状态: {self.get_current_state().name}")
        else:
            logger.info("未找到已存储的睡眠状态，将使用默认值。")
            self.save_state()

    def save_state(self):
        """
        将当前内存中的状态保存到 local_storage。
        在保存前，会将枚举类型的 state 转换为字符串，以便JSON序列化。
        """
        data_to_save = self.state.copy()
        # 将 state 枚举成员转换为它的值（字符串）
        data_to_save["state"] = self.state["state"]
        local_storage[self._STATE_KEY] = data_to_save
        logger.debug(f"睡眠状态已保存: {data_to_save}")

    def get_current_state(self) -> SleepState:
        """
        获取当前的睡眠状态。
        在返回状态前，会先检查特殊状态（如生气）是否已过期。
        """
        # 检查特殊状态是否已过期
        state_until_str = self.state.get("state_until")
        if state_until_str:
            state_until = datetime.fromisoformat(state_until_str)
            if datetime.now() > state_until:
                logger.info(f"特殊状态 {self.state['state'].name} 已结束，自动恢复为 SLEEPING。")
                # 假设特殊状态（如生气）结束后，是恢复到普通睡眠状态
                self.set_state(SleepState.SLEEPING)

        return self.state["state"]

    def set_state(
        self,
        new_state: SleepState,
        duration_seconds: float | None = None,
        sleep_start: datetime | None = None,
        wake_up: datetime | None = None,
    ):
        """
        核心函数：切换到新的睡眠状态，并更新相关的状态数据。
        """
        current_state = self.get_current_state()
        if current_state == new_state:
            return  # 状态未改变

        logger.info(f"睡眠状态变更: {current_state.name} -> {new_state.name}")
        self.state["state"] = new_state

        if new_state == SleepState.AWAKE:
            self._default_state() # 醒来时重置所有状态
            self.state["state"] = SleepState.AWAKE # 确保状态正确

        elif new_state == SleepState.SLEEPING:
            self.state["sleep_start_time"] = (sleep_start or datetime.now()).isoformat()
            self.state["wake_up_time"] = wake_up.isoformat() if wake_up else None
            self.state["state_until"] = None # 清除特殊状态持续时间
            self.state["wakefulness"] = 0.0 # 进入睡眠时清零唤醒度

        elif new_state in [SleepState.WOKEN_UP_ANGRY, SleepState.INSOMNIA]:
            if duration_seconds:
                self.state["state_until"] = (datetime.now() + timedelta(seconds=duration_seconds)).isoformat()
            else:
                self.state["state_until"] = None


        self.save_state()

    def update_last_checked(self):
        """更新最后检查时间"""
        self.state["last_checked"] = datetime.now().isoformat()
        self.save_state()

    def get_wake_up_time(self) -> datetime | None:
        """获取预定的起床时间，如果已设置的话。"""
        wake_up_str = self.state.get("wake_up_time")
        if wake_up_str:
            try:
                return datetime.fromisoformat(wake_up_str)
            except (ValueError, TypeError):
                return None
        return None

    def get_sleep_start_time(self) -> datetime | None:
        """获取本次睡眠的开始时间，如果已设置的话。"""
        sleep_start_str = self.state.get("sleep_start_time")
        if sleep_start_str:
            try:
                return datetime.fromisoformat(sleep_start_str)
            except (ValueError, TypeError):
                return None
        return None

    def set_wake_up_time(self, wake_up: datetime):
        """
        更新起床时间。
        主要用于“日程表”模式下，当第二天凌晨拿到新日程时，更新之前未知的起床时间。
        """
        if self.get_current_state() == SleepState.AWAKE:
            logger.warning("尝试为清醒状态设置起床时间，操作被忽略。")
            return
        self.state["wake_up_time"] = wake_up.isoformat()
        logger.info(f"更新预定起床时间为: {self.state['wake_up_time']}")
        self.save_state()

    def _refresh_sleep_state(self):
        """
        程序启动时，刷新并校准当前的睡眠状态。
        """
        now = datetime.now()
        current_state = self.get_current_state()
        # 检查并更新作息时间
        # _should_be_sleeping 会综合判断固定时间和日程表，返回最终结果
        _, new_wake_up_time_for_check = utils.should_be_sleeping(now)

        # 我们需要的是睡眠开始时间，所以再单独获取一下
        from src.config.config import global_config
        if global_config.sleep_system.sleep_by_schedule:
            new_sleep_time, _ = utils._get_sleep_times_from_schedule(now) # type: ignore
        else:
            new_sleep_time, _ = utils._get_fixed_sleep_times(now) # type: ignore
        logger.info(f"新的睡眠时间:{new_sleep_time}")

        stored_sleep_time = self.get_sleep_start_time()

        if new_sleep_time and stored_sleep_time:
            time_diff = abs(new_sleep_time - stored_sleep_time)
            # 如果差异大于2小时，则认为作息已更新
            if time_diff > timedelta(hours=2):
                logger.info(f"检测到新的睡眠时间 {new_sleep_time} 与存储的 {stored_sleep_time} 差异过大，将进行更新。")
                # 更新状态以记录新的作息时间，但保持清醒
                self.state["sleep_start_time"] = new_sleep_time.timestamp()
                self.state["wake_up_time"] = new_wake_up_time_for_check.timestamp() if new_wake_up_time_for_check else None
                self.save_state()
        if current_state == SleepState.SLEEPING:
                wake_up_time = self.get_wake_up_time()
                if wake_up_time:
                    if now <= wake_up_time:
                        logger.info(f"启动时检测到已超过起床时间 (起床时间: {wake_up_time})，将状态强制唤醒。")
                        self.set_state(SleepState.AWAKE)
                else:
                    # 如果没有起床时间，则根据睡眠开始时间判断
                    sleep_start_time = self.get_sleep_start_time()
                    if sleep_start_time:
                        logger.info(f"{now}-{sleep_start_time}")
                        if now > sleep_start_time:
                            logger.warning(f"当前时间 {now} 早于睡眠开始时间 {sleep_start_time}。将强制唤醒。")
                            self.set_state(SleepState.AWAKE)
                        # 如果 now >= sleep_start_time，则说明正在正常睡眠，无需操作
                    else:
                        # 如果连睡眠开始时间都没有，说明状态异常
                        logger.warning("启动时检测到睡眠状态异常：没有起床时间也没有睡眠开始时间。将强制唤醒。")
                        self.set_state(SleepState.AWAKE)
        elif current_state == SleepState.AWAKE:
            should_sleep, new_wake_up_time = utils.should_be_sleeping(now)

            if should_sleep:
                logger.info("启动时检测到当前时间应处于睡眠状态，但状态为清醒。将强制进入睡眠。")
                self.set_state(SleepState.SLEEPING, wake_up=new_wake_up_time)


# 全局单例
_sleep_state_manager_instance: SleepStateManager | None = None


def get_sleep_state_manager() -> SleepStateManager:
    """
    获取睡眠状态管理器的单例实例。
    在首次调用时会创建实例。
    """
    global _sleep_state_manager_instance
    if _sleep_state_manager_instance is None:
        _sleep_state_manager_instance = SleepStateManager()
    return _sleep_state_manager_instance
