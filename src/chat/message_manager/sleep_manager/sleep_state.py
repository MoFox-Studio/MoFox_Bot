from datetime import date, datetime
from enum import Enum, auto

from src.common.logger import get_logger
from src.manager.local_store_manager import local_storage

logger = get_logger("sleep_state")


class SleepState(Enum):
    """
    定义了角色可能处于的几种睡眠状态。
    这是一个状态机，用于管理角色的睡眠周期。
    """

    AWAKE = auto()  # 清醒状态
    INSOMNIA = auto()  # 失眠状态
    PREPARING_SLEEP = auto()  # 准备入睡状态，一个短暂的过渡期
    SLEEPING = auto()  # 正在睡觉状态
    WOKEN_UP = auto()  # 被吵醒状态


class SleepContext:
    """
    睡眠上下文，负责封装和管理所有与睡眠相关的状态，并处理其持久化。
    """

    def __init__(self):
        """初始化睡眠上下文，并从本地存储加载初始状态。"""
        self.current_state: SleepState = SleepState.AWAKE
        self.sleep_buffer_end_time: datetime | None = None
        self.total_delayed_minutes_today: float = 0.0
        self.last_sleep_check_date: date | None = None
        self.re_sleep_attempt_time: datetime | None = None
        self.load()

    def save(self):
        """将当前的睡眠状态数据保存到本地存储。"""
        try:
            state = {
                "current_state": self.current_state.name,
                "sleep_buffer_end_time_ts": self.sleep_buffer_end_time.timestamp()
                if self.sleep_buffer_end_time
                else None,
                "total_delayed_minutes_today": self.total_delayed_minutes_today,
                "last_sleep_check_date_str": self.last_sleep_check_date.isoformat()
                if self.last_sleep_check_date
                else None,
                "re_sleep_attempt_time_ts": self.re_sleep_attempt_time.timestamp()
                if self.re_sleep_attempt_time
                else None,
            }
            local_storage["schedule_sleep_state"] = state
            logger.debug(f"已保存睡眠上下文: {state}")
        except Exception as e:
            logger.error(f"保存睡眠上下文失败: {e}")

    def load(self):
        """从本地存储加载并解析睡眠状态。"""
        try:
            state = local_storage["schedule_sleep_state"]
            if not (state and isinstance(state, dict)):
                logger.info("未找到本地睡眠上下文，使用默认值。")
                return

            state_name = state.get("current_state")
            if state_name and hasattr(SleepState, state_name):
                self.current_state = SleepState[state_name]

            end_time_ts = state.get("sleep_buffer_end_time_ts")
            if end_time_ts:
                self.sleep_buffer_end_time = datetime.fromtimestamp(end_time_ts)

            re_sleep_ts = state.get("re_sleep_attempt_time_ts")
            if re_sleep_ts:
                self.re_sleep_attempt_time = datetime.fromtimestamp(re_sleep_ts)

            self.total_delayed_minutes_today = state.get("total_delayed_minutes_today", 0.0)

            date_str = state.get("last_sleep_check_date_str")
            if date_str:
                self.last_sleep_check_date = datetime.fromisoformat(date_str).date()

            logger.info(f"成功从本地存储加载睡眠上下文: {state}")
        except Exception as e:
            logger.warning(f"加载睡眠上下文失败，将使用默认值: {e}")
