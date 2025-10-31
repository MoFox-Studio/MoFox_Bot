# MessageRecv 类备份 - 已从 message.py 中移除
# 备份日期: 2025-10-31
# 此类已被 DatabaseMessages 完全取代

# MessageRecv 类已被移除
# 现在所有消息处理都使用 DatabaseMessages
# 如果需要从消息字典创建 DatabaseMessages，请使用 message_processor.process_message_from_dict()
#
# 历史参考: MessageRecv 曾经是接收消息的包装类，现已被 DatabaseMessages 完全取代
# 迁移完成日期: 2025-10-31

"""
# 以下是已删除的 MessageRecv 类（保留作为参考）
class MessageRecv:
    接收消息类 - DatabaseMessages 的轻量级包装器
    
    这个类现在主要作为适配器层,处理外部消息格式并内部使用 DatabaseMessages。
    保留此类是为了向后兼容性和处理 message_segment 的异步逻辑。
"""

    def __init__(self, message_dict: dict[str, Any]):
        """从MessageCQ的字典初始化

        Args:
            message_dict: MessageCQ序列化后的字典
        """
        # 保留原始消息信息用于某些场景
        self.message_info = BaseMessageInfo.from_dict(message_dict.get("message_info", {}))
        self.message_segment = Seg.from_dict(message_dict.get("message_segment", {}))
        self.raw_message = message_dict.get("raw_message")
        
        # 处理状态(在process()之前临时使用)
        self._processing_state = {
            "is_emoji": False,
            "has_emoji": False,
            "is_picid": False,
            "has_picid": False,
            "is_voice": False,
            "is_video": False,
            "is_mentioned": None,
            "is_at": False,
            "priority_mode": "interest",
            "priority_info": None,
        }
        
        self.chat_stream = None
        self.reply = None
        self.processed_plain_text = message_dict.get("processed_plain_text", "")
        
        # 解析additional_config中的notice信息
        self.is_notify = False
        self.is_public_notice = False
        self.notice_type = None
        if self.message_info.additional_config and isinstance(self.message_info.additional_config, dict):
            self.is_notify = self.message_info.additional_config.get("is_notice", False)
            self.is_public_notice = self.message_info.additional_config.get("is_public_notice", False)
            self.notice_type = self.message_info.additional_config.get("notice_type")
    
    # 兼容性属性 - 代理到 _processing_state
    @property
    def is_emoji(self) -> bool:
        return self._processing_state["is_emoji"]
    
    @is_emoji.setter
    def is_emoji(self, value: bool):
        self._processing_state["is_emoji"] = value
    
    @property
    def has_emoji(self) -> bool:
        return self._processing_state["has_emoji"]
    
    @has_emoji.setter
    def has_emoji(self, value: bool):
        self._processing_state["has_emoji"] = value
    
    @property
    def is_picid(self) -> bool:
        return self._processing_state["is_picid"]
    
    @is_picid.setter  
    def is_picid(self, value: bool):
        self._processing_state["is_picid"] = value
    
    @property
    def has_picid(self) -> bool:
        return self._processing_state["has_picid"]
    
    @has_picid.setter
    def has_picid(self, value: bool):
        self._processing_state["has_picid"] = value
    
    @property
    def is_voice(self) -> bool:
        return self._processing_state["is_voice"]
    
    @is_voice.setter
    def is_voice(self, value: bool):
        self._processing_state["is_voice"] = value
    
    @property
    def is_video(self) -> bool:
        return self._processing_state["is_video"]
    
    @is_video.setter
    def is_video(self, value: bool):
        self._processing_state["is_video"] = value
    
    @property
    def is_mentioned(self):
        return self._processing_state["is_mentioned"]
    
    @is_mentioned.setter
    def is_mentioned(self, value):
        self._processing_state["is_mentioned"] = value
    
    @property
    def is_at(self) -> bool:
        return self._processing_state["is_at"]
    
    @is_at.setter
    def is_at(self, value: bool):
        self._processing_state["is_at"] = value
    
    @property
    def priority_mode(self) -> str:
        return self._processing_state["priority_mode"]
    
    @priority_mode.setter
    def priority_mode(self, value: str):
        self._processing_state["priority_mode"] = value
    
    @property
    def priority_info(self):
        return self._processing_state["priority_info"]
    
    @priority_info.setter
    def priority_info(self, value):
        self._processing_state["priority_info"] = value
    
    # 其他常用属性
    interest_value: float = 0.0
    is_command: bool = False
    memorized_times: int = 0
    
    def __post_init__(self):
        """dataclass 初始化后处理"""
        self.key_words = []
        self.key_words_lite = []

    def update_chat_stream(self, chat_stream: "ChatStream"):
        self.chat_stream = chat_stream

    def to_database_message(self) -> "DatabaseMessages":
        """将 MessageRecv 转换为 DatabaseMessages 对象
        
        Returns:
            DatabaseMessages: 数据库消息对象
        """
        import time
        
        message_info = self.message_info
        msg_user_info = getattr(message_info, "user_info", None)
        stream_user_info = getattr(self.chat_stream, "user_info", None) if self.chat_stream else None
        group_info = getattr(self.chat_stream, "group_info", None) if self.chat_stream else None

        message_id = message_info.message_id or ""
        message_time = message_info.time if hasattr(message_info, "time") and message_info.time is not None else time.time()
        is_mentioned = None
        if isinstance(self.is_mentioned, bool):
            is_mentioned = self.is_mentioned
        elif isinstance(self.is_mentioned, int | float):
            is_mentioned = self.is_mentioned != 0

        # 提取用户信息
        user_id = ""
        user_nickname = ""
        user_cardname = None
        user_platform = ""
        if msg_user_info:
            user_id = str(getattr(msg_user_info, "user_id", "") or "")
            user_nickname = getattr(msg_user_info, "user_nickname", "") or ""
            user_cardname = getattr(msg_user_info, "user_cardname", None)
            user_platform = getattr(msg_user_info, "platform", "") or ""
        elif stream_user_info:
            user_id = str(getattr(stream_user_info, "user_id", "") or "")
            user_nickname = getattr(stream_user_info, "user_nickname", "") or ""
            user_cardname = getattr(stream_user_info, "user_cardname", None)
            user_platform = getattr(stream_user_info, "platform", "") or ""

        # 提取聊天流信息
        chat_user_id = str(getattr(stream_user_info, "user_id", "") or "") if stream_user_info else ""
        chat_user_nickname = getattr(stream_user_info, "user_nickname", "") or "" if stream_user_info else ""
        chat_user_cardname = getattr(stream_user_info, "user_cardname", None) if stream_user_info else None
        chat_user_platform = getattr(stream_user_info, "platform", "") or "" if stream_user_info else ""

        group_id = getattr(group_info, "group_id", None) if group_info else None
        group_name = getattr(group_info, "group_name", None) if group_info else None
        group_platform = getattr(group_info, "platform", None) if group_info else None

        # 准备 additional_config
        additional_config_str = None
        try:
            import orjson
            
            additional_config_data = {}
            
            # 首先获取adapter传递的additional_config
            if hasattr(message_info, 'additional_config') and message_info.additional_config:
                if isinstance(message_info.additional_config, dict):
                    additional_config_data = message_info.additional_config.copy()
                elif isinstance(message_info.additional_config, str):
                    try:
                        additional_config_data = orjson.loads(message_info.additional_config)
                    except Exception as e:
                        logger.warning(f"无法解析 additional_config JSON: {e}")
                        additional_config_data = {}
            
            # 添加notice相关标志
            if self.is_notify:
                additional_config_data["is_notice"] = True
                additional_config_data["notice_type"] = self.notice_type or "unknown"
                additional_config_data["is_public_notice"] = bool(self.is_public_notice)
            
            # 添加format_info到additional_config中
            if hasattr(message_info, 'format_info') and message_info.format_info:
                try:
                    format_info_dict = message_info.format_info.to_dict()
                    additional_config_data["format_info"] = format_info_dict
                    logger.debug(f"[message.py] 嵌入 format_info 到 additional_config: {format_info_dict}")
                except Exception as e:
                    logger.warning(f"将 format_info 转换为字典失败: {e}")
            
            # 序列化为JSON字符串
            if additional_config_data:
                additional_config_str = orjson.dumps(additional_config_data).decode("utf-8")
        except Exception as e:
            logger.error(f"准备 additional_config 失败: {e}")

        # 创建数据库消息对象
        db_message = DatabaseMessages(
            message_id=message_id,
            time=float(message_time),
            chat_id=self.chat_stream.stream_id if self.chat_stream else "",
            processed_plain_text=self.processed_plain_text,
            display_message=self.processed_plain_text,
            is_mentioned=is_mentioned,
            is_at=bool(self.is_at) if self.is_at is not None else None,
            is_emoji=bool(self.is_emoji),
            is_picid=bool(self.is_picid),
            is_command=bool(self.is_command),
            is_notify=bool(self.is_notify),
            is_public_notice=bool(self.is_public_notice),
            notice_type=self.notice_type,
            additional_config=additional_config_str,
            user_id=user_id,
            user_nickname=user_nickname,
            user_cardname=user_cardname,
            user_platform=user_platform,
            chat_info_stream_id=self.chat_stream.stream_id if self.chat_stream else "",
            chat_info_platform=self.chat_stream.platform if self.chat_stream else "",
            chat_info_create_time=float(self.chat_stream.create_time) if self.chat_stream else 0.0,
            chat_info_last_active_time=float(self.chat_stream.last_active_time) if self.chat_stream else 0.0,
            chat_info_user_id=chat_user_id,
            chat_info_user_nickname=chat_user_nickname,
            chat_info_user_cardname=chat_user_cardname,
            chat_info_user_platform=chat_user_platform,
            chat_info_group_id=group_id,
            chat_info_group_name=group_name,
            chat_info_group_platform=group_platform,
        )

        # 同步兴趣度等衍生属性
        db_message.interest_value = getattr(self, "interest_value", 0.0)
        setattr(db_message, "should_reply", getattr(self, "should_reply", False))
        setattr(db_message, "should_act", getattr(self, "should_act", False))

        return db_message

    async def process(self) -> None:
        """处理消息内容，生成纯文本和详细文本

        这个方法必须在创建实例后显式调用，因为它包含异步操作。
        """
        self.processed_plain_text = await self._process_message_segments(self.message_segment)

    async def _process_single_segment(self, segment: Seg) -> str:
        """处理单个消息段

        Args:
            segment: 消息段

        Returns:
            str: 处理后的文本
        """
        try:
            if segment.type == "text":
                self.is_picid = False
                self.is_emoji = False
                self.is_video = False
                return segment.data  # type: ignore
            elif segment.type == "at":
                self.is_picid = False
                self.is_emoji = False
                self.is_video = False
                # 处理at消息，格式为"昵称:QQ号"
                if isinstance(segment.data, str) and ":" in segment.data:
                    nickname, qq_id = segment.data.split(":", 1)
                    return f"@{nickname}"
                return f"@{segment.data}" if isinstance(segment.data, str) else "@未知用户"
            elif segment.type == "image":
                # 如果是base64图片数据
                if isinstance(segment.data, str):
                    self.has_picid = True
                    self.is_picid = True
                    self.is_emoji = False
                    self.is_video = False
                    image_manager = get_image_manager()
                    # print(f"segment.data: {segment.data}")
                    _, processed_text = await image_manager.process_image(segment.data)
                    return processed_text
                return "[发了一张图片，网卡了加载不出来]"
            elif segment.type == "emoji":
                self.has_emoji = True
                self.is_emoji = True
                self.is_picid = False
                self.is_voice = False
                self.is_video = False
                if isinstance(segment.data, str):
                    return await get_image_manager().get_emoji_description(segment.data)
                return "[发了一个表情包，网卡了加载不出来]"
            elif segment.type == "voice":
                self.is_picid = False
                self.is_emoji = False
                self.is_voice = True
                self.is_video = False

                # 检查消息是否由机器人自己发送
                if self.message_info and self.message_info.user_info and str(self.message_info.user_info.user_id) == str(global_config.bot.qq_account):
                    logger.info(f"检测到机器人自身发送的语音消息 (User ID: {self.message_info.user_info.user_id})，尝试从缓存获取文本。")
                    if isinstance(segment.data, str):
                        cached_text = consume_self_voice_text(segment.data)
                        if cached_text:
                            logger.info(f"成功从缓存中获取语音文本: '{cached_text[:70]}...'")
                            return f"[语音：{cached_text}]"
                        else:
                            logger.warning("机器人自身语音消息缓存未命中，将回退到标准语音识别。")

                # 标准语音识别流程 (也作为缓存未命中的后备方案)
                if isinstance(segment.data, str):
                    return await get_voice_text(segment.data)
                return "[发了一段语音，网卡了加载不出来]"
            elif segment.type == "mention_bot":
                self.is_picid = False
                self.is_emoji = False
                self.is_voice = False
                self.is_video = False
                self.is_mentioned = float(segment.data)  # type: ignore
                return ""
            elif segment.type == "priority_info":
                self.is_picid = False
                self.is_emoji = False
                self.is_voice = False
                if isinstance(segment.data, dict):
                    # 处理优先级信息
                    self.priority_mode = "priority"
                    self.priority_info = segment.data
                    """
                    {
                        'message_type': 'vip', # vip or normal
                        'message_priority': 1.0, # 优先级，大为优先，float
                    }
                    """
                return ""
            elif segment.type == "file":
                if isinstance(segment.data, dict):
                    file_name = segment.data.get('name', '未知文件')
                    file_size = segment.data.get('size', '未知大小')
                    return f"[文件：{file_name} ({file_size}字节)]"
                return "[收到一个文件]"
            elif segment.type == "video":
                self.is_picid = False
                self.is_emoji = False
                self.is_voice = False
                self.is_video = True
                logger.info(f"接收到视频消息，数据类型: {type(segment.data)}")

                # 检查视频分析功能是否可用
                if not is_video_analysis_available():
                    logger.warning("⚠️ Rust视频处理模块不可用，跳过视频分析")
                    return "[视频]"

                if global_config.video_analysis.enable:
                    logger.info("已启用视频识别,开始识别")
                    if isinstance(segment.data, dict):
                        try:
                            # 从Adapter接收的视频数据
                            video_base64 = segment.data.get("base64")
                            filename = segment.data.get("filename", "video.mp4")

                            logger.info(f"视频文件名: {filename}")
                            logger.info(f"Base64数据长度: {len(video_base64) if video_base64 else 0}")

                            if video_base64:
                                # 解码base64视频数据
                                video_bytes = base64.b64decode(video_base64)
                                logger.info(f"解码后视频大小: {len(video_bytes)} 字节")

                                # 使用video analyzer分析视频
                                video_analyzer = get_video_analyzer()
                                result = await video_analyzer.analyze_video_from_bytes(
                                    video_bytes, filename, prompt=global_config.video_analysis.batch_analysis_prompt
                                )

                                logger.info(f"视频分析结果: {result}")

                                # 返回视频分析结果
                                summary = result.get("summary", "")
                                if summary:
                                    return f"[视频内容] {summary}"
                                else:
                                    return "[已收到视频，但分析失败]"
                            else:
                                logger.warning("视频消息中没有base64数据")
                                return "[收到视频消息，但数据异常]"
                        except Exception as e:
                            logger.error(f"视频处理失败: {e!s}")
                            import traceback

                            logger.error(f"错误详情: {traceback.format_exc()}")
                            return "[收到视频，但处理时出现错误]"
                    else:
                        logger.warning(f"视频消息数据不是字典格式: {type(segment.data)}")
                    return "[发了一个视频，但格式不支持]"
                else:
