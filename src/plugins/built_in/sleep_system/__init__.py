import logging
from pathlib import Path
from typing import Optional

from src.plugin_system import BasePlugin, register_plugin

from .config import SleepSystemConfig
from .state_manager import StateManager
from .sleep_logic import SleepLogic
from .tasks import SleepCycleTask

# 日志配置
logger = logging.getLogger(__name__)

# 全局任务变量
sleep_task: Optional[SleepCycleTask] = None
sleep_logic_instance: Optional[SleepLogic] = None

@register_plugin
class SleepSystemPlugin(BasePlugin):
    plugin_name: str = "sleep_system"
    
    def on_load(self) -> None:
        global sleep_task, sleep_logic_instance
        logger.info("睡眠系统插件正在加载...")
        
        # 1. 加载配置
        config = self.get_config(self.plugin_name, SleepSystemConfig)
        
        # 2. 初始化状态管理器
        state_file = Path(f"data/{self.plugin_name}_state.json")
        state_manager = StateManager(state_file_path=state_file)
        
        # 3. 初始化核心逻辑
        sleep_logic_instance = SleepLogic(config=config, state_manager=state_manager)
        
        # 4. 初始化并启动定时任务
        sleep_task = SleepCycleTask(sleep_logic=sleep_logic_instance, interval_seconds=30)
        sleep_task.start()
        
        logger.info("睡眠系统插件加载完成，定时任务已启动。")

    def on_unload(self) -> None:
        global sleep_task, sleep_logic_instance
        logger.info("睡眠系统插件正在卸载...")
        if sleep_task:
            sleep_task.stop()
        sleep_logic_instance = None
        logger.info("睡眠系统插件已卸载，定时任务已停止。")
