from abc import abstractmethod
from typing import TYPE_CHECKING

from src.common.data_models.database_data_model import DatabaseMessages
from src.common.logger import get_logger
from src.plugin_system.base.component_types import ChatType, CommandInfo, ComponentType
from src.plugin_system.base.plus_command import PlusCommand

if TYPE_CHECKING:
    from src.chat.message_receive.chat_stream import ChatStream

logger = get_logger("base_command")


class BaseCommand(PlusCommand):
    """旧版Command组件基类（兼容层）

    此类作为旧版插件的兼容层，新的插件开发请使用PlusCommand

    子类可以通过类属性定义命令模式：
    - command_pattern: 命令匹配的正则表达式
    """

    # 旧版命令标识
    _is_legacy: bool = True

    command_name: str = ""
    """Command组件的名称"""
    command_description: str = ""
    """Command组件的描述"""
    # 默认命令设置
    command_pattern: str = r""
    """命令匹配的正则表达式"""

    # 用于存储正则匹配组
    matched_groups: dict[str, str] = {}

    def __init__(self, message: DatabaseMessages, plugin_config: dict | None = None):
        """初始化Command组件"""
        # 调用PlusCommand的初始化
        super().__init__(message, plugin_config)

        # 旧版属性兼容
        self.log_prefix = "[Command]"
        self.matched_groups = {}  # 初始化为空

    def set_matched_groups(self, groups: dict[str, str]) -> None:
        """设置正则表达式匹配的命名组"""
        self.matched_groups = groups

    @abstractmethod
    async def execute(self) -> tuple[bool, str | None, bool]:
        """执行Command的抽象方法，子类必须实现

        Returns:
            Tuple[bool, Optional[str], bool]: (是否执行成功, 可选的回复消息, 是否拦截消息)
        """
        pass

    @classmethod
    def get_command_info(cls) -> "CommandInfo":
        """从类属性生成CommandInfo"""
        if "." in cls.command_name:
            logger.error(f"Command名称 '{cls.command_name}' 包含非法字符 '.'，请使用下划线替代")
            raise ValueError(f"Command名称 '{cls.command_name}' 包含非法字符 '.'，请使用下划线替代")
        return CommandInfo(
            name=cls.command_name,
            component_type=ComponentType.COMMAND,
            description=cls.command_description,
            command_pattern=cls.command_pattern,
            chat_type_allow=getattr(cls, "chat_type_allow", ChatType.ALL),
        )
