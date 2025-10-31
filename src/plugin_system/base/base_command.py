from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from src.common.data_models.database_data_model import DatabaseMessages
from src.common.logger import get_logger
from src.plugin_system.apis import send_api
from src.plugin_system.base.component_types import ChatType, CommandInfo, ComponentType

if TYPE_CHECKING:
    from src.chat.message_receive.chat_stream import ChatStream

logger = get_logger("base_command")


class BaseCommand(ABC):
    """Command组件基类

    Command是插件的一种组件类型，用于处理命令请求

    子类可以通过类属性定义命令模式：
    - command_pattern: 命令匹配的正则表达式
    - command_help: 命令帮助信息
    - command_examples: 命令使用示例列表
    """

    command_name: str = ""
    """Command组件的名称"""
    command_description: str = ""
    """Command组件的描述"""
    # 默认命令设置
    command_pattern: str = r""
    """命令匹配的正则表达式"""
    chat_type_allow: ChatType = ChatType.ALL
    """允许的聊天类型，默认为所有类型"""

    def __init__(self, message: DatabaseMessages, plugin_config: dict | None = None):
        """初始化Command组件

        Args:
            message: 接收到的消息对象（DatabaseMessages）
            plugin_config: 插件配置字典
        """
        self.message = message
        self.matched_groups: dict[str, str] = {}  # 存储正则表达式匹配的命名组
        self.plugin_config = plugin_config or {}  # 直接存储插件配置字典

        self.log_prefix = "[Command]"
        
        # chat_stream 会在运行时被 bot.py 设置
        self.chat_stream: "ChatStream | None" = None

        # 从类属性获取chat_type_allow设置
        self.chat_type_allow = getattr(self.__class__, "chat_type_allow", ChatType.ALL)

        logger.debug(f"{self.log_prefix} Command组件初始化完成")

        # 验证聊天类型限制
        if not self._validate_chat_type():
            is_group = message.group_info is not None
            logger.warning(
                f"{self.log_prefix} Command '{self.command_name}' 不支持当前聊天类型: "
                f"{'群聊' if is_group else '私聊'}, 允许类型: {self.chat_type_allow.value}"
            )

    def set_matched_groups(self, groups: dict[str, str]) -> None:
        """设置正则表达式匹配的命名组

        Args:
            groups: 正则表达式匹配的命名组
        """
        self.matched_groups = groups

    def _validate_chat_type(self) -> bool:
        """验证当前聊天类型是否允许执行此Command

        Returns:
            bool: 如果允许执行返回True，否则返回False
        """
        if self.chat_type_allow == ChatType.ALL:
            return True

        # 检查是否为群聊消息（DatabaseMessages使用group_info来判断）
        is_group = self.message.group_info is not None

        if self.chat_type_allow == ChatType.GROUP and is_group:
            return True
        elif self.chat_type_allow == ChatType.PRIVATE and not is_group:
            return True
        else:
            return False

    def is_chat_type_allowed(self) -> bool:
        """检查当前聊天类型是否允许执行此Command

        这是一个公开的方法，供外部调用检查聊天类型限制

        Returns:
            bool: 如果允许执行返回True，否则返回False
        """
        return self._validate_chat_type()

    @abstractmethod
    async def execute(self) -> tuple[bool, str | None, bool]:
        """执行Command的抽象方法，子类必须实现

        Returns:
            Tuple[bool, Optional[str], bool]: (是否执行成功, 可选的回复消息, 是否拦截消息 不进行 后续处理)
        """
        pass

    def get_config(self, key: str, default=None):
        """获取插件配置值，使用嵌套键访问

        Args:
            key: 配置键名，使用嵌套访问如 "section.subsection.key"
            default: 默认值

        Returns:
            Any: 配置值或默认值
        """
        if not self.plugin_config:
            return default

        # 支持嵌套键访问
        keys = key.split(".")
        current = self.plugin_config

        for k in keys:
            if isinstance(current, dict) and k in current:
                current = current[k]
            else:
                return default

        return current

    async def send_text(self, content: str, reply_to: str = "") -> bool:
        """发送回复消息

        Args:
            content: 回复内容
            reply_to: 回复消息，格式为"发送者:消息内容"

        Returns:
            bool: 是否发送成功
        """
        # 获取聊天流信息
        if not self.chat_stream or not hasattr(self.chat_stream, "stream_id"):
            logger.error(f"{self.log_prefix} 缺少聊天流或stream_id")
            return False

        return await send_api.text_to_stream(text=content, stream_id=self.chat_stream.stream_id, reply_to=reply_to)

    async def send_type(
        self, message_type: str, content: str, display_message: str = "", typing: bool = False, reply_to: str = ""
    ) -> bool:
        """发送指定类型的回复消息到当前聊天环境

        Args:
            message_type: 消息类型，如"text"、"image"、"emoji"等
            content: 消息内容
            display_message: 显示消息（可选）
            typing: 是否显示正在输入
            reply_to: 回复消息，格式为"发送者:消息内容"

        Returns:
            bool: 是否发送成功
        """
        # 获取聊天流信息
        if not self.chat_stream or not hasattr(self.chat_stream, "stream_id"):
            logger.error(f"{self.log_prefix} 缺少聊天流或stream_id")
            return False

        return await send_api.custom_to_stream(
            message_type=message_type,
            content=content,
            stream_id=self.chat_stream.stream_id,
            display_message=display_message,
            typing=typing,
            reply_to=reply_to,
        )

    async def send_command(
        self, command_name: str, args: dict | None = None, display_message: str = "", storage_message: bool = True
    ) -> bool:
        """发送命令消息

        Args:
            command_name: 命令名称
            args: 命令参数
            display_message: 显示消息
            storage_message: 是否存储消息到数据库

        Returns:
            bool: 是否发送成功
        """
        try:
            # 获取聊天流信息
            if not self.chat_stream or not hasattr(self.chat_stream, "stream_id"):
                logger.error(f"{self.log_prefix} 缺少聊天流或stream_id")
                return False

            # 构造命令数据
            command_data = {"name": command_name, "args": args or {}}

            success = await send_api.command_to_stream(
                command=command_data,
                stream_id=self.chat_stream.stream_id,
                storage_message=storage_message,
                display_message=display_message,
            )

            if success:
                logger.info(f"{self.log_prefix} 成功发送命令: {command_name}")
            else:
                logger.error(f"{self.log_prefix} 发送命令失败: {command_name}")

            return success

        except Exception as e:
            logger.error(f"{self.log_prefix} 发送命令时出错: {e}")
            return False

    async def send_emoji(self, emoji_base64: str) -> bool:
        """发送表情包

        Args:
            emoji_base64: 表情包的base64编码

        Returns:
            bool: 是否发送成功
        """
        if not self.chat_stream or not hasattr(self.chat_stream, "stream_id"):
            logger.error(f"{self.log_prefix} 缺少聊天流或stream_id")
            return False

        return await send_api.emoji_to_stream(emoji_base64, self.chat_stream.stream_id)

    async def send_image(self, image_base64: str) -> bool:
        """发送图片

        Args:
            image_base64: 图片的base64编码

        Returns:
            bool: 是否发送成功
        """
        if not self.chat_stream or not hasattr(self.chat_stream, "stream_id"):
            logger.error(f"{self.log_prefix} 缺少聊天流或stream_id")
            return False

        return await send_api.image_to_stream(image_base64, self.chat_stream.stream_id)

    @classmethod
    def get_command_info(cls) -> "CommandInfo":
        """从类属性生成CommandInfo

        Args:
            name: Command名称，如果不提供则使用类名
            description: Command描述，如果不提供则使用类文档字符串

        Returns:
            CommandInfo: 生成的Command信息对象
        """
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
