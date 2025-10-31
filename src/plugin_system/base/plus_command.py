"""增强版命令处理器

提供更简单易用的命令处理方式，无需手写正则表达式
"""

import re
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from src.common.data_models.database_data_model import DatabaseMessages
from src.common.logger import get_logger
from src.config.config import global_config
from src.plugin_system.apis import send_api
from src.plugin_system.base.base_command import BaseCommand
from src.plugin_system.base.command_args import CommandArgs
from src.plugin_system.base.component_types import ChatType, ComponentType, PlusCommandInfo

if TYPE_CHECKING:
    from src.chat.message_receive.chat_stream import ChatStream

logger = get_logger("plus_command")


class PlusCommand(ABC):
    """增强版命令基类

    提供更简单的命令定义方式，无需手写正则表达式

    子类只需要定义：
    - command_name: 命令名称
    - command_description: 命令描述
    - command_aliases: 命令别名列表（可选）
    - priority: 优先级（可选，数字越大优先级越高）
    - chat_type_allow: 允许的聊天类型（可选）
    - intercept_message: 是否拦截消息（可选）
    """

    # 子类需要定义的属性
    command_name: str = ""
    """命令名称，如 'echo'"""

    command_description: str = ""
    """命令描述"""

    command_aliases: list[str] = []
    """命令别名列表，如 ['say', 'repeat']"""

    priority: int = 0
    """命令优先级，数字越大优先级越高"""

    chat_type_allow: ChatType = ChatType.ALL
    """允许的聊天类型"""

    intercept_message: bool = False
    """是否拦截消息，不进行后续处理"""

    def __init__(self, message: DatabaseMessages, plugin_config: dict | None = None):
        """初始化命令组件

        Args:
            message: 接收到的消息对象（DatabaseMessages）
            plugin_config: 插件配置字典
        """
        self.message = message
        self.plugin_config = plugin_config or {}
        self.log_prefix = "[PlusCommand]"

        # chat_stream 会在运行时被 bot.py 设置
        self.chat_stream: "ChatStream | None" = None

        # 解析命令参数
        self._parse_command()

        # 验证聊天类型限制
        if not self._validate_chat_type():
            is_group = message.group_info is not None
            logger.warning(
                f"{self.log_prefix} 命令 '{self.command_name}' 不支持当前聊天类型: "
                f"{'群聊' if is_group else '私聊'}, 允许类型: {self.chat_type_allow.value}"
            )

    def _parse_command(self) -> None:
        """解析命令和参数"""
        if not hasattr(self.message, "processed_plain_text") or not self.message.processed_plain_text:
            self.args = CommandArgs("")
            return

        plain_text = self.message.processed_plain_text.strip()

        # 获取配置的命令前缀
        prefixes = global_config.command.command_prefixes

        # 检查是否以任何前缀开头
        matched_prefix = None
        for prefix in prefixes:
            if plain_text.startswith(prefix):
                matched_prefix = prefix
                break

        if not matched_prefix:
            self.args = CommandArgs("")
            return

        # 移除前缀
        command_part = plain_text[len(matched_prefix) :].strip()

        # 分离命令名和参数
        parts = command_part.split(None, 1)
        if not parts:
            self.args = CommandArgs("")
            return

        command_word = parts[0].lower()
        args_text = parts[1] if len(parts) > 1 else ""

        # 检查命令名是否匹配
        all_commands = [self.command_name.lower()] + [alias.lower() for alias in self.command_aliases]
        if command_word not in all_commands:
            self.args = CommandArgs("")
            return

        # 创建参数对象
        self.args = CommandArgs(args_text)

    def _validate_chat_type(self) -> bool:
        """验证当前聊天类型是否允许执行此命令

        Returns:
            bool: 如果允许执行返回True，否则返回False
        """
        if self.chat_type_allow == ChatType.ALL:
            return True

        # 检查是否为群聊消息（DatabaseMessages使用group_info判断）
        is_group = self.message.group_info is not None

        if self.chat_type_allow == ChatType.GROUP and is_group:
            return True
        elif self.chat_type_allow == ChatType.PRIVATE and not is_group:
            return True
        else:
            return False

    def is_chat_type_allowed(self) -> bool:
        """检查当前聊天类型是否允许执行此命令

        Returns:
            bool: 如果允许执行返回True，否则返回False
        """
        return self._validate_chat_type()

    def is_command_match(self) -> bool:
        """检查当前消息是否匹配此命令

        Returns:
            bool: 如果匹配返回True
        """
        return not self.args.is_empty or self._is_exact_command_call()

    def _is_exact_command_call(self) -> bool:
        """检查是否是精确的命令调用（无参数）"""
        if not self.message.processed_plain_text:
            return False

        plain_text = self.message.processed_plain_text.strip()

        # 获取配置的命令前缀
        prefixes = global_config.command.command_prefixes

        # 检查每个前缀
        for prefix in prefixes:
            if plain_text.startswith(prefix):
                command_part = plain_text[len(prefix) :].strip()
                all_commands = [self.command_name.lower()] + [alias.lower() for alias in self.command_aliases]
                if command_part.lower() in all_commands:
                    return True

        return False

    @abstractmethod
    async def execute(self, args: CommandArgs) -> tuple[bool, str | None, bool]:
        """执行命令的抽象方法，子类必须实现

        Args:
            args: 解析后的命令参数

        Returns:
            Tuple[bool, Optional[str], bool]: (是否执行成功, 可选的回复消息, 是否拦截消息)
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
    def get_plus_command_info(cls) -> "PlusCommandInfo":
        """从类属性生成PlusCommandInfo

        Returns:
            PlusCommandInfo: 生成的增强命令信息对象
        """
        if "." in cls.command_name:
            logger.error(f"命令名称 '{cls.command_name}' 包含非法字符 '.'，请使用下划线替代")
            raise ValueError(f"命令名称 '{cls.command_name}' 包含非法字符 '.'，请使用下划线替代")

        return PlusCommandInfo(
            name=cls.command_name,
            component_type=ComponentType.PLUS_COMMAND,
            description=cls.command_description,
            command_aliases=getattr(cls, "command_aliases", []),
            priority=getattr(cls, "priority", 0),
            chat_type_allow=getattr(cls, "chat_type_allow", ChatType.ALL),
            intercept_message=getattr(cls, "intercept_message", False),
        )

    @classmethod
    def _generate_command_pattern(cls) -> str:
        """生成命令匹配的正则表达式

        Returns:
            str: 正则表达式字符串
        """
        # 获取所有可能的命令名（主命令名 + 别名）
        all_commands = [cls.command_name, *getattr(cls, "command_aliases", [])]

        # 转义特殊字符并创建选择组
        escaped_commands = [re.escape(cmd) for cmd in all_commands]
        commands_pattern = "|".join(escaped_commands)

        # 获取默认前缀列表（这里先用硬编码，后续可以优化为动态获取）
        default_prefixes = ["/", "!", ".", "#"]
        escaped_prefixes = [re.escape(prefix) for prefix in default_prefixes]
        prefixes_pattern = "|".join(escaped_prefixes)

        # 生成完整的正则表达式
        # 匹配: [前缀][命令名][可选空白][任意参数]
        pattern = f"^(?P<prefix>{prefixes_pattern})(?P<command>{commands_pattern})(?P<args>\\s.*)?$"

        return pattern


class PlusCommandAdapter(BaseCommand):
    """PlusCommand适配器

    将PlusCommand适配到现有的插件系统，继承BaseCommand
    """

    def __init__(self, plus_command_class, message: DatabaseMessages, plugin_config: dict | None = None):
        """初始化适配器

        Args:
            plus_command_class: PlusCommand子类
            message: 消息对象（DatabaseMessages）
            plugin_config: 插件配置
        """
        # 先设置必要的类属性
        self.command_name = plus_command_class.command_name
        self.command_description = plus_command_class.command_description
        self.command_pattern = plus_command_class._generate_command_pattern()
        self.chat_type_allow = getattr(plus_command_class, "chat_type_allow", ChatType.ALL)
        self.priority = getattr(plus_command_class, "priority", 0)
        self.intercept_message = getattr(plus_command_class, "intercept_message", False)

        # 调用父类初始化
        super().__init__(message, plugin_config)

        # 创建PlusCommand实例
        self.plus_command = plus_command_class(message, plugin_config)

    async def execute(self) -> tuple[bool, str | None, bool]:
        """执行命令

        Returns:
            Tuple[bool, Optional[str], bool]: 执行结果
        """
        # 检查命令是否匹配
        if not self.plus_command.is_command_match():
            return False, "命令不匹配", False

        # 检查聊天类型权限
        if not self.plus_command.is_chat_type_allowed():
            return False, "不支持当前聊天类型", self.intercept_message

        # 执行命令
        try:
            return await self.plus_command.execute(self.plus_command.args)
        except Exception as e:
            logger.error(f"执行命令时出错: {e}", exc_info=True)
            return False, f"命令执行出错: {e!s}", self.intercept_message


def create_plus_command_adapter(plus_command_class):
    """创建PlusCommand适配器的工厂函数

    Args:
        plus_command_class: PlusCommand子类

    Returns:
        适配器类
    """

    class AdapterClass(BaseCommand):
        command_name = plus_command_class.command_name
        command_description = plus_command_class.command_description
        command_pattern = plus_command_class._generate_command_pattern()
        chat_type_allow = getattr(plus_command_class, "chat_type_allow", ChatType.ALL)

        def __init__(self, message: DatabaseMessages, plugin_config: dict | None = None):
            super().__init__(message, plugin_config)
            self.plus_command = plus_command_class(message, plugin_config)
            self.priority = getattr(plus_command_class, "priority", 0)
            self.intercept_message = getattr(plus_command_class, "intercept_message", False)

        async def execute(self) -> tuple[bool, str | None, bool]:
            """执行命令"""
            # 从BaseCommand的正则匹配结果中提取参数
            args_text = ""
            if hasattr(self, "matched_groups") and self.matched_groups:
                # 从正则匹配组中获取参数部分
                args_match = self.matched_groups.get("args", "")
                if args_match:
                    args_text = args_match.strip()

            # 创建CommandArgs对象
            command_args = CommandArgs(args_text)

            # 检查聊天类型权限
            if not self.plus_command.is_chat_type_allowed():
                return False, "不支持当前聊天类型", self.intercept_message

            # 执行命令，传递正确解析的参数
            try:
                return await self.plus_command.execute(command_args)
            except Exception as e:
                logger.error(f"执行命令时出错: {e}", exc_info=True)
                return False, f"命令执行出错: {e!s}", self.intercept_message

    return AdapterClass


# 兼容旧的命名
PlusCommandAdapter = create_plus_command_adapter

