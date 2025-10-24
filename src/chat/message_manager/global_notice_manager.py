"""
全局Notice管理器
用于统一管理所有notice消息，将notice与正常消息分离
"""

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from src.common.data_models.database_data_model import DatabaseMessages
from src.common.logger import get_logger

logger = get_logger("global_notice_manager")


class NoticeScope(Enum):
    """Notice作用域"""
    PUBLIC = "public"  # 公共notice，所有聊天流可见
    STREAM = "stream"  # 特定聊天流notice


@dataclass
class NoticeMessage:
    """Notice消息数据结构"""
    message: DatabaseMessages
    scope: NoticeScope
    target_stream_id: str | None = None  # 如果是STREAM类型，指定目标流ID
    timestamp: float = field(default_factory=time.time)
    ttl: int = 3600  # 默认1小时过期

    def is_expired(self) -> bool:
        """检查是否过期"""
        return time.time() - self.timestamp > self.ttl

    def is_accessible_by_stream(self, stream_id: str) -> bool:
        """检查聊天流是否可以访问此notice"""
        if self.scope == NoticeScope.PUBLIC:
            return True
        return self.target_stream_id == stream_id


class GlobalNoticeManager:
    """全局Notice管理器"""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "_initialized"):
            return

        self._initialized = True
        self._notices: dict[str, deque[NoticeMessage]] = defaultdict(deque)
        self._max_notices_per_type = 100  # 每种类型最大存储数量
        self._cleanup_interval = 300  # 5分钟清理一次过期消息
        self._last_cleanup_time = time.time()

        # 统计信息
        self.stats = {
            "total_notices": 0,
            "public_notices": 0,
            "stream_notices": 0,
            "expired_notices": 0,
            "last_cleanup_time": 0,
        }

        logger.info("全局Notice管理器初始化完成")

    def add_notice(
        self,
        message: DatabaseMessages,
        scope: NoticeScope = NoticeScope.STREAM,
        target_stream_id: str | None = None,
        ttl: int | None = None
    ) -> bool:
        """添加notice消息

        Args:
            message: 数据库消息对象
            scope: notice作用域
            target_stream_id: 目标聊天流ID（仅在STREAM模式下有效）
            ttl: 生存时间（秒），默认为1小时

        Returns:
            bool: 是否添加成功
        """
        try:
            # 验证消息是否为notice类型
            if not self._is_notice_message(message):
                logger.warning(f"尝试添加非notice消息: {message.message_id}")
                return False

            # 验证参数
            if scope == NoticeScope.STREAM and not target_stream_id:
                logger.error("STREAM类型的notice必须指定target_stream_id")
                return False

            # 创建notice消息
            notice = NoticeMessage(
                message=message,
                scope=scope,
                target_stream_id=target_stream_id,
                ttl=ttl or 3600  # 默认1小时
            )

            # 确定存储键
            storage_key = self._get_storage_key(scope, target_stream_id, message)

            # 添加到存储
            self._notices[storage_key].append(notice)

            # 限制数量
            if len(self._notices[storage_key]) > self._max_notices_per_type:
                # 移除最旧的消息
                removed = self._notices[storage_key].popleft()
                logger.debug(f"移除过期notice: {removed.message.message_id}")

            # 更新统计
            self.stats["total_notices"] += 1
            if scope == NoticeScope.PUBLIC:
                self.stats["public_notices"] += 1
            else:
                self.stats["stream_notices"] += 1

            # 定期清理过期消息
            self._cleanup_expired_notices()

            logger.info(f"✅ Notice已添加: id={message.message_id}, type={self._get_notice_type(message)}, scope={scope.value}, target={target_stream_id}, storage_key={storage_key}, ttl={ttl}s")
            return True

        except Exception as e:
            logger.error(f"添加notice消息失败: {e}")
            return False

    def get_accessible_notices(self, stream_id: str, limit: int = 20) -> list[NoticeMessage]:
        """获取指定聊天流可访问的notice消息

        Args:
            stream_id: 聊天流ID
            limit: 最大返回数量

        Returns:
            List[NoticeMessage]: 可访问的notice消息列表，按时间倒序排列
        """
        try:
            accessible_notices = []
            current_time = time.time()

            # 清理过期消息
            if current_time - self._last_cleanup_time > self._cleanup_interval:
                self._cleanup_expired_notices()

            # 收集可访问的notice
            for storage_key, notices in self._notices.items():
                for notice in notices:
                    if notice.is_expired():
                        continue

                    if notice.is_accessible_by_stream(stream_id):
                        accessible_notices.append(notice)

            # 按时间倒序排列
            accessible_notices.sort(key=lambda x: x.timestamp, reverse=True)

            # 限制数量
            return accessible_notices[:limit]

        except Exception as e:
            logger.error(f"获取可访问notice失败: {e}")
            return []

    def get_notice_text(self, stream_id: str, limit: int = 10) -> str:
        """获取格式化的notice文本，用于构建提示词

        Args:
            stream_id: 聊天流ID
            limit: 最大notice数量

        Returns:
            str: 格式化的notice文本块（不包含标题，由调用方添加）
        """
        try:
            notices = self.get_accessible_notices(stream_id, limit)

            if not notices:
                logger.debug(f"没有可访问的notice消息: stream_id={stream_id}")
                return ""

            # 构建notice文本块（不包含标题和结束线）
            notice_lines = []

            for notice in notices:
                message = notice.message
                notice_type = self._get_notice_type(message)

                # 格式化notice消息
                if notice_type:
                    notice_line = f"[{notice_type}] {message.processed_plain_text or message.raw_message}"
                else:
                    notice_line = f"[通知] {message.processed_plain_text or message.raw_message}"

                # 添加时间信息（相对时间）
                time_diff = int(time.time() - notice.timestamp)
                if time_diff < 60:
                    time_str = "刚刚"
                elif time_diff < 3600:
                    time_str = f"{time_diff // 60}分钟前"
                elif time_diff < 86400:
                    time_str = f"{time_diff // 3600}小时前"
                else:
                    time_str = f"{time_diff // 86400}天前"

                notice_line += f" ({time_str})"
                notice_lines.append(notice_line)

            result = "\n".join(notice_lines)
            logger.debug(f"获取notice文本成功: stream_id={stream_id}, 数量={len(notices)}")
            return result

        except Exception as e:
            logger.error(f"获取notice文本失败: {e}", exc_info=True)
            return ""

    def clear_notices(self, stream_id: str | None = None, notice_type: str | None = None) -> int:
        """清理notice消息

        Args:
            stream_id: 聊天流ID，如果为None则清理所有流
            notice_type: notice类型，如果为None则清理所有类型

        Returns:
            int: 清理的消息数量
        """
        try:
            removed_count = 0

            # 需要移除的键
            keys_to_remove = []

            for storage_key, notices in self._notices.items():
                new_notices = deque()

                for notice in notices:
                    should_remove = True

                    # 检查流ID过滤
                    if stream_id is not None:
                        if notice.scope == NoticeScope.STREAM:
                            if notice.target_stream_id != stream_id:
                                should_remove = False
                        else:
                            # 公共notice，只有当指定清理所有流时才清理
                            should_remove = False

                    # 检查notice类型过滤
                    if should_remove and notice_type is not None:
                        message_type = self._get_notice_type(notice.message)
                        if message_type != notice_type:
                            should_remove = False

                    if should_remove:
                        removed_count += 1
                    else:
                        new_notices.append(notice)

                if new_notices:
                    self._notices[storage_key] = new_notices
                else:
                    keys_to_remove.append(storage_key)

            # 移除空的键
            for key in keys_to_remove:
                del self._notices[key]

            logger.info(f"清理notice消息: {removed_count} 条")
            return removed_count

        except Exception as e:
            logger.error(f"清理notice消息失败: {e}")
            return 0

    def get_stats(self) -> dict[str, Any]:
        """获取统计信息"""
        # 更新实时统计
        total_active_notices = sum(len(notices) for notices in self._notices.values())
        self.stats["total_notices"] = total_active_notices
        self.stats["active_keys"] = len(self._notices)
        self.stats["last_cleanup_time"] = int(self._last_cleanup_time)

        # 添加详细的存储键信息
        storage_keys_info = {}
        for key, notices in self._notices.items():
            storage_keys_info[key] = {
                "count": len(notices),
                "oldest": min((n.timestamp for n in notices), default=0),
                "newest": max((n.timestamp for n in notices), default=0),
            }
        self.stats["storage_keys"] = storage_keys_info

        return self.stats.copy()

    def _is_notice_message(self, message: DatabaseMessages) -> bool:
        """检查消息是否为notice类型"""
        try:
            # 首先检查消息的is_notify字段
            if hasattr(message, "is_notify") and message.is_notify:
                return True

            # 检查消息的附加配置
            if hasattr(message, "additional_config") and message.additional_config:
                if isinstance(message.additional_config, dict):
                    return message.additional_config.get("is_notice", False)
                elif isinstance(message.additional_config, str):
                    # 兼容JSON字符串格式
                    import json
                    config = json.loads(message.additional_config)
                    return config.get("is_notice", False)

            # 检查消息类型或其他标识
            return False

        except Exception as e:
            logger.debug(f"检查notice类型失败: {e}")
            return False

    def _get_storage_key(self, scope: NoticeScope, target_stream_id: str | None, message: DatabaseMessages) -> str:
        """生成存储键"""
        if scope == NoticeScope.PUBLIC:
            return "public"
        else:
            notice_type = self._get_notice_type(message) or "default"
            return f"stream_{target_stream_id}_{notice_type}"

    def _get_notice_type(self, message: DatabaseMessages) -> str | None:
        """获取notice类型"""
        try:
            if hasattr(message, "additional_config") and message.additional_config:
                if isinstance(message.additional_config, dict):
                    return message.additional_config.get("notice_type")
                elif isinstance(message.additional_config, str):
                    import json
                    config = json.loads(message.additional_config)
                    return config.get("notice_type")
            return None
        except Exception:
            return None

    def _cleanup_expired_notices(self) -> int:
        """清理过期的notice消息"""
        try:
            current_time = time.time()
            if current_time - self._last_cleanup_time < self._cleanup_interval:
                return 0

            removed_count = 0
            keys_to_remove = []

            for storage_key, notices in self._notices.items():
                new_notices = deque()

                for notice in notices:
                    if notice.is_expired():
                        removed_count += 1
                        self.stats["expired_notices"] += 1
                    else:
                        new_notices.append(notice)

                if new_notices:
                    self._notices[storage_key] = new_notices
                else:
                    keys_to_remove.append(storage_key)

            # 移除空的键
            for key in keys_to_remove:
                del self._notices[key]

            self._last_cleanup_time = current_time

            if removed_count > 0:
                logger.debug(f"清理过期notice: {removed_count} 条")

            return removed_count

        except Exception as e:
            logger.error(f"清理过期notice失败: {e}")
            return 0


# 创建全局单例实例
global_notice_manager = GlobalNoticeManager()
