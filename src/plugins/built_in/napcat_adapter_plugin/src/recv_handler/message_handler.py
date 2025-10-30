from ...event_types import NapcatEvent
from src.plugin_system.core.event_manager import event_manager
from src.common.logger import get_logger
from ...CONSTS import PLUGIN_NAME

logger = get_logger("napcat_adapter")

from src.plugin_system.apis import config_api
from ..utils import (
    get_group_info,
    get_member_info,
    get_image_base64,
    get_record_detail,
    get_self_info,
    get_message_detail,
)
from .qq_emoji_list import qq_face
from .message_sending import message_send_instance
from . import RealMessageType, MessageType, ACCEPT_FORMAT
from ..video_handler import get_video_downloader
from ..websocket_manager import websocket_manager

import time
import json
import websockets as Server
import base64
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any
import uuid

from maim_message import (
    UserInfo,
    GroupInfo,
    Seg,
    BaseMessageInfo,
    MessageBase,
    TemplateInfo,
    FormatInfo,
)


from ..response_pool import get_response


class MessageHandler:
    def __init__(self):
        self.server_connection: Server.ServerConnection = None
        self.bot_id_list: Dict[int, bool] = {}
        self.plugin_config = None
        # 消息缓冲功能已移除

    def set_plugin_config(self, plugin_config: dict):
        """设置插件配置"""
        self.plugin_config = plugin_config
        # 消息缓冲功能已移除

    async def shutdown(self):
        """关闭消息处理器，清理资源"""
        # 消息缓冲功能已移除

    # 消息缓冲功能已移除

    async def set_server_connection(self, server_connection: Server.ServerConnection) -> None:
        """设置Napcat连接"""
        self.server_connection = server_connection

    def get_server_connection(self) -> Server.ServerConnection:
        """获取当前的服务器连接"""
        # 优先使用直接设置的连接，否则从 websocket_manager 获取
        if self.server_connection:
            return self.server_connection
        return websocket_manager.get_connection()

    async def check_allow_to_chat(
        self,
        user_id: int,
        group_id: Optional[int] = None,
        ignore_bot: Optional[bool] = False,
        ignore_global_list: Optional[bool] = False,
    ) -> bool:
        # sourcery skip: hoist-statement-from-if, merge-else-if-into-elif
        """
        检查是否允许聊天
        Parameters:
            user_id: int: 用户ID
            group_id: int: 群ID
            ignore_bot: bool: 是否忽略机器人检查
            ignore_global_list: bool: 是否忽略全局黑名单检查
        Returns:
            bool: 是否允许聊天
        """
        logger.debug(f"群聊id: {group_id}, 用户id: {user_id}")
        logger.debug("开始检查聊天白名单/黑名单")

        # 使用新的权限管理器检查权限
        if group_id:
            # 检查群聊黑白名单
            group_list_type = config_api.get_plugin_config(self.plugin_config, "features.group_list_type", "blacklist")
            group_list = config_api.get_plugin_config(self.plugin_config, "features.group_list", [])

            if group_list_type == "whitelist":
                if group_id not in group_list:
                    logger.warning("群聊不在白名单中，消息被丢弃")
                    return False
            else:  # blacklist
                if group_id in group_list:
                    logger.warning("群聊在黑名单中，消息被丢弃")
                    return False
        else:
            # 检查私聊黑白名单
            private_list_type = config_api.get_plugin_config(
                self.plugin_config, "features.private_list_type", "blacklist"
            )
            private_list = config_api.get_plugin_config(self.plugin_config, "features.private_list", [])

            if private_list_type == "whitelist":
                if user_id not in private_list:
                    logger.warning("私聊不在白名单中，消息被丢弃")
                    return False
            else:  # blacklist
                if user_id in private_list:
                    logger.warning("私聊在黑名单中，消息被丢弃")
                    return False

        # 检查全局禁止名单
        ban_user_id = config_api.get_plugin_config(self.plugin_config, "features.ban_user_id", [])
        if not ignore_global_list and user_id in ban_user_id:
            logger.warning("用户在全局黑名单中，消息被丢弃")
            return False

        # 检查QQ官方机器人
        ban_qq_bot = config_api.get_plugin_config(self.plugin_config, "features.ban_qq_bot", False)
        if ban_qq_bot and group_id and not ignore_bot:
            logger.debug("开始判断是否为机器人")
            member_info = await get_member_info(self.get_server_connection(), group_id, user_id)
            if member_info:
                is_bot = member_info.get("is_robot")
                if is_bot is None:
                    logger.warning("无法获取用户是否为机器人，默认为不是但是不进行更新")
                else:
                    if is_bot:
                        logger.warning("QQ官方机器人消息拦截已启用，消息被丢弃，新机器人加入拦截名单")
                        self.bot_id_list[user_id] = True
                        return False
                    else:
                        self.bot_id_list[user_id] = False

        return True

    async def handle_raw_message(self, raw_message: dict) -> None:
        # sourcery skip: low-code-quality, remove-unreachable-code
        """
        从Napcat接受的原始消息处理

        Parameters:
            raw_message: dict: 原始消息
        """

        # 添加原始消息调试日志，特别关注message字段
        logger.debug(
            f"收到原始消息: message_type={raw_message.get('message_type')}, message_id={raw_message.get('message_id')}"
        )
        logger.debug(f"原始消息内容: {raw_message.get('message', [])}")

        # 检查是否包含@或video消息段
        message_segments = raw_message.get("message", [])
        if message_segments:
            for i, seg in enumerate(message_segments):
                seg_type = seg.get("type")
                if seg_type in ["at", "video"]:
                    logger.info(f"检测到 {seg_type.upper()} 消息段 [{i}]: {seg}")
                elif seg_type not in ["text", "face", "image"]:
                    logger.warning(f"检测到特殊消息段 [{i}]: type={seg_type}, data={seg.get('data', {})}")

        message_type: str = raw_message.get("message_type")
        message_id: int = raw_message.get("message_id")
        # message_time: int = raw_message.get("time")
        message_time: float = time.time()  # 应可乐要求，现在是float了

        template_info: TemplateInfo = None  # 模板信息，暂时为空，等待启用
        if message_type == MessageType.private:
            sub_type = raw_message.get("sub_type")
            if sub_type == MessageType.Private.friend:
                sender_info: dict = raw_message.get("sender")

                if not await self.check_allow_to_chat(sender_info.get("user_id"), None):
                    return None

                # 发送者用户信息
                user_info: UserInfo = UserInfo(
                    platform=config_api.get_plugin_config(self.plugin_config, "maibot_server.platform_name"),
                    user_id=sender_info.get("user_id"),
                    user_nickname=sender_info.get("nickname"),
                    user_cardname=sender_info.get("card"),
                )

                # 不存在群信息
                group_info: GroupInfo = None
            elif sub_type == MessageType.Private.group:
                """
                本部分暂时不做支持，先放着
                """
                logger.warning("群临时消息类型不支持")
                return None

                sender_info: dict = raw_message.get("sender")

                # 由于临时会话中，Napcat默认不发送成员昵称，所以需要单独获取
                fetched_member_info: dict = await get_member_info(
                    self.get_server_connection(),
                    raw_message.get("group_id"),
                    sender_info.get("user_id"),
                )
                nickname = fetched_member_info.get("nickname") if fetched_member_info else None
                # 发送者用户信息
                user_info: UserInfo = UserInfo(
                    platform=config_api.get_plugin_config(self.plugin_config, "maibot_server.platform_name"),
                    user_id=sender_info.get("user_id"),
                    user_nickname=nickname,
                    user_cardname=None,
                )

                # -------------------这里需要群信息吗？-------------------

                # 获取群聊相关信息，在此单独处理group_name，因为默认发送的消息中没有
                fetched_group_info: dict = await get_group_info(
                    self.get_server_connection(), raw_message.get("group_id")
                )
                group_name = ""
                if fetched_group_info.get("group_name"):
                    group_name = fetched_group_info.get("group_name")

                group_info: GroupInfo = GroupInfo(
                    platform=config_api.get_plugin_config(self.plugin_config, "maibot_server.platform_name"),
                    group_id=raw_message.get("group_id"),
                    group_name=group_name,
                )

            else:
                logger.warning(f"私聊消息类型 {sub_type} 不支持")
                return None
        elif message_type == MessageType.group:
            sub_type = raw_message.get("sub_type")
            if sub_type == MessageType.Group.normal:
                sender_info: dict = raw_message.get("sender")

                if not await self.check_allow_to_chat(sender_info.get("user_id"), raw_message.get("group_id")):
                    return None

                # 发送者用户信息
                user_info: UserInfo = UserInfo(
                    platform=config_api.get_plugin_config(self.plugin_config, "maibot_server.platform_name"),
                    user_id=sender_info.get("user_id"),
                    user_nickname=sender_info.get("nickname"),
                    user_cardname=sender_info.get("card"),
                )

                # 获取群聊相关信息，在此单独处理group_name，因为默认发送的消息中没有
                fetched_group_info = await get_group_info(self.get_server_connection(), raw_message.get("group_id"))
                group_name: str = None
                if fetched_group_info:
                    group_name = fetched_group_info.get("group_name")

                group_info: GroupInfo = GroupInfo(
                    platform=config_api.get_plugin_config(self.plugin_config, "maibot_server.platform_name"),
                    group_id=raw_message.get("group_id"),
                    group_name=group_name,
                )

            else:
                logger.warning(f"群聊消息类型 {sub_type} 不支持")
                return None

        # 处理实际信息
        if not raw_message.get("message"):
            logger.warning("原始消息内容为空")
            return None

        # 获取Seg列表
        seg_message: List[Seg] = await self.handle_real_message(raw_message)
        if not seg_message:
            logger.warning("处理后消息内容为空")
            return None

        # 动态生成 content_format
        content_formats = sorted(list(set(seg.type for seg in seg_message)))
        logger.debug(f"动态生成 content_format: {content_formats}")
        format_info: FormatInfo = FormatInfo(
            content_format=content_formats,
            accept_format=ACCEPT_FORMAT,
        )

        additional_config: dict = {}
        if config_api.get_plugin_config(self.plugin_config, "voice.use_tts"):
            additional_config["allow_tts"] = True

        # 消息信息
        message_info: BaseMessageInfo = BaseMessageInfo(
            platform=config_api.get_plugin_config(self.plugin_config, "maibot_server.platform_name"),
            message_id=message_id,
            time=message_time,
            user_info=user_info,
            group_info=group_info,
            template_info=template_info,
            format_info=format_info,
            additional_config=additional_config,
        )

        # 消息缓冲功能已移除，直接处理消息

        logger.debug(f"准备发送消息到MoFox-Bot，消息段数量: {len(seg_message)}")
        for i, seg in enumerate(seg_message):
            logger.debug(f"消息段 {i}: type={seg.type}, data={str(seg.data)[:100]}...")

        submit_seg: Seg = Seg(
            type="seglist",
            data=seg_message,
        )
        # MessageBase创建
        message_base: MessageBase = MessageBase(
            message_info=message_info,
            message_segment=submit_seg,
            raw_message=raw_message.get("raw_message"),
        )

        logger.debug("发送到Maibot处理信息")
        await message_send_instance.message_send(message_base)

    async def handle_real_message(self, raw_message: dict, in_reply: bool = False) -> List[Seg] | None:
        # sourcery skip: low-code-quality
        """
        处理实际消息
        Parameters:
            real_message: dict: 实际消息
        Returns:
            seg_message: list[Seg]: 处理后的消息段列表
        """
        real_message: list = raw_message.get("message")
        if not real_message:
            return None
        seg_message: List[Seg] = []
        for sub_message in real_message:
            sub_message: dict
            sub_message_type = sub_message.get("type")

            # 添加详细的消息类型调试信息
            logger.debug(f"处理消息段: type={sub_message_type}, data={sub_message.get('data', {})}")

            # 特别关注 at 和 video 消息的识别
            if sub_message_type == "at":
                logger.debug(f"检测到@消息: {sub_message}")
            elif sub_message_type == "video":
                logger.debug(f"检测到VIDEO消息: {sub_message}")
            elif sub_message_type not in ["text", "face", "image", "record"]:
                logger.warning(f"检测到特殊消息类型: {sub_message_type}, 完整消息: {sub_message}")

            match sub_message_type:
                case RealMessageType.text:
                    ret_seg = await self.handle_text_message(sub_message)
                    if ret_seg:
                        await event_manager.trigger_event(
                            NapcatEvent.ON_RECEIVED.TEXT, permission_group=PLUGIN_NAME, message_seg=ret_seg
                        )
                        seg_message.append(ret_seg)
                    else:
                        logger.warning("text处理失败")
                case RealMessageType.face:
                    ret_seg = await self.handle_face_message(sub_message)
                    if ret_seg:
                        await event_manager.trigger_event(
                            NapcatEvent.ON_RECEIVED.FACE, permission_group=PLUGIN_NAME, message_seg=ret_seg
                        )
                        seg_message.append(ret_seg)
                    else:
                        logger.warning("face处理失败或不支持")
                case RealMessageType.reply:
                    if not in_reply:
                        ret_seg = await self.handle_reply_message(sub_message)
                        if ret_seg:
                            await event_manager.trigger_event(
                                NapcatEvent.ON_RECEIVED.REPLY, permission_group=PLUGIN_NAME, message_seg=ret_seg
                            )
                            seg_message += ret_seg
                        else:
                            logger.warning("reply处理失败")
                case RealMessageType.image:
                    logger.debug("开始处理图片消息段")
                    ret_seg = await self.handle_image_message(sub_message)
                    if ret_seg:
                        await event_manager.trigger_event(
                            NapcatEvent.ON_RECEIVED.IMAGE, permission_group=PLUGIN_NAME, message_seg=ret_seg
                        )
                        seg_message.append(ret_seg)
                        logger.debug("图片处理成功，添加到消息段")
                    else:
                        logger.warning("image处理失败")
                    logger.debug("图片消息段处理完成")
                case RealMessageType.record:
                    ret_seg = await self.handle_record_message(sub_message)
                    if ret_seg:
                        await event_manager.trigger_event(
                            NapcatEvent.ON_RECEIVED.RECORD, permission_group=PLUGIN_NAME, message_seg=ret_seg
                        )
                        seg_message.clear()
                        seg_message.append(ret_seg)
                        break  # 使得消息只有record消息
                    else:
                        logger.warning("record处理失败或不支持")
                case RealMessageType.video:
                    logger.debug(f"开始处理VIDEO消息段: {sub_message}")
                    ret_seg = await self.handle_video_message(sub_message)
                    if ret_seg:
                        await event_manager.trigger_event(
                            NapcatEvent.ON_RECEIVED.VIDEO, permission_group=PLUGIN_NAME, message_seg=ret_seg
                        )
                        seg_message.append(ret_seg)
                    else:
                        logger.warning(f"video处理失败，原始消息: {sub_message}")
                case RealMessageType.at:
                    logger.debug(f"开始处理AT消息段: {sub_message}")
                    ret_seg = await self.handle_at_message(
                        sub_message,
                        raw_message.get("self_id"),
                        raw_message.get("group_id"),
                    )
                    if ret_seg:
                        await event_manager.trigger_event(
                            NapcatEvent.ON_RECEIVED.AT, permission_group=PLUGIN_NAME, message_seg=ret_seg
                        )
                        seg_message.append(ret_seg)
                    else:
                        logger.warning(f"at处理失败，原始消息: {sub_message}")
                case RealMessageType.rps:
                    ret_seg = await self.handle_rps_message(sub_message)
                    if ret_seg:
                        await event_manager.trigger_event(
                            NapcatEvent.ON_RECEIVED.RPS, permission_group=PLUGIN_NAME, message_seg=ret_seg
                        )
                        seg_message.append(ret_seg)
                    else:
                        logger.warning("rps处理失败")
                case RealMessageType.dice:
                    ret_seg = await self.handle_dice_message(sub_message)
                    if ret_seg:
                        await event_manager.trigger_event(
                            NapcatEvent.ON_RECEIVED.DICE, permission_group=PLUGIN_NAME, message_seg=ret_seg
                        )
                        seg_message.append(ret_seg)
                    else:
                        logger.warning("dice处理失败")
                case RealMessageType.shake:
                    ret_seg = await self.handle_shake_message(sub_message)
                    if ret_seg:
                        await event_manager.trigger_event(
                            NapcatEvent.ON_RECEIVED.SHAKE, permission_group=PLUGIN_NAME, message_seg=ret_seg
                        )
                        seg_message.append(ret_seg)
                    else:
                        logger.warning("shake处理失败")
                case RealMessageType.share:
                    print(
                        "\n\n哦哦哦噢噢噢哦哦你收到了一个超级无敌SHARE消息，快速速把你刚刚收到的消息截图发到MoFox-Bot群里！！！！\n\n"
                    )
                    logger.warning("暂时不支持链接解析")
                case RealMessageType.forward:
                    messages = await self._get_forward_message(sub_message)
                    if not messages:
                        logger.warning("转发消息内容为空或获取失败")
                        return None
                    ret_seg = await self.handle_forward_message(messages)
                    if ret_seg:
                        seg_message.append(ret_seg)
                    else:
                        logger.warning("转发消息处理失败")
                case RealMessageType.node:
                    print(
                        "\n\n哦哦哦噢噢噢哦哦你收到了一个超级无敌NODE消息，快速速把你刚刚收到的消息截图发到MoFox-Bot群里！！！！\n\n"
                    )
                    logger.warning("不支持转发消息节点解析")
                case RealMessageType.json:
                    ret_seg = await self.handle_json_message(sub_message)
                    if ret_seg:
                        await event_manager.trigger_event(
                            NapcatEvent.ON_RECEIVED.JSON, permission_group=PLUGIN_NAME, message_seg=ret_seg
                        )
                        seg_message.append(ret_seg)
                    else:
                        logger.warning("json处理失败")
                case RealMessageType.file:
                    ret_seg = await self.handle_file_message(sub_message)
                    if ret_seg:
                        # NapcatEvent doesn't have a FILE event yet, so we won't trigger one for now.
                        seg_message.append(ret_seg)
                    else:
                        logger.warning("file处理失败")
                case _:
                    logger.warning(f"未知消息类型: {sub_message_type}")

        logger.debug(f"handle_real_message完成，处理了{len(real_message)}个消息段，生成了{len(seg_message)}个seg")
        return seg_message

    async def handle_text_message(self, raw_message: dict) -> Seg:
        """
        处理纯文本信息
        Parameters:
            raw_message: dict: 原始消息
        Returns:
            seg_data: Seg: 处理后的消息段
        """
        message_data: dict = raw_message.get("data")
        plain_text: str = message_data.get("text")
        return Seg(type="text", data=plain_text)

    async def handle_face_message(self, raw_message: dict) -> Seg | None:
        """
        处理表情消息
        Parameters:
            raw_message: dict: 原始消息
        Returns:
            seg_data: Seg: 处理后的消息段
        """
        message_data: dict = raw_message.get("data")
        face_raw_id: str = str(message_data.get("id"))
        if face_raw_id in qq_face:
            face_content: str = qq_face.get(face_raw_id)
            return Seg(type="text", data=face_content)
        else:
            logger.warning(f"不支持的表情：{face_raw_id}")
            return None

    async def handle_image_message(self, raw_message: dict) -> Seg | None:
        """
        处理图片消息与表情包消息
        Parameters:
            raw_message: dict: 原始消息
        Returns:
            seg_data: Seg: 处理后的消息段
        """
        message_data: dict = raw_message.get("data")
        image_sub_type = message_data.get("sub_type")
        try:
            image_base64 = await get_image_base64(message_data.get("url"))
        except Exception as e:
            logger.error(f"图片消息处理失败: {str(e)}")
            return None
        if image_sub_type == 0:
            """这部分认为是图片"""
            return Seg(type="image", data=image_base64)
        elif image_sub_type not in [4, 9]:
            """这部分认为是表情包"""
            return Seg(type="emoji", data=image_base64)
        else:
            logger.warning(f"不支持的图片子类型：{image_sub_type}")
            return None

    async def handle_at_message(self, raw_message: dict, self_id: int, group_id: int) -> Seg | None:
        # sourcery skip: use-named-expression
        """
        处理at消息
        Parameters:
            raw_message: dict: 原始消息
            self_id: int: 机器人QQ号
            group_id: int: 群号
        Returns:
            seg_data: Seg: 处理后的消息段
        """
        message_data: dict = raw_message.get("data")
        if message_data:
            qq_id = message_data.get("qq")
            if str(self_id) == str(qq_id):
                logger.debug("机器人被at")
                self_info: dict = await get_self_info(self.get_server_connection())
                if self_info:
                    # 返回包含昵称和用户ID的at格式，便于后续处理
                    return Seg(type="at", data=f"{self_info.get('nickname')}:{self_info.get('user_id')}")
                else:
                    return None
            else:
                member_info: dict = await get_member_info(
                    self.get_server_connection(), group_id=group_id, user_id=qq_id
                )
                if member_info:
                    # 返回包含昵称和用户ID的at格式，便于后续处理
                    return Seg(type="at", data=f"{member_info.get('nickname')}:{member_info.get('user_id')}")
                else:
                    return None

    async def handle_record_message(self, raw_message: dict) -> Seg | None:
        """
        处理语音消息
        Parameters:
            raw_message: dict: 原始消息
        Returns:
            seg_data: Seg: 处理后的消息段
        """
        message_data: dict = raw_message.get("data")
        file: str = message_data.get("file")
        if not file:
            logger.warning("语音消息缺少文件信息")
            return None
        try:
            record_detail = await get_record_detail(self.get_server_connection(), file)
            if not record_detail:
                logger.warning("获取语音消息详情失败")
                return None
            audio_base64: str = record_detail.get("base64")
        except Exception as e:
            logger.error(f"语音消息处理失败: {str(e)}")
            return None
        if not audio_base64:
            logger.error("语音消息处理失败，未获取到音频数据")
            return None
        return Seg(type="voice", data=audio_base64)

    async def handle_video_message(self, raw_message: dict) -> Seg | None:
        """
        处理视频消息
        Parameters:
            raw_message: dict: 原始消息
        Returns:
            seg_data: Seg: 处理后的消息段
        """
        message_data: dict = raw_message.get("data")

        # 添加详细的调试信息
        logger.debug(f"视频消息原始数据: {raw_message}")
        logger.debug(f"视频消息数据: {message_data}")

        # QQ视频消息可能包含url或filePath字段
        video_url = message_data.get("url")
        file_path = message_data.get("filePath") or message_data.get("file_path")

        logger.debug(f"视频URL: {video_url}")
        logger.debug(f"视频文件路径: {file_path}")

        # 优先使用本地文件路径，其次使用URL
        video_source = file_path if file_path else video_url

        if not video_source:
            logger.warning("视频消息缺少URL或文件路径信息")
            logger.warning(f"完整消息数据: {message_data}")
            return None

        try:
            # 检查是否为本地文件路径
            if file_path and Path(file_path).exists():
                logger.debug(f"使用本地视频文件: {file_path}")
                # 直接读取本地文件
                with open(file_path, "rb") as f:
                    video_data = f.read()

                # 将视频数据编码为base64用于传输
                video_base64 = base64.b64encode(video_data).decode("utf-8")
                logger.debug(f"视频文件大小: {len(video_data) / (1024 * 1024):.2f} MB")

                # 返回包含详细信息的字典格式
                return Seg(
                    type="video",
                    data={
                        "base64": video_base64,
                        "filename": Path(file_path).name,
                        "size_mb": len(video_data) / (1024 * 1024),
                    },
                )

            elif video_url:
                logger.debug(f"使用视频URL下载: {video_url}")
                # 使用video_handler下载视频
                video_downloader = get_video_downloader()
                download_result = await video_downloader.download_video(video_url)

                if not download_result["success"]:
                    logger.warning(f"视频下载失败: {download_result.get('error', '未知错误')}")
                    logger.warning(f"失败的URL: {video_url}")
                    return None

                # 将视频数据编码为base64用于传输
                video_base64 = base64.b64encode(download_result["data"]).decode("utf-8")
                logger.debug(f"视频下载成功，大小: {len(download_result['data']) / (1024 * 1024):.2f} MB")

                # 返回包含详细信息的字典格式
                return Seg(
                    type="video",
                    data={
                        "base64": video_base64,
                        "filename": download_result.get("filename", "video.mp4"),
                        "size_mb": len(download_result["data"]) / (1024 * 1024),
                        "url": video_url,
                    },
                )

            else:
                logger.warning("既没有有效的本地文件路径，也没有有效的视频URL")
                return None

        except Exception as e:
            logger.error(f"视频消息处理失败: {str(e)}")
            logger.error(f"视频源: {video_source}")
            return None

    async def handle_reply_message(self, raw_message: dict) -> List[Seg] | None:
        # sourcery skip: move-assign-in-block, use-named-expression
        """
        处理回复消息

        """
        raw_message_data: dict = raw_message.get("data")
        message_id: int = None
        if raw_message_data:
            message_id = raw_message_data.get("id")
        else:
            return None
        message_detail: dict = await get_message_detail(self.get_server_connection(), message_id)
        if not message_detail:
            logger.warning("获取被引用的消息详情失败")
            return None
        reply_message = await self.handle_real_message(message_detail, in_reply=True)
        if reply_message is None:
            reply_message = [Seg(type="text", data="(获取发言内容失败)")]
        sender_info: dict = message_detail.get("sender")
        sender_nickname: str = sender_info.get("nickname")
        sender_id = sender_info.get("user_id")
        seg_message: List[Seg] = []
        if not sender_nickname:
            logger.warning("无法获取被引用的人的昵称，返回默认值")
            seg_message.append(Seg(type="text", data="[回复 未知用户："))
        else:
            if sender_id:
                seg_message.append(Seg(type="text", data=f"[回复<{sender_nickname}({sender_id})>："))
            else:
                seg_message.append(Seg(type="text", data=f"[回复<{sender_nickname}>："))
        seg_message += reply_message
        seg_message.append(Seg(type="text", data="]，说："))
        return seg_message

    async def handle_forward_message(self, message_list: list) -> Seg | None:
        """
        递归处理转发消息，并按照动态方式确定图片处理方式
        Parameters:
            message_list: list: 转发消息列表
        """
        handled_message, image_count = await self._handle_forward_message(message_list, 0)
        handled_message: Seg
        image_count: int
        if not handled_message:
            return None

        processed_message: Seg
        if image_count < 5 and image_count > 0:
            # 处理图片数量小于5的情况，此时解析图片为base64
            logger.debug("图片数量小于5，开始解析图片为base64")
            processed_message = await self._recursive_parse_image_seg(handled_message, True)
        elif image_count > 0:
            logger.debug("图片数量大于等于5，开始解析图片为占位符")
            # 处理图片数量大于等于5的情况，此时解析图片为占位符
            processed_message = await self._recursive_parse_image_seg(handled_message, False)
        else:
            # 处理没有图片的情况，此时直接返回
            logger.debug("没有图片，直接返回")
            processed_message = handled_message

        # 添加转发消息提示
        forward_hint = Seg(type="text", data="这是一条转发消息：\n")
        return Seg(type="seglist", data=[forward_hint, processed_message])

    async def handle_dice_message(self, raw_message: dict) -> Seg:
        message_data: dict = raw_message.get("data", {})
        res = message_data.get("result", "")
        return Seg(type="text", data=f"[扔了一个骰子，点数是{res}]")

    async def handle_shake_message(self, raw_message: dict) -> Seg:
        return Seg(type="text", data="[向你发送了窗口抖动，现在你的屏幕猛烈地震了一下！]")

    async def handle_json_message(self, raw_message: dict) -> Seg:
        """
        处理JSON消息
        Parameters:
            raw_message: dict: 原始消息
        Returns:
            seg_data: Seg: 处理后的消息段
        """
        message_data: dict = raw_message.get("data", {})
        json_data = message_data.get("data", "")

        # 检查JSON消息格式
        if not message_data or "data" not in message_data:
            logger.warning("JSON消息格式不正确")
            return Seg(type="json", data=json.dumps(message_data))

        try:
            # 尝试将json_data解析为Python对象
            nested_data = json.loads(json_data)

            # 检查是否是机器人自己上传文件的回声
            if self._is_file_upload_echo(nested_data):
                logger.info("检测到机器人发送文件的回声消息，将作为文件消息处理")
                # 从回声消息中提取文件信息
                file_info = self._extract_file_info_from_echo(nested_data)
                if file_info:
                    # 构建一个与普通文件消息格式相同的字典
                    file_message_dict = {"type": "file", "data": file_info}
                    return await self.handle_file_message(file_message_dict)

            # 检查是否是QQ小程序分享消息
            if "app" in nested_data and "com.tencent.miniapp" in str(nested_data.get("app", "")):
                logger.debug("检测到QQ小程序分享消息，开始提取信息")

                # 提取目标字段
                extracted_info = {}

                # 提取 meta.detail_1 中的信息
                meta = nested_data.get("meta", {})
                detail_1 = meta.get("detail_1", {})

                if detail_1:
                    extracted_info["title"] = detail_1.get("title", "")
                    extracted_info["desc"] = detail_1.get("desc", "")
                    qqdocurl = detail_1.get("qqdocurl", "")

                    # 从qqdocurl中提取b23.tv短链接
                    if qqdocurl and "b23.tv" in qqdocurl:
                        # 查找b23.tv链接的起始位置
                        start_pos = qqdocurl.find("https://b23.tv/")
                        if start_pos != -1:
                            # 提取从https://b23.tv/开始的部分
                            b23_part = qqdocurl[start_pos:]
                            # 查找第一个?的位置，截取到?之前
                            question_pos = b23_part.find("?")
                            if question_pos != -1:
                                extracted_info["short_url"] = b23_part[:question_pos]
                            else:
                                extracted_info["short_url"] = b23_part
                        else:
                            extracted_info["short_url"] = qqdocurl
                    else:
                        extracted_info["short_url"] = qqdocurl

                # 如果成功提取到关键信息，返回格式化的文本
                if extracted_info.get("title") or extracted_info.get("desc") or extracted_info.get("short_url"):
                    content_parts = []

                    if extracted_info.get("title"):
                        content_parts.append(f"来源: {extracted_info['title']}")

                    if extracted_info.get("desc"):
                        content_parts.append(f"标题: {extracted_info['desc']}")

                    if extracted_info.get("short_url"):
                        content_parts.append(f"链接: {extracted_info['short_url']}")

                    formatted_content = "\n".join(content_parts)
                    return Seg(
                        type="text",
                        data=f"这是一条小程序分享消息，可以根据来源，考虑使用对应解析工具\n{formatted_content}",
                    )

            # 检查是否是音乐分享 (QQ音乐类型)
            if nested_data.get("view") == "music" and "com.tencent.music" in str(nested_data.get("app", "")):
                meta = nested_data.get("meta", {})
                music = meta.get("music", {})
                if music:
                    tag = music.get("tag", "未知来源")
                    logger.debug(f"检测到【{tag}】音乐分享消息 (music view)，开始提取信息")

                    title = music.get("title", "未知歌曲")
                    desc = music.get("desc", "未知艺术家")
                    jump_url = music.get("jumpUrl", "")
                    preview_url = music.get("preview", "")

                    artist = "未知艺术家"
                    song_title = title

                    if "网易云音乐" in tag:
                        artist = desc
                    elif "QQ音乐" in tag:
                        if " - " in title:
                            parts = title.split(" - ", 1)
                            song_title = parts[0]
                            artist = parts[1]
                        else:
                            artist = desc

                    formatted_content = (
                        f"这是一张来自【{tag}】的音乐分享卡片：\n"
                        f"歌曲: {song_title}\n"
                        f"艺术家: {artist}\n"
                        f"跳转链接: {jump_url}\n"
                        f"封面图: {preview_url}"
                    )
                    return Seg(type="text", data=formatted_content)

            # 检查是否是新闻/图文分享 (网易云音乐可能伪装成这种)
            elif nested_data.get("view") == "news" and "com.tencent.tuwen" in str(nested_data.get("app", "")):
                meta = nested_data.get("meta", {})
                news = meta.get("news", {})
                if news and "网易云音乐" in news.get("tag", ""):
                    tag = news.get("tag")
                    logger.debug(f"检测到【{tag}】音乐分享消息 (news view)，开始提取信息")

                    title = news.get("title", "未知歌曲")
                    desc = news.get("desc", "未知艺术家")
                    jump_url = news.get("jumpUrl", "")
                    preview_url = news.get("preview", "")

                    formatted_content = (
                        f"这是一张来自【{tag}】的音乐分享卡片：\n"
                        f"标题: {title}\n"
                        f"描述: {desc}\n"
                        f"跳转链接: {jump_url}\n"
                        f"封面图: {preview_url}"
                    )
                    return Seg(type="text", data=formatted_content)

            # 如果没有提取到关键信息，返回None
            return None

        except json.JSONDecodeError:
            # 如果解析失败，我们假设它不是我们关心的任何一种结构化JSON，
            # 而是普通的文本或者无法解析的格式。
            logger.debug(f"无法将data字段解析为JSON: {json_data}")
            return None
        except Exception as e:
            logger.error(f"处理JSON消息时发生未知错误: {e}")
            return None

    async def handle_file_message(self, raw_message: dict) -> Seg | None:
        """
        处理文件消息
        Parameters:
            raw_message: dict: 原始消息
        Returns:
            seg_data: Seg: 处理后的消息段
        """
        message_data: dict = raw_message.get("data")
        if not message_data:
            logger.warning("文件消息缺少 data 字段")
            return None

        # 提取文件信息
        file_name = message_data.get("file")
        file_size = message_data.get("file_size")
        file_id = message_data.get("file_id")

        logger.info(f"收到文件消息: name={file_name}, size={file_size}, id={file_id}")

        # 将文件信息打包成字典
        file_data = {
            "name": file_name,
            "size": file_size,
            "id": file_id,
        }

        return Seg(type="file", data=file_data)

    def _is_file_upload_echo(self, nested_data: Any) -> bool:
        """检查一个JSON对象是否是机器人自己上传文件的回声消息"""
        if not isinstance(nested_data, dict):
            return False

        # 检查 'app' 和 'meta' 字段是否存在
        if "app" not in nested_data or "meta" not in nested_data:
            return False

        # 检查 'app' 字段是否包含 'com.tencent.miniapp'
        if "com.tencent.miniapp" not in str(nested_data.get("app", "")):
            return False

        # 检查 'meta' 内部的 'detail_1' 的 'busi_id' 是否为 '1014'
        meta = nested_data.get("meta", {})
        detail_1 = meta.get("detail_1", {})
        if detail_1.get("busi_id") == "1014":
            return True

        return False

    def _extract_file_info_from_echo(self, nested_data: dict) -> Optional[dict]:
        """从文件上传的回声消息中提取文件信息"""
        try:
            meta = nested_data.get("meta", {})
            detail_1 = meta.get("detail_1", {})
            
            # 文件名在 'desc' 字段
            file_name = detail_1.get("desc")
            
            # 文件大小在 'summary' 字段，格式为 "大小：1.7MB"
            summary = detail_1.get("summary", "")
            file_size_str = summary.replace("大小：", "").strip() # 移除前缀和空格
            
            # QQ API有时返回的大小不标准，这里我们只提取它给的字符串
            # 实际大小已经由Napcat在发送时记录，这里主要是为了保持格式一致
            
            if file_name and file_size_str:
                return {"file": file_name, "file_size": file_size_str, "file_id": None} # file_id在回声中不可用
        except Exception as e:
            logger.error(f"从文件回声中提取信息失败: {e}")
            
        return None
        
    async def handle_rps_message(self, raw_message: dict) -> Seg:
        message_data: dict = raw_message.get("data", {})
        res = message_data.get("result", "")
        if res == "1":
            shape = "布"
        elif res == "2":
            shape = "剪刀"
        else:
            shape = "石头"
        return Seg(type="text", data=f"[发送了一个魔法猜拳表情，结果是：{shape}]")

    async def _recursive_parse_image_seg(self, seg_data: Seg, to_image: bool) -> Seg:
        # sourcery skip: merge-else-if-into-elif
        if to_image:
            if seg_data.type == "seglist":
                new_seg_list = []
                for i_seg in seg_data.data:
                    parsed_seg = await self._recursive_parse_image_seg(i_seg, to_image)
                    new_seg_list.append(parsed_seg)
                return Seg(type="seglist", data=new_seg_list)
            elif seg_data.type == "image":
                image_url = seg_data.data
                try:
                    encoded_image = await get_image_base64(image_url)
                except Exception as e:
                    logger.error(f"图片处理失败: {str(e)}")
                    return Seg(type="text", data="[图片]")
                return Seg(type="image", data=encoded_image)
            elif seg_data.type == "emoji":
                image_url = seg_data.data
                try:
                    encoded_image = await get_image_base64(image_url)
                except Exception as e:
                    logger.error(f"图片处理失败: {str(e)}")
                    return Seg(type="text", data="[表情包]")
                return Seg(type="emoji", data=encoded_image)
            else:
                logger.debug(f"不处理类型: {seg_data.type}")
                return seg_data
        else:
            if seg_data.type == "seglist":
                new_seg_list = []
                for i_seg in seg_data.data:
                    parsed_seg = await self._recursive_parse_image_seg(i_seg, to_image)
                    new_seg_list.append(parsed_seg)
                return Seg(type="seglist", data=new_seg_list)
            elif seg_data.type == "image":
                return Seg(type="text", data="[图片]")
            elif seg_data.type == "emoji":
                return Seg(type="text", data="[动画表情]")
            else:
                logger.debug(f"不处理类型: {seg_data.type}")
                return seg_data

    async def _handle_forward_message(self, message_list: list, layer: int) -> Tuple[Seg, int] | Tuple[None, int]:
        # sourcery skip: low-code-quality
        """
        递归处理实际转发消息
        Parameters:
            message_list: list: 转发消息列表，首层对应messages字段，后面对应content字段
            layer: int: 当前层级
        Returns:
            seg_data: Seg: 处理后的消息段
            image_count: int: 图片数量
        """
        seg_list: List[Seg] = []
        image_count = 0
        if message_list is None:
            return None, 0
        for sub_message in message_list:
            sub_message: dict
            sender_info: dict = sub_message.get("sender")
            user_nickname: str = sender_info.get("nickname", "QQ用户")
            user_nickname_str = f"【{user_nickname}】:"
            break_seg = Seg(type="text", data="\n")
            message_of_sub_message_list: List[Dict[str, Any]] = sub_message.get("message")
            if not message_of_sub_message_list:
                logger.warning("转发消息内容为空")
                continue
            message_of_sub_message = message_of_sub_message_list[0]
            if message_of_sub_message.get("type") == RealMessageType.forward:
                if layer >= 3:
                    full_seg_data = Seg(
                        type="text",
                        data=("--" * layer) + f"【{user_nickname}】:【转发消息】\n",
                    )
                else:
                    sub_message_data = message_of_sub_message.get("data")
                    if not sub_message_data:
                        continue
                    contents = sub_message_data.get("content")
                    seg_data, count = await self._handle_forward_message(contents, layer + 1)
                    image_count += count
                    head_tip = Seg(
                        type="text",
                        data=("--" * layer) + f"【{user_nickname}】: 合并转发消息内容：\n",
                    )
                    full_seg_data = Seg(type="seglist", data=[head_tip, seg_data])
                seg_list.append(full_seg_data)
            elif message_of_sub_message.get("type") == RealMessageType.text:
                sub_message_data = message_of_sub_message.get("data")
                if not sub_message_data:
                    continue
                text_message = sub_message_data.get("text")
                seg_data = Seg(type="text", data=text_message)
                data_list: List[Any] = []
                if layer > 0:
                    data_list = [
                        Seg(type="text", data=("--" * layer) + user_nickname_str),
                        seg_data,
                        break_seg,
                    ]
                else:
                    data_list = [
                        Seg(type="text", data=user_nickname_str),
                        seg_data,
                        break_seg,
                    ]
                seg_list.append(Seg(type="seglist", data=data_list))
            elif message_of_sub_message.get("type") == RealMessageType.image:
                image_count += 1
                image_data = message_of_sub_message.get("data")
                sub_type = image_data.get("sub_type")
                image_url = image_data.get("url")
                data_list: List[Any] = []
                if sub_type == 0:
                    seg_data = Seg(type="image", data=image_url)
                else:
                    seg_data = Seg(type="emoji", data=image_url)
                if layer > 0:
                    data_list = [
                        Seg(type="text", data=("--" * layer) + user_nickname_str),
                        seg_data,
                        break_seg,
                    ]
                else:
                    data_list = [
                        Seg(type="text", data=user_nickname_str),
                        seg_data,
                        break_seg,
                    ]
                full_seg_data = Seg(type="seglist", data=data_list)
                seg_list.append(full_seg_data)
        return Seg(type="seglist", data=seg_list), image_count

    async def _get_forward_message(self, raw_message: dict) -> Dict[str, Any] | None:
        forward_message_data: Dict = raw_message.get("data")
        if not forward_message_data:
            logger.warning("转发消息内容为空")
            return None
        forward_message_id = forward_message_data.get("id")
        request_uuid = str(uuid.uuid4())
        payload = json.dumps(
            {
                "action": "get_forward_msg",
                "params": {"message_id": forward_message_id},
                "echo": request_uuid,
            }
        )
        try:
            connection = self.get_server_connection()
            if not connection:
                logger.error("没有可用的 WebSocket 连接")
                return None
            await connection.send(payload)
            response: dict = await get_response(request_uuid)
        except TimeoutError:
            logger.error("获取转发消息超时")
            return None
        except Exception as e:
            logger.error(f"获取转发消息失败: {str(e)}")
            return None
        logger.debug(
            f"转发消息原始格式：{json.dumps(response)[:80]}..."
            if len(json.dumps(response)) > 80
            else json.dumps(response)
        )
        response_data: Dict = response.get("data")
        if not response_data:
            logger.warning("转发消息内容为空或获取失败")
            return None
        return response_data.get("messages")

    # 消息缓冲功能已移除


message_handler = MessageHandler()
