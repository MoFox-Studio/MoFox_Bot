import json
import logging
from pathlib import Path
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field
import asyncio

# 设置日志记录
logger = logging.getLogger(__name__)

class SleepState(BaseModel):
    """定义睡眠状态的数据模型"""
    current_state: str = Field(default="AWAKE", description="当前的睡眠状态")
    state_end_time: Optional[float] = Field(default=None, description="当前状态的预计结束时间戳")
    last_updated: float = Field(description="状态最后更新的时间戳")
    metadata: Dict[str, Any] = Field(default={}, description="用于存储额外状态信息的字典")

class StateManager:
    """
    负责睡眠状态的持久化管理。
    将状态以 JSON 格式读/写到本地文件，以降低耦合。
    """
    def __init__(self, state_file_path: Path):
        self.state_file_path = state_file_path
        self._state: Optional[SleepState] = None
        self._lock = asyncio.Lock()
        self._load_state()

    def _load_state(self) -> None:
        """从文件加载状态，如果文件不存在或为空，则创建默认状态"""
        try:
            if self.state_file_path.exists() and self.state_file_path.stat().st_size > 0:
                with open(self.state_file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self._state = SleepState(**data)
                    logger.info(f"睡眠状态已从 {self.state_file_path} 加载。")
            else:
                self._create_default_state()
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(f"无法解析状态文件 {self.state_file_path}: {e}。将创建新的默认状态。")
            self._create_default_state()
        except Exception as e:
            logger.error(f"加载睡眠状态时发生未知错误: {e}")
            self._create_default_state()

    def _create_default_state(self) -> None:
        """创建一个默认的清醒状态"""
        import time
        self._state = SleepState(last_updated=time.time())
        logger.info("未找到现有状态文件，已创建默认的睡眠状态 (AWAKE)。")
        # 立即保存一次，以确保文件被创建
        asyncio.create_task(self.save_state())

    async def get_state(self) -> SleepState:
        """异步获取当前的状态"""
        async with self._lock:
            if self._state is None:
                self._load_state()
            # 此时 _state 必然已被 _load_state 或 _create_default_state 初始化
            assert self._state is not None, "State should be initialized here"
            return self._state.copy(deep=True)

    async def save_state(self, new_state: Optional[SleepState] = None) -> None:
        """
        异步保存当前状态到文件。
        如果提供了 new_state，则先更新内部状态。
        """
        async with self._lock:
            if new_state:
                self._state = new_state

            if self._state is None:
                logger.warning("尝试保存一个空的状态，操作已跳过。")
                return

            try:
                # 确保目录存在
                self.state_file_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.state_file_path, 'w', encoding='utf-8') as f:
                    json.dump(self._state.dict(), f, indent=4, ensure_ascii=False)
                logger.debug(f"睡眠状态已成功保存到 {self.state_file_path}。")
            except Exception as e:
                logger.error(f"保存睡眠状态到 {self.state_file_path} 时失败: {e}")
