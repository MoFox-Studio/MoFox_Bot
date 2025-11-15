"""
MaiZone（麦麦空间）- 重构版
"""

import asyncio
from pathlib import Path

from src.common.logger import get_logger
from src.plugin_system import BasePlugin, ComponentInfo, register_plugin
from src.plugin_system.base.component_types import PermissionNodeField
from src.plugin_system.base.config_types import ConfigField

# 全局背景任务集合
_background_tasks = set()

from .actions.read_feed_action import ReadFeedAction
from .actions.send_feed_action import SendFeedAction
from .commands.send_feed_command import SendFeedCommand
from .services.content_service import ContentService
from .services.cookie_service import CookieService
from .services.image_service import ImageService
from .services.manager import register_service
from .services.monitor_service import MonitorService
from .services.qzone_service import QZoneService
from .services.reply_tracker_service import ReplyTrackerService
from .services.scheduler_service import SchedulerService

logger = get_logger("MaiZone.Plugin")


@register_plugin
class MaiZoneRefactoredPlugin(BasePlugin):
    plugin_name: str = "MaiZoneRefactored"
    plugin_version: str = "3.0.0"
    plugin_author: str = "Kilo Code"
    plugin_description: str = "重构版的MaiZone插件"
    config_file_name: str = "config.toml"
    enable_plugin: bool = True
    dependencies: list[str] = []
    python_dependencies: list[str] = []

    config_schema: dict = {
        "plugin": {"enable": ConfigField(type=bool, default=True, description="是否启用插件")},
        "models": {
            "text_model": ConfigField(type=str, default="maizone", description="生成文本的模型名称"),
            "siliconflow_apikey": ConfigField(type=str, default="", description="硅基流动AI生图API密钥"),
        },
        "send": {
            "permission": ConfigField(type=list, default=[], description="发送权限QQ号列表"),
            "permission_type": ConfigField(type=str, default="whitelist", description="权限类型"),
            "enable_image": ConfigField(type=bool, default=False, description="是否启用说说配图"),
            "enable_ai_image": ConfigField(type=bool, default=False, description="是否启用AI生成配图"),
            "enable_reply": ConfigField(type=bool, default=True, description="完成后是否回复"),
            "ai_image_number": ConfigField(type=int, default=1, description="AI生成图片数量"),
            "image_number": ConfigField(type=int, default=1, description="本地配图数量（1-9张）"),
            "image_directory": ConfigField(
                type=str, default=(Path(__file__).parent / "images").as_posix(), description="图片存储目录"
            ),
        },
        "read": {
            "permission": ConfigField(type=list, default=[], description="阅读权限QQ号列表"),
            "permission_type": ConfigField(type=str, default="blacklist", description="权限类型"),
            "read_number": ConfigField(type=int, default=5, description="一次读取的说说数量"),
            "like_possibility": ConfigField(type=float, default=1.0, description="点赞概率"),
            "comment_possibility": ConfigField(type=float, default=0.3, description="评论概率"),
        },
        "monitor": {
            "enable_auto_monitor": ConfigField(type=bool, default=False, description="是否启用自动监控"),
            "interval_minutes": ConfigField(type=int, default=10, description="监控间隔分钟数"),
            "enable_auto_reply": ConfigField(type=bool, default=False, description="是否启用自动回复自己说说的评论"),
        },
        "schedule": {
            "enable_schedule": ConfigField(type=bool, default=False, description="是否启用定时发送"),
            "random_interval_min_minutes": ConfigField(type=int, default=120, description="随机间隔分钟数下限"),
            "random_interval_max_minutes": ConfigField(type=int, default=135, description="随机间隔分钟数上限"),
            "forbidden_hours_start": ConfigField(type=int, default=2, description="禁止发送的开始小时(24小时制)"),
            "forbidden_hours_end": ConfigField(type=int, default=6, description="禁止发送的结束小时(24小时制)"),
        },
        "cookie": {
            "http_fallback_host": ConfigField(
                type=str, default="127.0.0.1", description="备用Cookie获取服务的主机地址"
            ),
            "http_fallback_port": ConfigField(type=int, default=9999, description="备用Cookie获取服务的端口"),
            "napcat_token": ConfigField(type=str, default="", description="Napcat服务的认证Token（可选）"),
        },
    }

    permission_nodes: list[PermissionNodeField] = [
        PermissionNodeField(node_name="send_feed", description="是否可以使用机器人发送QQ空间说说"),
        PermissionNodeField(node_name="read_feed", description="是否可以使用机器人读取QQ空间说说"),
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    async def on_plugin_loaded(self):
        """插件加载完成后的回调，初始化服务并启动后台任务"""
        # --- 创建并注册所有服务实例 ---
        content_service = ContentService(self.get_config)
        image_service = ImageService(self.get_config)
        cookie_service = CookieService(self.get_config)
        reply_tracker_service = ReplyTrackerService()

        qzone_service = QZoneService(
            self.get_config,
            content_service,
            image_service,
            cookie_service,
            reply_tracker_service,
        )
        scheduler_service = SchedulerService(self.get_config, qzone_service)
        monitor_service = MonitorService(self.get_config, qzone_service)

        register_service("qzone", qzone_service)
        register_service("reply_tracker", reply_tracker_service)
        register_service("get_config", self.get_config)

        logger.info("MaiZone重构版插件服务已注册。")

        # --- 启动后台任务 ---
        task1 = asyncio.create_task(scheduler_service.start())
        _background_tasks.add(task1)
        task1.add_done_callback(_background_tasks.discard)

        task2 = asyncio.create_task(monitor_service.start())
        _background_tasks.add(task2)
        task2.add_done_callback(_background_tasks.discard)

        logger.info("MaiZone后台监控和定时任务已启动。")

    def get_plugin_components(self) -> list[tuple[ComponentInfo, type]]:
        return [
            (SendFeedAction.get_action_info(), SendFeedAction),
            (ReadFeedAction.get_action_info(), ReadFeedAction),
            (SendFeedCommand.get_plus_command_info(), SendFeedCommand),
        ]
