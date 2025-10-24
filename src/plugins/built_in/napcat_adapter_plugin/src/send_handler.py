import json
import time
import random
import websockets as Server
import uuid
from maim_message import (
    UserInfo,
    GroupInfo,
    Seg,
    BaseMessageInfo,
    MessageBase,
)
from typing import Dict, Any, Tuple, Optional
from src.plugin_system.apis import config_api

from . import CommandType
from .response_pool import get_response
from src.common.logger import get_logger

logger = get_logger("napcat_adapter")
from .utils import get_image_format, convert_image_to_gif
from .recv_handler.message_sending import message_send_instance
from .websocket_manager import websocket_manager


class SendHandler:
    def __init__(self):
        self.server_connection: Optional[Server.ServerConnection] = None
        self.plugin_config = None

    def set_plugin_config(self, plugin_config: dict):
        """设置插件配置"""
        self.plugin_config = plugin_config

    async def set_server_connection(self, server_connection: Server.ServerConnection) -> None:
        """设置Napcat连接"""
        self.server_connection = server_connection

    def get_server_connection(self) -> Optional[Server.ServerConnection]:
        """获取当前的服务器连接"""
        # 优先使用直接设置的连接，否则从 websocket_manager 获取
        if self.server_connection:
            return self.server_connection
        return websocket_manager.get_connection()

    async def handle_message(self, raw_message_base_dict: dict) -> None:
        raw_message_base: MessageBase = MessageBase.from_dict(raw_message_base_dict)
        message_segment: Seg = raw_message_base.message_segment
        logger.info("接收到来自MoFox-Bot的消息，处理中")
        if message_segment.type == "command":
            logger.info("处理命令")
            return await self.send_command(raw_message_base)
        elif message_segment.type == "adapter_command":
            logger.info("处理适配器命令")
            return await self.handle_adapter_command(raw_message_base)
        else:
            logger.info("处理普通消息")
            return await self.send_normal_message(raw_message_base)

    async def send_normal_message(self, raw_message_base: MessageBase) -> None:
        """
        处理普通消息发送
        """
        logger.info("处理普通信息中")
        message_info: BaseMessageInfo = raw_message_base.message_info
        message_segment: Seg = raw_message_base.message_segment
        group_info: Optional[GroupInfo] = message_info.group_info
        user_info: Optional[UserInfo] = message_info.user_info
        target_id: Optional[int] = None
        action: Optional[str] = None
        id_name: Optional[str] = None
        processed_message: list = []
        try:
            if user_info:
                processed_message = await self.handle_seg_recursive(message_segment, user_info)
        except Exception as e:
            logger.error(f"处理消息时发生错误: {e}")
            return

        if not processed_message:
            logger.critical("现在暂时不支持解析此回复！")
            return None

        if group_info and user_info:
            logger.debug("发送群聊消息")
            target_id = int(group_info.group_id) if group_info.group_id else None
            action = "send_group_msg"
            id_name = "group_id"
        elif user_info:
            logger.debug("发送私聊消息")
            target_id = int(user_info.user_id) if user_info.user_id else None
            action = "send_private_msg"
            id_name = "user_id"
        else:
            logger.error("无法识别的消息类型")
            return
        logger.info("尝试发送到napcat")
        logger.debug(
            f"准备发送到napcat的消息体: action='{action}', {id_name}='{target_id}', message='{processed_message}'"
        )
        response = await self.send_message_to_napcat(
            action,
            {
                id_name: target_id,
                "message": processed_message,
            },
        )
        if response.get("status") == "ok":
            logger.info("消息发送成功")
            qq_message_id = response.get("data", {}).get("message_id")
            await self.message_sent_back(raw_message_base, qq_message_id)
        else:
            logger.warning(f"消息发送失败，napcat返回：{str(response)}")

    async def send_command(self, raw_message_base: MessageBase) -> None:
        """
        处理命令类
        """
        logger.info("处理命令中")
        message_info: BaseMessageInfo = raw_message_base.message_info
        message_segment: Seg = raw_message_base.message_segment
        group_info: Optional[GroupInfo] = message_info.group_info
        seg_data: Dict[str, Any] = message_segment.data if isinstance(message_segment.data, dict) else {}
        command_name: Optional[str] = seg_data.get("name")
        try:
            args = seg_data.get("args", {})
            if not isinstance(args, dict):
                args = {}

            match command_name:
                case CommandType.GROUP_BAN.name:
                    command, args_dict = self.handle_ban_command(args, group_info)
                case CommandType.GROUP_WHOLE_BAN.name:
                    command, args_dict = self.handle_whole_ban_command(args, group_info)
                case CommandType.GROUP_KICK.name:
                    command, args_dict = self.handle_kick_command(args, group_info)
                case CommandType.SEND_POKE.name:
                    command, args_dict = self.handle_poke_command(args, group_info)
                case CommandType.DELETE_MSG.name:
                    command, args_dict = self.delete_msg_command(args)
                case CommandType.AI_VOICE_SEND.name:
                    command, args_dict = self.handle_ai_voice_send_command(args, group_info)
                case CommandType.SET_EMOJI_LIKE.name:
                    command, args_dict = self.handle_set_emoji_like_command(args)
                case CommandType.SEND_AT_MESSAGE.name:
                    command, args_dict = self.handle_at_message_command(args, group_info)
                case CommandType.SEND_LIKE.name:
                    command, args_dict = self.handle_send_like_command(args)
                case _:
                    logger.error(f"未知命令: {command_name}")
                    return
        except Exception as e:
            logger.error(f"处理命令时发生错误: {e}")
            return None

        if not command or not args_dict:
            logger.error("命令或参数缺失")
            return None

        logger.info(f"准备向 Napcat 发送命令: command='{command}', args_dict='{args_dict}'")
        response = await self.send_message_to_napcat(command, args_dict)
        logger.info(f"收到 Napcat 的命令响应: {response}")

        if response.get("status") == "ok":
            logger.info(f"命令 {command_name} 执行成功")
        else:
            logger.warning(f"命令 {command_name} 执行失败，napcat返回：{str(response)}")

    async def handle_adapter_command(self, raw_message_base: MessageBase) -> None:
        """
        处理适配器命令类 - 用于直接向Napcat发送命令并返回结果
        """
        logger.info("处理适配器命令中")
        message_segment: Seg = raw_message_base.message_segment
        seg_data: Dict[str, Any] = message_segment.data if isinstance(message_segment.data, dict) else {}

        try:
            action = seg_data.get("action")
            params = seg_data.get("params", {})
            request_id = seg_data.get("request_id")

            if not action:
                logger.error("适配器命令缺少action参数")
                await self.send_adapter_command_response(
                    raw_message_base, {"status": "error", "message": "缺少action参数"}, request_id
                )
                return

            logger.info(f"执行适配器命令: {action}")

            # 根据action决定处理方式
            if action == "get_cookies":
                # 对于get_cookies，我们需要一个更长的超时时间
                response = await self.send_message_to_napcat(action, params, timeout=40.0)
            else:
                # 对于其他命令，使用默认超时
                response = await self.send_message_to_napcat(action, params)

            # 发送响应回MoFox-Bot
            await self.send_adapter_command_response(raw_message_base, response, request_id)

            if response.get("status") == "ok":
                logger.info(f"适配器命令 {action} 执行成功")
            else:
                logger.warning(f"适配器命令 {action} 执行失败，napcat返回：{str(response)}")
            # 无论成功失败，都记录下完整的响应内容以供调试
            logger.debug(f"适配器命令 {action} 的完整响应: {response}")

        except Exception as e:
            logger.error(f"处理适配器命令时发生错误: {e}")
            error_response = {"status": "error", "message": str(e)}
            await self.send_adapter_command_response(raw_message_base, error_response, seg_data.get("request_id"))

    def get_level(self, seg_data: Seg) -> int:
        if seg_data.type == "seglist":
            return 1 + max(self.get_level(seg) for seg in seg_data.data)
        else:
            return 1

    async def handle_seg_recursive(self, seg_data: Seg, user_info: UserInfo) -> list:
        payload: list = []
        if seg_data.type == "seglist":
            # level = self.get_level(seg_data)  # 给以后可能的多层嵌套做准备，此处不使用
            if not seg_data.data:
                return []
            for seg in seg_data.data:
                payload = await self.process_message_by_type(seg, payload, user_info)
        else:
            payload = await self.process_message_by_type(seg_data, payload, user_info)
        return payload

    async def process_message_by_type(self, seg: Seg, payload: list, user_info: UserInfo) -> list:
        # sourcery skip: reintroduce-else, swap-if-else-branches, use-named-expression
        new_payload = payload
        if seg.type == "reply":
            target_id = seg.data
            target_id = str(target_id)
            if target_id == "notice":
                return payload
            new_payload = self.build_payload(
                payload,
                await self.handle_reply_message(target_id if isinstance(target_id, str) else "", user_info),
                True,
            )
        elif seg.type == "text":
            text = seg.data
            if not text:
                return payload
            new_payload = self.build_payload(
                payload,
                self.handle_text_message(text if isinstance(text, str) else ""),
                False,
            )
        elif seg.type == "face":
            logger.warning("MoFox-Bot 发送了qq原生表情，暂时不支持")
        elif seg.type == "image":
            image = seg.data
            new_payload = self.build_payload(payload, self.handle_image_message(image), False)
        elif seg.type == "emoji":
            emoji = seg.data
            new_payload = self.build_payload(payload, self.handle_emoji_message(emoji), False)
        elif seg.type == "voice":
            voice = seg.data
            new_payload = self.build_payload(payload, self.handle_voice_message(voice), False)
        elif seg.type == "voiceurl":
            voice_url = seg.data
            new_payload = self.build_payload(payload, self.handle_voiceurl_message(voice_url), False)
        elif seg.type == "music":
            song_id = seg.data
            new_payload = self.build_payload(payload, self.handle_music_message(song_id), False)
        elif seg.type == "videourl":
            video_url = seg.data
            new_payload = self.build_payload(payload, self.handle_videourl_message(video_url), False)
        elif seg.type == "file":
            file_path = seg.data
            new_payload = self.build_payload(payload, self.handle_file_message(file_path), False)
        return new_payload

    def build_payload(self, payload: list, addon: dict | list, is_reply: bool = False) -> list:
        # sourcery skip: for-append-to-extend, merge-list-append, simplify-generator
        """构建发送的消息体"""
        if is_reply:
            temp_list = []
            if isinstance(addon, list):
                temp_list.extend(addon)
            else:
                temp_list.append(addon)
            for i in payload:
                if i.get("type") == "reply":
                    logger.debug("检测到多个回复，使用最新的回复")
                    continue
                temp_list.append(i)
            return temp_list
        else:
            if isinstance(addon, list):
                payload.extend(addon)
            else:
                payload.append(addon)
            return payload

    async def handle_reply_message(self, id: str, user_info: UserInfo) -> dict | list:
        """处理回复消息"""
        logger.debug(f"开始处理回复消息，消息ID: {id}")
        reply_seg = {"type": "reply", "data": {"id": id}}

        # 检查是否启用引用艾特功能
        if not config_api.get_plugin_config(self.plugin_config, "features.enable_reply_at", False):
            logger.info("引用艾特功能未启用，仅发送普通回复")
            return reply_seg

        try:
            msg_info_response = await self.send_message_to_napcat("get_msg", {"message_id": id})
            logger.debug(f"获取消息 {id} 的详情响应: {msg_info_response}")

            replied_user_id = None
            if msg_info_response and msg_info_response.get("status") == "ok":
                sender_info = msg_info_response.get("data", {}).get("sender")
                if sender_info:
                    replied_user_id = sender_info.get("user_id")

            # 如果没有获取到被回复者的ID，则直接返回，不进行@
            if not replied_user_id:
                logger.warning(f"无法获取消息 {id} 的发送者信息，跳过 @")
                logger.debug(f"最终返回的回复段: {reply_seg}")
                return reply_seg

            # 根据概率决定是否艾特用户
            if random.random() < config_api.get_plugin_config(self.plugin_config, "features.reply_at_rate", 0.5):
                at_seg = {"type": "at", "data": {"qq": str(replied_user_id)}}
                # 在艾特后面添加一个空格
                text_seg = {"type": "text", "data": {"text": " "}}
                result_seg = [reply_seg, at_seg, text_seg]
                logger.info(f"最终返回的回复段: {result_seg}")
                return result_seg

        except Exception as e:
            logger.error(f"处理引用回复并尝试@时出错: {e}")
            # 出现异常时，只发送普通的回复，避免程序崩溃
            logger.info(f"最终返回的回复段: {reply_seg}")
            return reply_seg

        logger.debug(f"最终返回的回复段: {reply_seg}")
        return reply_seg

    def handle_text_message(self, message: str) -> dict:
        """处理文本消息"""
        return {"type": "text", "data": {"text": message}}

    def handle_image_message(self, encoded_image: str) -> dict:
        """处理图片消息"""
        return {
            "type": "image",
            "data": {
                "file": f"base64://{encoded_image}",
                "subtype": 0,
            },
        }  # base64 编码的图片

    def handle_emoji_message(self, encoded_emoji: str) -> dict:
        """处理表情消息"""
        encoded_image = encoded_emoji
        image_format = get_image_format(encoded_emoji)
        if image_format != "gif":
            encoded_image = convert_image_to_gif(encoded_emoji)
        return {
            "type": "image",
            "data": {
                "file": f"base64://{encoded_image}",
                "subtype": 1,
                "summary": "[动画表情]",
            },
        }

    def handle_voice_message(self, encoded_voice: str) -> dict:
        """处理语音消息"""
        use_tts = False
        if self.plugin_config:
            use_tts = config_api.get_plugin_config(self.plugin_config, "voice.use_tts", False)

        if not use_tts:
            logger.warning("未启用语音消息处理")
            return {}
        if not encoded_voice:
            return {}
        return {
            "type": "record",
            "data": {"file": f"base64://{encoded_voice}"},
        }

    def handle_voiceurl_message(self, voice_url: str) -> dict:
        """处理语音链接消息"""
        return {
            "type": "record",
            "data": {"file": voice_url},
        }

    def handle_music_message(self, song_id: str) -> dict:
        """处理音乐消息"""
        return {
            "type": "music",
            "data": {"type": "163", "id": song_id},
        }

    def handle_videourl_message(self, video_url: str) -> dict:
        """处理视频链接消息"""
        return {
            "type": "video",
            "data": {"file": video_url},
        }

    def handle_file_message(self, file_path: str) -> dict:
        """处理文件消息"""
        return {
            "type": "file",
            "data": {"file": f"file://{file_path}"},
        }

    def delete_msg_command(self, args: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        """处理删除消息命令"""
        return "delete_msg", {"message_id": args["message_id"]}

    def handle_ban_command(self, args: Dict[str, Any], group_info: GroupInfo) -> Tuple[str, Dict[str, Any]]:
        """处理封禁命令

        Args:
            args (Dict[str, Any]): 参数字典
            group_info (GroupInfo): 群聊信息（对应目标群聊）

        Returns:
            Tuple[CommandType, Dict[str, Any]]
        """
        duration: int = int(args["duration"])
        user_id: int = int(args["qq_id"])
        group_id: int = int(group_info.group_id)
        if duration < 0:
            raise ValueError("封禁时间必须大于等于0")
        if not user_id or not group_id:
            raise ValueError("封禁命令缺少必要参数")
        if duration > 2592000:
            raise ValueError("封禁时间不能超过30天")
        return (
            CommandType.GROUP_BAN.value,
            {
                "group_id": group_id,
                "user_id": user_id,
                "duration": duration,
            },
        )

    def handle_whole_ban_command(self, args: Dict[str, Any], group_info: GroupInfo) -> Tuple[str, Dict[str, Any]]:
        """处理全体禁言命令

        Args:
            args (Dict[str, Any]): 参数字典
            group_info (GroupInfo): 群聊信息（对应目标群聊）

        Returns:
            Tuple[CommandType, Dict[str, Any]]
        """
        enable = args["enable"]
        assert isinstance(enable, bool), "enable参数必须是布尔值"
        group_id: int = int(group_info.group_id)
        if group_id <= 0:
            raise ValueError("群组ID无效")
        return (
            CommandType.GROUP_WHOLE_BAN.value,
            {
                "group_id": group_id,
                "enable": enable,
            },
        )

    def handle_kick_command(self, args: Dict[str, Any], group_info: GroupInfo) -> Tuple[str, Dict[str, Any]]:
        """处理群成员踢出命令

        Args:
            args (Dict[str, Any]): 参数字典
            group_info (GroupInfo): 群聊信息（对应目标群聊）

        Returns:
            Tuple[CommandType, Dict[str, Any]]
        """
        user_id: int = int(args["qq_id"])
        group_id: int = int(group_info.group_id)
        if group_id <= 0:
            raise ValueError("群组ID无效")
        if user_id <= 0:
            raise ValueError("用户ID无效")
        return (
            CommandType.GROUP_KICK.value,
            {
                "group_id": group_id,
                "user_id": user_id,
                "reject_add_request": False,  # 不拒绝加群请求
            },
        )

    def handle_poke_command(self, args: Dict[str, Any], group_info: GroupInfo) -> Tuple[str, Dict[str, Any]]:
        """处理戳一戳命令

        Args:
            args (Dict[str, Any]): 参数字典
            group_info (GroupInfo): 群聊信息（对应目标群聊）

        Returns:
            Tuple[CommandType, Dict[str, Any]]
        """
        user_id: int = int(args["qq_id"])
        if group_info is None:
            group_id = None
        else:
            group_id: int = int(group_info.group_id)
            if group_id <= 0:
                raise ValueError("群组ID无效")
        if user_id <= 0:
            raise ValueError("用户ID无效")
        return (
            CommandType.SEND_POKE.value,
            {
                "group_id": group_id,
                "user_id": user_id,
            },
        )

    def handle_set_emoji_like_command(self, args: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        """处理设置表情回应命令

        Args:
            args (Dict[str, Any]): 参数字典


        Returns:
            Tuple[CommandType, Dict[str, Any]]
        """
        logger.info(f"开始处理表情回应命令, 接收到参数: {args}")
        try:
            message_id = int(args["message_id"])
            emoji_id = int(args["emoji_id"])
            set_like = bool(args["set"])
        except (KeyError, ValueError) as e:
            logger.error(f"处理表情回应命令时发生错误: {e}, 原始参数: {args}")
            raise ValueError(f"缺少必需参数或参数类型错误: {e}")

        return (
            CommandType.SET_EMOJI_LIKE.value,
            {"message_id": message_id, "emoji_id": emoji_id, "set": set_like},
        )

    def handle_send_like_command(self, args: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        """
        处理发送点赞命令的逻辑。

        Args:
            args (Dict[str, Any]): 参数字典

        Returns:
            Tuple[CommandType, Dict[str, Any]]
        """
        try:
            user_id: int = int(args["qq_id"])
            times: int = int(args["times"])
        except (KeyError, ValueError):
            raise ValueError("缺少必需参数: qq_id 或 times")

        return (
            CommandType.SEND_LIKE.value,
            {"user_id": user_id, "times": times},
        )

    def handle_ai_voice_send_command(self, args: Dict[str, Any], group_info: GroupInfo) -> Tuple[str, Dict[str, Any]]:
        """
        处理AI语音发送命令的逻辑。
        并返回 NapCat 兼容的 (action, params) 元组。
        """
        if not group_info or not group_info.group_id:
            raise ValueError("AI语音发送命令必须在群聊上下文中使用")
        if not args:
            raise ValueError("AI语音发送命令缺少参数")

        group_id: int = int(group_info.group_id)
        character_id = args.get("character")
        text_content = args.get("text")

        if not character_id or not text_content:
            raise ValueError(f"AI语音发送命令参数不完整: character='{character_id}', text='{text_content}'")

        return (
            CommandType.AI_VOICE_SEND.value,
            {
                "group_id": group_id,
                "text": text_content,
                "character": character_id,
            },
        )

    async def send_message_to_napcat(self, action: str, params: dict, timeout: float = 20.0) -> dict:
        request_uuid = str(uuid.uuid4())
        payload = json.dumps({"action": action, "params": params, "echo": request_uuid})

        # 获取当前连接
        connection = self.get_server_connection()
        if not connection:
            logger.error("没有可用的 Napcat 连接")
            return {"status": "error", "message": "no connection"}

        try:
            await connection.send(payload)
            response = await get_response(request_uuid, timeout=timeout)  # 使用传入的超时时间
        except TimeoutError:
            logger.error(f"发送消息超时（{timeout}秒），未收到响应: action={action}, params={params}")
            return {"status": "error", "message": "timeout"}
        except Exception as e:
            logger.error(f"发送消息失败: {e}")
            return {"status": "error", "message": str(e)}
        return response

    async def message_sent_back(self, message_base: MessageBase, qq_message_id: str) -> None:
        # 修改 additional_config，添加 echo 字段
        if message_base.message_info.additional_config is None:
            message_base.message_info.additional_config = {}

        message_base.message_info.additional_config["echo"] = True

        # 获取原始的 mmc_message_id
        mmc_message_id = message_base.message_info.message_id

        # 修改 message_segment 为 notify 类型
        message_base.message_segment = Seg(
            type="notify", data={"sub_type": "echo", "echo": mmc_message_id, "actual_id": qq_message_id}
        )
        await message_send_instance.message_send(message_base)
        logger.debug("已回送消息ID")
        return

    async def send_adapter_command_response(
        self, original_message: MessageBase, response_data: dict, request_id: str
    ) -> None:
        """
        发送适配器命令响应回MoFox-Bot

        Args:
            original_message: 原始消息
            response_data: 响应数据
            request_id: 请求ID
        """
        try:
            # 修改 additional_config，添加 echo 字段
            if original_message.message_info.additional_config is None:
                original_message.message_info.additional_config = {}

            original_message.message_info.additional_config["echo"] = True

            # 修改 message_segment 为 adapter_response 类型
            original_message.message_segment = Seg(
                type="adapter_response",
                data={"request_id": request_id, "response": response_data, "timestamp": int(time.time() * 1000)},
            )

            await message_send_instance.message_send(original_message)
            logger.debug(f"已发送适配器命令响应，request_id: {request_id}")

        except Exception as e:
            logger.error(f"发送适配器命令响应时出错: {e}")

    def handle_at_message_command(self, args: Dict[str, Any], group_info: GroupInfo) -> Tuple[str, Dict[str, Any]]:
        """处理艾特并发送消息命令

        Args:
            args (Dict[str, Any]): 参数字典, 包含 qq_id 和 text
            group_info (GroupInfo): 群聊信息

        Returns:
            Tuple[str, Dict[str, Any]]: (action, params)
        """
        at_user_id = args.get("qq_id")
        text = args.get("text")

        if not at_user_id or not text:
            raise ValueError("艾特消息命令缺少 qq_id 或 text 参数")

        if not group_info:
            raise ValueError("艾特消息命令必须在群聊上下文中使用")

        message_payload = [
            {"type": "at", "data": {"qq": str(at_user_id)}},
            {"type": "text", "data": {"text": " " + str(text)}},
        ]

        return (
            "send_group_msg",
            {
                "group_id": group_info.group_id,
                "message": message_payload,
            },
        )


send_handler = SendHandler()
