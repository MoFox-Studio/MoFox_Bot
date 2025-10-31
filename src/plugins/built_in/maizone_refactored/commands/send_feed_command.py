"""
发送说说命令        await self.send_text(f"收到！正在为你生成关于"{topic or '随机'}"的说说，请稍候...【热重载测试成功】")件
"""


from typing import ClassVar

from src.common.logger import get_logger
from src.plugin_system.base.command_args import CommandArgs
from src.plugin_system.base.plus_command import PlusCommand
from src.plugin_system.utils.permission_decorators import require_permission

from ..services.manager import get_config_getter, get_qzone_service

logger = get_logger("MaiZone.SendFeedCommand")


class SendFeedCommand(PlusCommand):
    """
    响应用户通过 `/send_feed` 命令发送说说的请求。
    测试热重载功能 - 这是一个测试注释，现在应该可以正常工作了！
    """

    command_name: str = "send_feed"
    command_description: str = "发一条QQ空间说说"
    command_aliases: ClassVar[list[str]] = ["发空间"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    @require_permission("plugin.maizone.send_feed")
    async def execute(self, args: CommandArgs) -> tuple[bool, str, bool]:
        """
        执行命令的核心逻辑。
        """

        topic = args.get_remaining()
        stream_id = self.message.chat_stream.stream_id

        await self.send_text(f"收到！正在为你生成关于“{topic or '随机'}”的说说，请稍候...")

        try:
            qzone_service = get_qzone_service()
            result = await qzone_service.send_feed(topic, stream_id)

            if result.get("success"):
                reply_message = f"已经成功发送说说：\n{result.get('message', '')}"
                get_config = get_config_getter()
                if get_config("send.enable_reply", True):
                    await self.send_text(reply_message)
                return True, "发送成功", True
            else:
                await self.send_text(f"哎呀，发送失败了：{result.get('message', '未知错误')}")
                return False, result.get("message", "未知错误"), True

        except Exception as e:
            logger.error(f"执行发送说说命令时发生未知异常: {e},它的类型是:{type(e)}", exc_info=True)
            await self.send_text("呜... 发送过程中好像出了点问题。")
            return False, "命令执行异常", True
