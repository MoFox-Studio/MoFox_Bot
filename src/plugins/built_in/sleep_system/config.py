from typing import Tuple
from pydantic import BaseModel, Field


class SleepSystemConfig(BaseModel):
    # 睡眠时间段，格式为 (时, 分)
    sleep_time: Tuple[int, int] = Field(default=(23, 0), description="每日固定的入睡时间点")
    wake_up_time: Tuple[int, int] = Field(default=(7, 0), description="每日固定的唤醒时间点")

    # 睡前准备时间（分钟）
    prepare_sleep_duration: int = Field(default=15, ge=5, le=30, description="进入睡眠状态前的准备时间（分钟）")

    # 失眠设置
    insomnia_probability: float = Field(default=0.1, ge=0, le=1, description="在睡眠状态下触发失眠的概率")
    insomnia_duration_minutes: Tuple[int, int] = Field(default=(10, 30), description="失眠状态的持续时间范围（分钟）")

    # 被吵醒设置
    woken_up_cooldown_minutes: int = Field(default=10, description="被吵醒后尝试重新入睡的冷却时间（分钟）")