import asyncio
import hashlib
import time

from maim_message import GroupInfo, UserInfo
from rich.traceback import install
from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from src.common.data_models.database_data_model import DatabaseMessages
from src.common.database.sqlalchemy_database_api import get_db_session
from src.common.database.sqlalchemy_models import ChatStreams  # 新增导入
from src.common.logger import get_logger
from src.config.config import global_config  # 新增导入

install(extra_lines=3)


logger = get_logger("chat_stream")


class ChatStream:
    """聊天流对象，存储一个完整的聊天上下文"""

    def __init__(
        self,
        stream_id: str,
        platform: str,
        user_info: UserInfo | None = None,
        group_info: GroupInfo | None = None,
        data: dict | None = None,
    ):
        self.stream_id = stream_id
        self.platform = platform
        self.user_info = user_info
        self.group_info = group_info
        self.create_time = data.get("create_time", time.time()) if data else time.time()
        self.last_active_time = data.get("last_active_time", self.create_time) if data else self.create_time
        self.sleep_pressure = data.get("sleep_pressure", 0.0) if data else 0.0
        self.saved = False

        # 创建单流上下文管理器（包含StreamContext）
        from src.chat.message_manager.context_manager import SingleStreamContextManager
        from src.common.data_models.message_manager_data_model import StreamContext
        from src.plugin_system.base.component_types import ChatMode, ChatType

        self.context_manager: SingleStreamContextManager = SingleStreamContextManager(
            stream_id=stream_id,
            context=StreamContext(
                stream_id=stream_id,
                chat_type=ChatType.GROUP if group_info else ChatType.PRIVATE,
                chat_mode=ChatMode.NORMAL,
            ),
        )

        # 基础参数
        self.base_interest_energy = 0.5  # 默认基础兴趣度
        self._focus_energy = 0.5  # 内部存储的focus_energy值
        self.no_reply_consecutive = 0

    def to_dict(self) -> dict:
        """转换为字典格式"""
        return {
            "stream_id": self.stream_id,
            "platform": self.platform,
            "user_info": self.user_info.to_dict() if self.user_info else None,
            "group_info": self.group_info.to_dict() if self.group_info else None,
            "create_time": self.create_time,
            "last_active_time": self.last_active_time,
            "sleep_pressure": self.sleep_pressure,
            "focus_energy": self.focus_energy,
            # 基础兴趣度
            "base_interest_energy": self.base_interest_energy,
            # stream_context基本信息（通过context_manager访问）
            "stream_context_chat_type": self.context_manager.context.chat_type.value,
            "stream_context_chat_mode": self.context_manager.context.chat_mode.value,
            # 统计信息
            "interruption_count": self.context_manager.context.interruption_count,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ChatStream":
        """从字典创建实例"""
        user_info = UserInfo.from_dict(data.get("user_info", {})) if data.get("user_info") else None
        group_info = GroupInfo.from_dict(data.get("group_info", {})) if data.get("group_info") else None

        instance = cls(
            stream_id=data["stream_id"],
            platform=data["platform"],
            user_info=user_info,  # type: ignore
            group_info=group_info,
            data=data,
        )

        # 恢复stream_context信息（通过context_manager访问）
        if "stream_context_chat_type" in data:
            from src.plugin_system.base.component_types import ChatMode, ChatType

            instance.context_manager.context.chat_type = ChatType(data["stream_context_chat_type"])
        if "stream_context_chat_mode" in data:
            from src.plugin_system.base.component_types import ChatMode, ChatType

            instance.context_manager.context.chat_mode = ChatMode(data["stream_context_chat_mode"])

        # 恢复interruption_count信息
        if "interruption_count" in data:
            instance.context_manager.context.interruption_count = data["interruption_count"]

        return instance

    def get_raw_id(self) -> str:
        """获取原始的、未哈希的聊天流ID字符串"""
        if self.group_info:
            return f"{self.platform}:{self.group_info.group_id}:group"
        elif self.user_info:
            return f"{self.platform}:{self.user_info.user_id}:private"
        else:
            return f"{self.platform}:unknown:private"

    def update_active_time(self):
        """更新最后活跃时间"""
        self.last_active_time = time.time()
        self.saved = False

    async def set_context(self, message: DatabaseMessages):
        """设置聊天消息上下文
        
        Args:
            message: DatabaseMessages 对象，直接使用不需要转换
        """
        # 直接使用传入的 DatabaseMessages，设置到上下文中
        self.context_manager.context.set_current_message(message)

        # 设置优先级信息（如果存在）
        priority_mode = getattr(message, "priority_mode", None)
        priority_info = getattr(message, "priority_info", None)
        if priority_mode:
            self.context_manager.context.priority_mode = priority_mode
        if priority_info:
            self.context_manager.context.priority_info = priority_info

        # 调试日志
        logger.debug(
            f"消息上下文已设置 - message_id: {message.message_id}, "
            f"chat_id: {message.chat_id}, "
            f"is_mentioned: {message.is_mentioned}, "
            f"is_emoji: {message.is_emoji}, "
            f"is_picid: {message.is_picid}, "
            f"interest_value: {message.interest_value}"
        )

    def _safe_get_actions(self, message: DatabaseMessages) -> list | None:
        """安全获取消息的actions字段"""
        import json

        try:
            actions = getattr(message, "actions", None)
            if actions is None:
                return None

            # 如果是字符串，尝试解析为JSON
            if isinstance(actions, str):
                try:
                    actions = json.loads(actions)
                except json.JSONDecodeError:
                    logger.warning(f"无法解析actions JSON字符串: {actions}")
                    return None

            # 确保返回列表类型
            if isinstance(actions, list):
                # 过滤掉空值和非字符串元素
                filtered_actions = [action for action in actions if action is not None and isinstance(action, str)]
                return filtered_actions if filtered_actions else None
            else:
                logger.warning(f"actions字段类型不支持: {type(actions)}")
                return None

        except Exception as e:
            logger.warning(f"获取actions字段失败: {e}")
            return None

    async def _calculate_message_interest(self, db_message):
        """计算消息兴趣值并更新消息对象"""
        try:
            from src.chat.interest_system.interest_manager import get_interest_manager

            interest_manager = get_interest_manager()

            if interest_manager.has_calculator():
                # 使用兴趣值计算组件计算
                result = await interest_manager.calculate_interest(db_message)

                if result.success:
                    # 更新消息对象的兴趣值相关字段
                    db_message.interest_value = result.interest_value
                    db_message.should_reply = result.should_reply
                    db_message.should_act = result.should_act

                    logger.debug(
                        f"消息 {db_message.message_id} 兴趣值已更新: {result.interest_value:.3f}, "
                        f"should_reply: {result.should_reply}, should_act: {result.should_act}"
                    )
                else:
                    logger.warning(f"消息 {db_message.message_id} 兴趣值计算失败: {result.error_message}")
                    # 使用默认值
                    db_message.interest_value = 0.3
                    db_message.should_reply = False
                    db_message.should_act = False
            else:
                # 没有兴趣值计算组件，抛出异常
                raise RuntimeError("没有可用的兴趣值计算组件")

        except Exception as e:
            logger.error(f"计算消息兴趣值失败: {e}", exc_info=True)
            # 异常情况下使用默认值
            if hasattr(db_message, "interest_value"):
                db_message.interest_value = 0.3
            if hasattr(db_message, "should_reply"):
                db_message.should_reply = False
            if hasattr(db_message, "should_act"):
                db_message.should_act = False

    def _generate_chat_id(self, message_info) -> str:
        """生成chat_id，基于群组或用户信息"""
        try:
            group_info = getattr(message_info, "group_info", None)
            user_info = getattr(message_info, "user_info", None)

            if group_info and hasattr(group_info, "group_id") and group_info.group_id:
                # 群聊：使用群组ID
                return f"{self.platform}_{group_info.group_id}"
            elif user_info and hasattr(user_info, "user_id") and user_info.user_id:
                # 私聊：使用用户ID
                return f"{self.platform}_{user_info.user_id}_private"
            else:
                # 默认：使用stream_id
                return self.stream_id
        except Exception as e:
            logger.warning(f"生成chat_id失败: {e}")
            return self.stream_id

    @property
    def focus_energy(self) -> float:
        """获取缓存的focus_energy值"""
        if hasattr(self, "_focus_energy"):
            return self._focus_energy
        else:
            return 0.5

    async def calculate_focus_energy(self) -> float:
        """异步计算focus_energy"""
        try:
            # 使用单流上下文管理器获取消息
            all_messages = self.context_manager.get_messages(limit=global_config.chat.max_context_size)

            # 获取用户ID
            user_id = None
            if self.user_info and hasattr(self.user_info, "user_id"):
                user_id = str(self.user_info.user_id)

            # 使用能量管理器计算
            from src.chat.energy_system import energy_manager

            energy = await energy_manager.calculate_focus_energy(
                stream_id=self.stream_id, messages=all_messages, user_id=user_id
            )

            # 更新内部存储
            self._focus_energy = energy

            logger.debug(f"聊天流 {self.stream_id} 能量: {energy:.3f}")
            return energy

        except Exception as e:
            logger.error(f"获取focus_energy失败: {e}", exc_info=True)
            # 返回缓存的值或默认值
            if hasattr(self, "_focus_energy"):
                return self._focus_energy
            else:
                return 0.5

    @focus_energy.setter
    def focus_energy(self, value: float):
        """设置focus_energy值（主要用于初始化或特殊场景）"""
        self._focus_energy = max(0.0, min(1.0, value))

    async def _get_user_relationship_score(self) -> float:
        """获取用户关系分"""
        # 使用统一的评分API
        try:
            from src.plugin_system.apis.scoring_api import scoring_api

            if self.user_info and hasattr(self.user_info, "user_id"):
                user_id = str(self.user_info.user_id)
                relationship_score = await scoring_api.get_user_relationship_score(user_id)
                logger.debug(f"ChatStream {self.stream_id}: 用户关系分 = {relationship_score:.3f}")
                return relationship_score

        except Exception as e:
            logger.warning(f"ChatStream {self.stream_id}: 关系分计算失败: {e}")

        # 默认基础分
        return 0.3


class ChatManager:
    """聊天管理器，管理所有聊天流"""

    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not self._initialized:
            from src.common.data_models.database_data_model import DatabaseMessages

            self.streams: dict[str, ChatStream] = {}  # stream_id -> ChatStream
            self.last_messages: dict[str, DatabaseMessages] = {}  # stream_id -> last_message
            # try:
            # async with get_db_session() as session:
            #     db.connect(reuse_if_open=True)
            #     # 确保 ChatStreams 表存在
            #     session.execute(text("CREATE TABLE IF NOT EXISTS chat_streams (stream_id TEXT PRIMARY KEY, platform TEXT, create_time REAL, last_active_time REAL, user_platform TEXT, user_id TEXT, user_nickname TEXT, user_cardname TEXT, group_platform TEXT, group_id TEXT, group_name TEXT)"))
            #     await session.commit()
            # except Exception as e:
            #     logger.error(f"数据库连接或 ChatStreams 表创建失败: {e}")

            self._initialized = True
            # 在事件循环中启动初始化
            # asyncio.create_task(self._initialize())
            # # 启动自动保存任务
            # asyncio.create_task(self._auto_save_task())

    async def _initialize(self):
        """异步初始化"""
        try:
            await self.load_all_streams()
            logger.info(f"聊天管理器已启动，已加载 {len(self.streams)} 个聊天流")
        except Exception as e:
            logger.error(f"聊天管理器启动失败: {e!s}")

    async def _auto_save_task(self):
        """定期自动保存所有聊天流"""
        while True:
            await asyncio.sleep(300)  # 每5分钟保存一次
            try:
                await self._save_all_streams()
                logger.info("聊天流自动保存完成")
            except Exception as e:
                logger.error(f"聊天流自动保存失败: {e!s}")

    def register_message(self, message: DatabaseMessages):
        """注册消息到聊天流"""
        # 从 DatabaseMessages 提取平台和用户/群组信息
        from maim_message import GroupInfo, UserInfo

        user_info = UserInfo(
            platform=message.user_info.platform,
            user_id=message.user_info.user_id,
            user_nickname=message.user_info.user_nickname,
            user_cardname=message.user_info.user_cardname or ""
        )

        group_info = None
        if message.group_info:
            group_info = GroupInfo(
                platform=message.group_info.group_platform or "",
                group_id=message.group_info.group_id,
                group_name=message.group_info.group_name
            )

        stream_id = self._generate_stream_id(
            message.chat_info.platform,
            user_info,
            group_info,
        )
        self.last_messages[stream_id] = message
        # logger.debug(f"注册消息到聊天流: {stream_id}")

    @staticmethod
    def _generate_stream_id(platform: str, user_info: UserInfo | None, group_info: GroupInfo | None = None) -> str:
        """生成聊天流唯一ID"""
        if not user_info and not group_info:
            raise ValueError("用户信息或群组信息必须提供")

        if group_info:
            # 组合关键信息
            components = [platform, str(group_info.group_id)]
        else:
            components = [platform, str(user_info.user_id), "private"]  # type: ignore

        # 使用SHA-256生成唯一ID
        key = "_".join(components)
        return hashlib.sha256(key.encode()).hexdigest()

    @staticmethod
    def get_stream_id(platform: str, id: str, is_group: bool = True) -> str:
        """获取聊天流ID"""
        components = [platform, id] if is_group else [platform, id, "private"]
        key = "_".join(components)
        return hashlib.sha256(key.encode()).hexdigest()

    async def get_or_create_stream(
        self, platform: str, user_info: UserInfo, group_info: GroupInfo | None = None
    ) -> ChatStream:
        """获取或创建聊天流 - 优化版本使用缓存管理器

        Args:
            platform: 平台标识
            user_info: 用户信息
            group_info: 群组信息（可选）

        Returns:
            ChatStream: 聊天流对象
        """
        # 生成stream_id
        try:
            stream_id = self._generate_stream_id(platform, user_info, group_info)

            # 检查内存中是否存在
            if stream_id in self.streams:
                stream = self.streams[stream_id]

                # 更新用户信息和群组信息
                stream.update_active_time()
                if user_info.platform and user_info.user_id:
                    stream.user_info = user_info
                if group_info:
                    stream.group_info = group_info

                # 检查是否有最后一条消息（现在使用 DatabaseMessages）
                from src.common.data_models.database_data_model import DatabaseMessages
                if stream_id in self.last_messages and isinstance(self.last_messages[stream_id], DatabaseMessages):
                    await stream.set_context(self.last_messages[stream_id])
                else:
                    logger.debug(f"聊天流 {stream_id} 不在最后消息列表中，可能是新创建的或还没有消息")
                return stream

            # 检查数据库中是否存在
            async def _db_find_stream_async(s_id: str):
                async with get_db_session() as session:
                    return (
                        (await session.execute(select(ChatStreams).where(ChatStreams.stream_id == s_id)))
                        .scalars()
                        .first()
                    )

            model_instance = await _db_find_stream_async(stream_id)

            if model_instance:
                # 从 SQLAlchemy 模型转换回 ChatStream.from_dict 期望的格式
                user_info_data = {
                    "platform": model_instance.user_platform,
                    "user_id": model_instance.user_id,
                    "user_nickname": model_instance.user_nickname,
                    "user_cardname": model_instance.user_cardname or "",
                }
                group_info_data = None
                if model_instance and getattr(model_instance, "group_id", None):
                    group_info_data = {
                        "platform": model_instance.group_platform,
                        "group_id": model_instance.group_id,
                        "group_name": model_instance.group_name,
                    }

                data_for_from_dict = {
                    "stream_id": model_instance.stream_id,
                    "platform": model_instance.platform,
                    "user_info": user_info_data,
                    "group_info": group_info_data,
                    "create_time": model_instance.create_time,
                    "last_active_time": model_instance.last_active_time,
                    "energy_value": model_instance.energy_value,
                    "sleep_pressure": model_instance.sleep_pressure,
                }
                stream = ChatStream.from_dict(data_for_from_dict)
                # 更新用户信息和群组信息
                stream.user_info = user_info
                if group_info:
                    stream.group_info = group_info
                stream.update_active_time()
            else:
                # 创建新的聊天流
                stream = ChatStream(
                    stream_id=stream_id,
                    platform=platform,
                    user_info=user_info,
                    group_info=group_info,
                )
        except Exception as e:
            logger.error(f"获取或创建聊天流失败: {e}", exc_info=True)
            raise e

        from src.common.data_models.database_data_model import DatabaseMessages

        if stream_id in self.last_messages and isinstance(self.last_messages[stream_id], DatabaseMessages):
            await stream.set_context(self.last_messages[stream_id])
        else:
            logger.debug(f"聊天流 {stream_id} 不在最后消息列表中，可能是新创建的")

        # 确保 ChatStream 有自己的 context_manager
        if not hasattr(stream, "context_manager") or stream.context_manager is None:
            from src.chat.message_manager.context_manager import SingleStreamContextManager
            from src.common.data_models.message_manager_data_model import StreamContext
            from src.plugin_system.base.component_types import ChatMode, ChatType

            logger.info(f"为 stream {stream_id} 创建新的 context_manager")
            stream.context_manager = SingleStreamContextManager(
                stream_id=stream_id,
                context=StreamContext(
                    stream_id=stream_id,
                    chat_type=ChatType.GROUP if stream.group_info else ChatType.PRIVATE,
                    chat_mode=ChatMode.NORMAL,
                ),
            )
        else:
            logger.info(f"stream {stream_id} 已有 context_manager，跳过创建")

        # 保存到内存和数据库
        self.streams[stream_id] = stream
        await self._save_stream(stream)
        return stream

    async def get_stream(self, stream_id: str) -> ChatStream | None:
        """通过stream_id获取聊天流"""
        from src.common.data_models.database_data_model import DatabaseMessages

        stream = self.streams.get(stream_id)
        if not stream:
            return None
        if stream_id in self.last_messages and isinstance(self.last_messages[stream_id], DatabaseMessages):
            await stream.set_context(self.last_messages[stream_id])
        return stream

    def get_stream_by_info(
        self, platform: str, user_info: UserInfo, group_info: GroupInfo | None = None
    ) -> ChatStream | None:
        """通过信息获取聊天流"""
        stream_id = self._generate_stream_id(platform, user_info, group_info)
        return self.streams.get(stream_id)

    async def get_stream_name(self, stream_id: str) -> str | None:
        """根据 stream_id 获取聊天流名称"""
        stream = await self.get_stream(stream_id)
        if not stream:
            return None

        if stream.group_info and stream.group_info.group_name:
            return stream.group_info.group_name
        elif stream.user_info and stream.user_info.user_nickname:
            return f"{stream.user_info.user_nickname}的私聊"
        else:
            return None

    @staticmethod
    def _prepare_stream_data(stream_data_dict: dict) -> dict:
        """准备聊天流保存数据"""
        user_info_d = stream_data_dict.get("user_info")
        group_info_d = stream_data_dict.get("group_info")

        return {
            "platform": stream_data_dict["platform"],
            "create_time": stream_data_dict["create_time"],
            "last_active_time": stream_data_dict["last_active_time"],
            "user_platform": user_info_d["platform"] if user_info_d else "",
            "user_id": user_info_d["user_id"] if user_info_d else "",
            "user_nickname": user_info_d["user_nickname"] if user_info_d else "",
            "user_cardname": user_info_d.get("user_cardname", "") if user_info_d else None,
            "group_platform": group_info_d["platform"] if group_info_d else "",
            "group_id": group_info_d["group_id"] if group_info_d else "",
            "group_name": group_info_d["group_name"] if group_info_d else "",
            "energy_value": stream_data_dict.get("energy_value", 5.0),
            "sleep_pressure": stream_data_dict.get("sleep_pressure", 0.0),
            "focus_energy": stream_data_dict.get("focus_energy", 0.5),
            # 新增动态兴趣度系统字段
            "base_interest_energy": stream_data_dict.get("base_interest_energy", 0.5),
            "message_interest_total": stream_data_dict.get("message_interest_total", 0.0),
            "message_count": stream_data_dict.get("message_count", 0),
            "action_count": stream_data_dict.get("action_count", 0),
            "reply_count": stream_data_dict.get("reply_count", 0),
            "last_interaction_time": stream_data_dict.get("last_interaction_time", time.time()),
            "consecutive_no_reply": stream_data_dict.get("consecutive_no_reply", 0),
            "interruption_count": stream_data_dict.get("interruption_count", 0),
        }

    @staticmethod
    async def _save_stream(stream: ChatStream):
        """保存聊天流到数据库 - 优化版本使用异步批量写入"""
        if stream.saved:
            return
        stream_data_dict = stream.to_dict()

        # 优先使用新的批量写入器
        try:
            from src.chat.message_manager.batch_database_writer import get_batch_writer

            batch_writer = get_batch_writer()
            if batch_writer.is_running:
                success = await batch_writer.schedule_stream_update(
                    stream_id=stream_data_dict["stream_id"],
                    update_data=ChatManager._prepare_stream_data(stream_data_dict),
                    priority=1,  # 流更新的优先级
                )
                if success:
                    stream.saved = True
                    logger.debug(f"聊天流 {stream.stream_id} 通过批量写入器调度成功")
                    return
                else:
                    logger.warning(f"批量写入器队列已满，使用原始方法: {stream.stream_id}")
            else:
                logger.debug(f"批量写入器未运行，使用原始方法: {stream.stream_id}")

        except Exception as e:
            logger.debug(f"批量写入器保存聊天流失败，使用原始方法: {e}")

        # 尝试使用数据库批量调度器（回退方案1）
        try:
            from src.common.database.db_batch_scheduler import batch_update, get_batch_session

            async with get_batch_session():
                # 使用批量更新
                result = await batch_update(
                    model_class=ChatStreams,
                    conditions={"stream_id": stream_data_dict["stream_id"]},
                    data=ChatManager._prepare_stream_data(stream_data_dict),
                )
                if result and result > 0:
                    stream.saved = True
                    logger.debug(f"聊天流 {stream.stream_id} 通过批量调度器保存成功")
                    return
        except (ImportError, Exception) as e:
            logger.debug(f"批量调度器保存聊天流失败，使用原始方法: {e}")

        # 回退到原始方法（最终方案）
        async def _db_save_stream_async(s_data_dict: dict):
            async with get_db_session() as session:
                user_info_d = s_data_dict.get("user_info")
                group_info_d = s_data_dict.get("group_info")
                fields_to_save = {
                    "platform": s_data_dict["platform"],
                    "create_time": s_data_dict["create_time"],
                    "last_active_time": s_data_dict["last_active_time"],
                    "user_platform": user_info_d["platform"] if user_info_d else "",
                    "user_id": user_info_d["user_id"] if user_info_d else "",
                    "user_nickname": user_info_d["user_nickname"] if user_info_d else "",
                    "user_cardname": user_info_d.get("user_cardname", "") if user_info_d else None,
                    "group_platform": group_info_d["platform"] if group_info_d else "",
                    "group_id": group_info_d["group_id"] if group_info_d else "",
                    "group_name": group_info_d["group_name"] if group_info_d else "",
                    "energy_value": s_data_dict.get("energy_value", 5.0),
                    "sleep_pressure": s_data_dict.get("sleep_pressure", 0.0),
                    "focus_energy": s_data_dict.get("focus_energy", 0.5),
                    # 新增动态兴趣度系统字段
                    "base_interest_energy": s_data_dict.get("base_interest_energy", 0.5),
                    "message_interest_total": s_data_dict.get("message_interest_total", 0.0),
                    "message_count": s_data_dict.get("message_count", 0),
                    "action_count": s_data_dict.get("action_count", 0),
                    "reply_count": s_data_dict.get("reply_count", 0),
                    "last_interaction_time": s_data_dict.get("last_interaction_time", time.time()),
                    "consecutive_no_reply": s_data_dict.get("consecutive_no_reply", 0),
                    "interruption_count": s_data_dict.get("interruption_count", 0),
                }
                if global_config.database.database_type == "sqlite":
                    stmt = sqlite_insert(ChatStreams).values(stream_id=s_data_dict["stream_id"], **fields_to_save)
                    stmt = stmt.on_conflict_do_update(index_elements=["stream_id"], set_=fields_to_save)
                elif global_config.database.database_type == "mysql":
                    stmt = mysql_insert(ChatStreams).values(stream_id=s_data_dict["stream_id"], **fields_to_save)
                    stmt = stmt.on_duplicate_key_update(
                        **{key: value for key, value in fields_to_save.items() if key != "stream_id"}
                    )
                else:
                    stmt = sqlite_insert(ChatStreams).values(stream_id=s_data_dict["stream_id"], **fields_to_save)
                    stmt = stmt.on_conflict_do_update(index_elements=["stream_id"], set_=fields_to_save)
                await session.execute(stmt)
                await session.commit()

        try:
            await _db_save_stream_async(stream_data_dict)
            stream.saved = True
        except Exception as e:
            logger.error(f"保存聊天流 {stream.stream_id} 到数据库失败 (SQLAlchemy): {e}", exc_info=True)

    async def _save_all_streams(self):
        """保存所有聊天流"""
        for stream in self.streams.values():
            await self._save_stream(stream)

    async def load_all_streams(self):
        """从数据库加载所有聊天流"""
        logger.info("正在从数据库加载所有聊天流")

        async def _db_load_all_streams_async():
            loaded_streams_data = []
            async with get_db_session() as session:
                result = await session.execute(select(ChatStreams))
                for model_instance in result.scalars().all():
                    user_info_data = {
                        "platform": model_instance.user_platform,
                        "user_id": model_instance.user_id,
                        "user_nickname": model_instance.user_nickname,
                        "user_cardname": model_instance.user_cardname or "",
                    }
                    group_info_data = None
                    if model_instance and getattr(model_instance, "group_id", None):
                        group_info_data = {
                            "platform": model_instance.group_platform,
                            "group_id": model_instance.group_id,
                            "group_name": model_instance.group_name,
                        }
                    data_for_from_dict = {
                        "stream_id": model_instance.stream_id,
                        "platform": model_instance.platform,
                        "user_info": user_info_data,
                        "group_info": group_info_data,
                        "create_time": model_instance.create_time,
                        "last_active_time": model_instance.last_active_time,
                        "energy_value": model_instance.energy_value,
                        "sleep_pressure": model_instance.sleep_pressure,
                        "focus_energy": getattr(model_instance, "focus_energy", 0.5),
                        # 新增动态兴趣度系统字段 - 使用getattr提供默认值
                        "base_interest_energy": getattr(model_instance, "base_interest_energy", 0.5),
                        "message_interest_total": getattr(model_instance, "message_interest_total", 0.0),
                        "message_count": getattr(model_instance, "message_count", 0),
                        "action_count": getattr(model_instance, "action_count", 0),
                        "reply_count": getattr(model_instance, "reply_count", 0),
                        "last_interaction_time": getattr(model_instance, "last_interaction_time", time.time()),
                        "relationship_score": getattr(model_instance, "relationship_score", 0.3),
                        "consecutive_no_reply": getattr(model_instance, "consecutive_no_reply", 0),
                        "interruption_count": getattr(model_instance, "interruption_count", 0),
                    }
                    loaded_streams_data.append(data_for_from_dict)
                await session.commit()
            return loaded_streams_data

        try:
            all_streams_data_list = await _db_load_all_streams_async()
            self.streams.clear()
            for data in all_streams_data_list:
                stream = ChatStream.from_dict(data)
                stream.saved = True
                self.streams[stream.stream_id] = stream
                # 不在异步加载中设置上下文，避免复杂依赖
                # if stream.stream_id in self.last_messages:
                #     await stream.set_context(self.last_messages[stream.stream_id])

                # 确保 ChatStream 有自己的 context_manager
                if not hasattr(stream, "context_manager") or stream.context_manager is None:
                    from src.chat.message_manager.context_manager import SingleStreamContextManager
                    from src.common.data_models.message_manager_data_model import StreamContext
                    from src.plugin_system.base.component_types import ChatMode, ChatType

                    logger.debug(f"为加载的 stream {stream.stream_id} 创建新的 context_manager")
                    stream.context_manager = SingleStreamContextManager(
                        stream_id=stream.stream_id,
                        context=StreamContext(
                            stream_id=stream.stream_id,
                            chat_type=ChatType.GROUP if stream.group_info else ChatType.PRIVATE,
                            chat_mode=ChatMode.NORMAL,
                        ),
                    )
                else:
                    logger.debug(f"加载的 stream {stream.stream_id} 已有 context_manager")
        except Exception as e:
            logger.error(f"从数据库加载所有聊天流失败 (SQLAlchemy): {e}", exc_info=True)


chat_manager = None


def get_chat_manager():
    global chat_manager
    if chat_manager is None:
        chat_manager = ChatManager()
    return chat_manager
