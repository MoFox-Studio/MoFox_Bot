"""
优化版聊天流 - 实现写时复制机制
避免不必要的深拷贝开销，提升多流并发性能
"""

import time
from typing import TYPE_CHECKING, Any

from maim_message import GroupInfo, UserInfo
from rich.traceback import install

from src.common.logger import get_logger
from src.config.config import global_config

if TYPE_CHECKING:
    from .message import MessageRecv

install(extra_lines=3)

logger = get_logger("optimized_chat_stream")


class SharedContext:
    """共享上下文数据 - 只读数据结构"""

    def __init__(self, stream_id: str, platform: str, user_info: UserInfo, group_info: GroupInfo | None = None):
        self.stream_id = stream_id
        self.platform = platform
        self.user_info = user_info
        self.group_info = group_info
        self.create_time = time.time()
        self._frozen = True

    def __setattr__(self, name, value):
        if hasattr(self, "_frozen") and self._frozen and name not in ["_frozen"]:
            raise AttributeError(f"SharedContext is frozen, cannot modify {name}")
        super().__setattr__(name, value)


class LocalChanges:
    """本地修改跟踪器"""

    def __init__(self):
        self._changes: dict[str, Any] = {}
        self._dirty = False

    def set_change(self, key: str, value: Any):
        """设置修改项"""
        self._changes[key] = value
        self._dirty = True

    def get_change(self, key: str, default: Any = None) -> Any:
        """获取修改项"""
        return self._changes.get(key, default)

    def has_changes(self) -> bool:
        """是否有修改"""
        return self._dirty

    def get_changes(self) -> dict[str, Any]:
        """获取所有修改"""
        return self._changes.copy()

    def clear_changes(self):
        """清除修改记录"""
        self._changes.clear()
        self._dirty = False


class OptimizedChatStream:
    """优化版聊天流 - 使用写时复制机制"""

    def __init__(
        self,
        stream_id: str,
        platform: str,
        user_info: UserInfo,
        group_info: GroupInfo | None = None,
        data: dict | None = None,
    ):
        # 共享的只读数据
        self._shared_context = SharedContext(
            stream_id=stream_id, platform=platform, user_info=user_info, group_info=group_info
        )

        # 本地修改数据
        self._local_changes = LocalChanges()

        # 写时复制标志
        self._copy_on_write = False

        # 基础参数
        self.base_interest_energy = data.get("base_interest_energy", 0.5) if data else 0.5
        self._focus_energy = data.get("focus_energy", 0.5) if data else 0.5
        self.no_reply_consecutive = 0

        # 创建StreamContext（延迟创建）
        self._stream_context = None
        self._context_manager = None

        # 更新活跃时间
        self.update_active_time()

        # 保存标志
        self.saved = False

    @property
    def stream_id(self) -> str:
        return self._shared_context.stream_id

    @property
    def platform(self) -> str:
        return self._shared_context.platform

    @property
    def user_info(self) -> UserInfo:
        return self._shared_context.user_info

    @user_info.setter
    def user_info(self, value: UserInfo):
        """修改用户信息时触发写时复制"""
        self._ensure_copy_on_write()
        # 由于SharedContext是frozen的，我们需要在本地修改中记录
        self._local_changes.set_change("user_info", value)

    @property
    def group_info(self) -> GroupInfo | None:
        if self._local_changes.has_changes() and "group_info" in self._local_changes._changes:
            return self._local_changes.get_change("group_info")
        return self._shared_context.group_info

    @group_info.setter
    def group_info(self, value: GroupInfo | None):
        """修改群组信息时触发写时复制"""
        self._ensure_copy_on_write()
        self._local_changes.set_change("group_info", value)

    @property
    def create_time(self) -> float:
        if self._local_changes.has_changes() and "create_time" in self._local_changes._changes:
            return self._local_changes.get_change("create_time")
        return self._shared_context.create_time

    @property
    def last_active_time(self) -> float:
        return self._local_changes.get_change("last_active_time", self.create_time)

    @last_active_time.setter
    def last_active_time(self, value: float):
        self._local_changes.set_change("last_active_time", value)
        self.saved = False

    @property
    def sleep_pressure(self) -> float:
        return self._local_changes.get_change("sleep_pressure", 0.0)

    @sleep_pressure.setter
    def sleep_pressure(self, value: float):
        self._local_changes.set_change("sleep_pressure", value)
        self.saved = False

    def _ensure_copy_on_write(self):
        """确保写时复制机制生效"""
        if not self._copy_on_write:
            self._copy_on_write = True
            # 深拷贝共享上下文到本地
            logger.debug(f"触发写时复制: {self.stream_id}")

    def _get_effective_user_info(self) -> UserInfo:
        """获取有效的用户信息"""
        if self._local_changes.has_changes() and "user_info" in self._local_changes._changes:
            return self._local_changes.get_change("user_info")
        return self._shared_context.user_info

    def _get_effective_group_info(self) -> GroupInfo | None:
        """获取有效的群组信息"""
        if self._local_changes.has_changes() and "group_info" in self._local_changes._changes:
            return self._local_changes.get_change("group_info")
        return self._shared_context.group_info

    def update_active_time(self):
        """更新最后活跃时间"""
        self.last_active_time = time.time()

    def set_context(self, message: "MessageRecv"):
        """设置聊天消息上下文"""
        # 确保stream_context存在
        if self._stream_context is None:
            self._ensure_copy_on_write()
            self._create_stream_context()

        # 将MessageRecv转换为DatabaseMessages并设置到stream_context
        import json

        from src.common.data_models.database_data_model import DatabaseMessages

        message_info = getattr(message, "message_info", {})
        user_info = getattr(message_info, "user_info", {})
        group_info = getattr(message_info, "group_info", {})

        reply_to = None
        if hasattr(message, "message_segment") and message.message_segment:
            reply_to = self._extract_reply_from_segment(message.message_segment)

        db_message = DatabaseMessages(
            message_id=getattr(message, "message_id", ""),
            time=getattr(message, "time", time.time()),
            chat_id=self._generate_chat_id(message_info),
            reply_to=reply_to,
            interest_value=getattr(message, "interest_value", 0.0),
            key_words=json.dumps(getattr(message, "key_words", []), ensure_ascii=False)
            if getattr(message, "key_words", None)
            else None,
            key_words_lite=json.dumps(getattr(message, "key_words_lite", []), ensure_ascii=False)
            if getattr(message, "key_words_lite", None)
            else None,
            is_mentioned=getattr(message, "is_mentioned", None),
            is_at=getattr(message, "is_at", False),
            is_emoji=getattr(message, "is_emoji", False),
            is_picid=getattr(message, "is_picid", False),
            is_voice=getattr(message, "is_voice", False),
            is_video=getattr(message, "is_video", False),
            is_command=getattr(message, "is_command", False),
            is_notify=getattr(message, "is_notify", False),
            is_public_notice=getattr(message, "is_public_notice", False),
            notice_type=getattr(message, "notice_type", None),
            processed_plain_text=getattr(message, "processed_plain_text", ""),
            display_message=getattr(message, "processed_plain_text", ""),
            priority_mode=getattr(message, "priority_mode", None),
            priority_info=json.dumps(getattr(message, "priority_info", None))
            if getattr(message, "priority_info", None)
            else None,
            additional_config=self._prepare_additional_config(message_info),
            user_id=str(getattr(user_info, "user_id", "")),
            user_nickname=getattr(user_info, "user_nickname", ""),
            user_cardname=getattr(user_info, "user_cardname", None),
            user_platform=getattr(user_info, "platform", ""),
            chat_info_group_id=getattr(group_info, "group_id", None),
            chat_info_group_name=getattr(group_info, "group_name", None),
            chat_info_group_platform=getattr(group_info, "platform", None),
            chat_info_user_id=str(getattr(user_info, "user_id", "")),
            chat_info_user_nickname=getattr(user_info, "user_nickname", ""),
            chat_info_user_cardname=getattr(user_info, "user_cardname", None),
            chat_info_user_platform=getattr(user_info, "platform", ""),
            chat_info_stream_id=self.stream_id,
            chat_info_platform=self.platform,
            chat_info_create_time=self.create_time,
            chat_info_last_active_time=self.last_active_time,
            actions=self._safe_get_actions(message),
            should_reply=getattr(message, "should_reply", False),
        )

        self._stream_context.set_current_message(db_message)
        self._stream_context.priority_mode = getattr(message, "priority_mode", None)
        self._stream_context.priority_info = getattr(message, "priority_info", None)

        logger.debug(
            f"消息数据转移完成 - message_id: {db_message.message_id}, "
            f"chat_id: {db_message.chat_id}, "
            f"interest_value: {db_message.interest_value}"
        )

    def _create_stream_context(self):
        """创建StreamContext"""
        from src.common.data_models.message_manager_data_model import StreamContext
        from src.plugin_system.base.component_types import ChatMode, ChatType

        self._stream_context = StreamContext(
            stream_id=self.stream_id,
            chat_type=ChatType.GROUP if self.group_info else ChatType.PRIVATE,
            chat_mode=ChatMode.NORMAL,
        )

        # 创建单流上下文管理器
        from src.chat.message_manager.context_manager import SingleStreamContextManager

        self._context_manager = SingleStreamContextManager(stream_id=self.stream_id, context=self._stream_context)

    @property
    def stream_context(self):
        """获取StreamContext"""
        if self._stream_context is None:
            self._ensure_copy_on_write()
            self._create_stream_context()
        return self._stream_context

    @property
    def context_manager(self):
        """获取ContextManager"""
        if self._context_manager is None:
            self._ensure_copy_on_write()
            self._create_stream_context()
        return self._context_manager

    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式 - 考虑本地修改"""
        user_info = self._get_effective_user_info()
        group_info = self._get_effective_group_info()

        return {
            "stream_id": self.stream_id,
            "platform": self.platform,
            "user_info": user_info.to_dict() if user_info else None,
            "group_info": group_info.to_dict() if group_info else None,
            "create_time": self.create_time,
            "last_active_time": self.last_active_time,
            "sleep_pressure": self.sleep_pressure,
            "focus_energy": self.focus_energy,
            "base_interest_energy": self.base_interest_energy,
            "stream_context_chat_type": self.stream_context.chat_type.value,
            "stream_context_chat_mode": self.stream_context.chat_mode.value,
            "interruption_count": self.stream_context.interruption_count,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "OptimizedChatStream":
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

        # 恢复stream_context信息
        if "stream_context_chat_type" in data:
            from src.plugin_system.base.component_types import ChatMode, ChatType

            instance.stream_context.chat_type = ChatType(data["stream_context_chat_type"])
        if "stream_context_chat_mode" in data:
            from src.plugin_system.base.component_types import ChatMode, ChatType

            instance.stream_context.chat_mode = ChatMode(data["stream_context_chat_mode"])

        # 恢复interruption_count信息
        if "interruption_count" in data:
            instance.stream_context.interruption_count = data["interruption_count"]

        return instance

    def _prepare_additional_config(self, message_info) -> str | None:
        """
        准备 additional_config，将 format_info 嵌入其中
        
        这个方法模仿 storage.py 中的逻辑，确保 DatabaseMessages 中的 additional_config
        包含 format_info，使得 action_modifier 能够正确获取适配器支持的消息类型
        
        Args:
            message_info: BaseMessageInfo 对象
            
        Returns:
            str | None: JSON 字符串格式的 additional_config，如果为空则返回 None
        """
        import orjson
        
        # 首先获取adapter传递的additional_config
        additional_config_data = {}
        if hasattr(message_info, 'additional_config') and message_info.additional_config:
            if isinstance(message_info.additional_config, dict):
                additional_config_data = message_info.additional_config.copy()
            elif isinstance(message_info.additional_config, str):
                # 如果是字符串，尝试解析
                try:
                    additional_config_data = orjson.loads(message_info.additional_config)
                except Exception as e:
                    logger.warning(f"无法解析 additional_config JSON: {e}")
                    additional_config_data = {}
        
        # 然后添加format_info到additional_config中
        if hasattr(message_info, 'format_info') and message_info.format_info:
            try:
                format_info_dict = message_info.format_info.to_dict()
                additional_config_data["format_info"] = format_info_dict
                logger.debug(f"嵌入 format_info 到 additional_config: {format_info_dict}")
            except Exception as e:
                logger.warning(f"将 format_info 转换为字典失败: {e}")
        else:
            logger.warning(f"[问题] 消息缺少 format_info: message_id={getattr(message_info, 'message_id', 'unknown')}")
            logger.warning("[问题] 这可能导致 Action 无法正确检查适配器支持的类型")
        
        # 序列化为JSON字符串
        if additional_config_data:
            try:
                return orjson.dumps(additional_config_data).decode("utf-8")
            except Exception as e:
                logger.error(f"序列化 additional_config 失败: {e}")
                return None
        return None

    def _safe_get_actions(self, message: "MessageRecv") -> list | None:
        """安全获取消息的actions字段"""
        import json
        
        try:
            actions = getattr(message, "actions", None)
            if actions is None:
                return None

            if isinstance(actions, str):
                try:
                    actions = json.loads(actions)
                except json.JSONDecodeError:
                    logger.warning(f"无法解析actions JSON字符串: {actions}")
                    return None

            if isinstance(actions, list):
                filtered_actions = [action for action in actions if action is not None and isinstance(action, str)]
                return filtered_actions if filtered_actions else None
            else:
                logger.warning(f"actions字段类型不支持: {type(actions)}")
                return None

        except Exception as e:
            logger.warning(f"获取actions字段失败: {e}")
            return None

    def _extract_reply_from_segment(self, segment) -> str | None:
        """从消息段中提取reply_to信息"""
        try:
            if hasattr(segment, "type") and segment.type == "seglist":
                if hasattr(segment, "data") and segment.data:
                    for seg in segment.data:
                        reply_id = self._extract_reply_from_segment(seg)
                        if reply_id:
                            return reply_id
            elif hasattr(segment, "type") and segment.type == "reply":
                return str(segment.data) if segment.data else None
        except Exception as e:
            logger.warning(f"提取reply_to信息失败: {e}")
        return None

    def _generate_chat_id(self, message_info) -> str:
        """生成chat_id，基于群组或用户信息"""
        try:
            group_info = getattr(message_info, "group_info", None)
            user_info = getattr(message_info, "user_info", None)

            if group_info and hasattr(group_info, "group_id") and group_info.group_id:
                return f"{self.platform}_{group_info.group_id}"
            elif user_info and hasattr(user_info, "user_id") and user_info.user_id:
                return f"{self.platform}_{user_info.user_id}_private"
            else:
                return self.stream_id
        except Exception as e:
            logger.warning(f"生成chat_id失败: {e}")
            return self.stream_id

    @property
    def focus_energy(self) -> float:
        """获取缓存的focus_energy值"""
        return self._focus_energy

    async def calculate_focus_energy(self) -> float:
        """异步计算focus_energy"""
        try:
            all_messages = self.context_manager.get_messages(limit=global_config.chat.max_context_size)

            user_id = None
            effective_user_info = self._get_effective_user_info()
            if effective_user_info and hasattr(effective_user_info, "user_id"):
                user_id = str(effective_user_info.user_id)

            from src.chat.energy_system import energy_manager

            energy = await energy_manager.calculate_focus_energy(
                stream_id=self.stream_id, messages=all_messages, user_id=user_id
            )

            self._focus_energy = energy

            logger.debug(f"聊天流 {self.stream_id} 能量: {energy:.3f}")
            return energy

        except Exception as e:
            logger.error(f"获取focus_energy失败: {e}", exc_info=True)
            return self._focus_energy

    @focus_energy.setter
    def focus_energy(self, value: float):
        """设置focus_energy值"""
        self._focus_energy = max(0.0, min(1.0, value))

    async def _get_user_relationship_score(self) -> float:
        """获取用户关系分"""
        try:
            from src.plugin_system.apis.scoring_api import scoring_api

            effective_user_info = self._get_effective_user_info()
            if effective_user_info and hasattr(effective_user_info, "user_id"):
                user_id = str(effective_user_info.user_id)
                relationship_score = await scoring_api.get_user_relationship_score(user_id)
                logger.debug(f"OptimizedChatStream {self.stream_id}: 用户关系分 = {relationship_score:.3f}")
                return relationship_score

        except Exception as e:
            logger.warning(f"OptimizedChatStream {self.stream_id}: 关系分计算失败: {e}")

        return 0.3

    def create_snapshot(self) -> "OptimizedChatStream":
        """创建当前状态的快照（用于缓存）"""
        # 创建一个新的实例，共享相同的上下文
        snapshot = OptimizedChatStream(
            stream_id=self.stream_id,
            platform=self.platform,
            user_info=self._get_effective_user_info(),
            group_info=self._get_effective_group_info(),
        )

        # 复制本地修改（但不触发写时复制）
        snapshot._local_changes._changes = self._local_changes.get_changes()
        snapshot._local_changes._dirty = self._local_changes._dirty
        snapshot._focus_energy = self._focus_energy
        snapshot.base_interest_energy = self.base_interest_energy
        snapshot.no_reply_consecutive = self.no_reply_consecutive
        snapshot.saved = self.saved

        return snapshot


# 为了向后兼容，创建一个工厂函数
def create_optimized_chat_stream(
    stream_id: str,
    platform: str,
    user_info: UserInfo,
    group_info: GroupInfo | None = None,
    data: dict | None = None,
) -> OptimizedChatStream:
    """创建优化版聊天流实例"""
    return OptimizedChatStream(
        stream_id=stream_id, platform=platform, user_info=user_info, group_info=group_info, data=data
    )
