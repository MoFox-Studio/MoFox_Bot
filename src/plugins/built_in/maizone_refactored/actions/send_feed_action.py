"""
发送说说动作组件
"""

from typing import ClassVar

from src.common.logger import get_logger
from src.plugin_system import ActionActivationType, BaseAction, ChatMode
from src.plugin_system.apis import generator_api
from src.plugin_system.apis.permission_api import permission_api

from ..services.manager import get_qzone_service

logger = get_logger("MaiZone.SendFeedAction")


class SendFeedAction(BaseAction):
    """
    当检测到用户意图是发送说说时，此动作被激活。
    """

    action_name: str = "send_feed"
    action_description: str = "发送一条关于特定主题的说说"
    activation_type: ActionActivationType = ActionActivationType.KEYWORD
    mode_enable: ChatMode = ChatMode.ALL
    activation_keywords: ClassVar[list] = ["发说说", "发空间", "发动态"]

    action_parameters: ClassVar[dict] = {
        "topic": "用户想要发送的说说主题",
        "user_name": "请求你发说说的好友的昵称",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    async def _check_permission(self) -> bool:
        """检查当前用户是否有权限执行此动作"""
        platform = self.chat_stream.platform
        user_id = self.chat_stream.user_info.user_id

        # 使用权限API检查用户是否有发送说说的权限
        return await permission_api.check_permission(platform, user_id, "plugin.maizone.send_feed")

    async def execute(self) -> tuple[bool, str]:
        """
        执行动作的核心逻辑。
        """
        if not await self._check_permission():
            _, reply_set, _ = await generator_api.generate_reply(
                chat_stream=self.chat_stream,
                action_data={"extra_info_block": "无权命令你发送说说，请用符合你人格特点的方式拒绝请求"},
            )
            if reply_set and isinstance(reply_set, list):
                for reply_type, reply_content in reply_set:
                    if reply_type == "text":
                        await self.send_text(reply_content)
            return False, "权限不足"

        topic = self.action_data.get("topic", "")
        stream_id = self.chat_stream.stream_id

        try:
            qzone_service = get_qzone_service()
            result = await qzone_service.send_feed(topic, stream_id)

            if result.get("success"):
                _, reply_set, _ = await generator_api.generate_reply(
                    chat_stream=self.chat_stream,
                    action_data={
                        "extra_info_block": f"你刚刚成功发送了一条关于“{topic or '随机'}”的说说，内容是：{result.get('message', '')}"
                    },
                )
                if reply_set and isinstance(reply_set, list):
                    for reply_type, reply_content in reply_set:
                        if reply_type == "text":
                            await self.send_text(reply_content)
                else:
                    await self.send_text("我发完说说啦，快去看看吧！")
                return True, "发送成功"
            else:
                await self.send_text(f"发送失败了呢，原因好像是：{result.get('message', '未知错误')}")
                return False, result.get("message", "未知错误")

        except Exception as e:
            logger.error(f"执行发送说说动作时发生未知异常: {e}", exc_info=True)
            await self.send_text("糟糕，发送的时候网络好像波动了一下...")
            return False, "动作执行异常"
