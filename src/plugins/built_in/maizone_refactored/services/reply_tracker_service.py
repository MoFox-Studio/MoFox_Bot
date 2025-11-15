"""
评论回复跟踪服务
负责记录和管理已回复过的评论ID，避免重复回复
"""

import os
import time
from pathlib import Path
from typing import Any

import orjson

from src.common.logger import get_logger
from src.plugin_system.apis.storage_api import get_local_storage

logger = get_logger("MaiZone.ReplyTrackerService")


class ReplyTrackerService:
    """
    评论回复跟踪服务
    使用插件存储API持久化存储已回复的评论ID
    """

    def __init__(self):
        # 使用新的存储API
        self.storage = get_local_storage("maizone_reply_tracker")

        # 内存中的已回复评论记录
        # 格式: {feed_id: {comment_id: timestamp, ...}, ...}
        self.replied_comments: dict[str, dict[str, float]] = {}

        # 数据清理配置
        self.max_record_days = 30  # 保留30天的记录

        # --- 一次性数据迁移 ---
        self._perform_one_time_migration()

        # 从新存储加载数据
        initial_data = self.storage.get("data", {})
        if self._validate_data(initial_data):
            self.replied_comments = initial_data
            logger.info(
                f"已从存储API加载 {len(self.replied_comments)} 条说说的回复记录，"
                f"总计 {sum(len(comments) for comments in self.replied_comments.values())} 条评论"
            )
        else:
            logger.error("从存储API加载的数据格式无效，将创建新的记录")
            self.replied_comments = {}

        logger.debug(f"ReplyTrackerService initialized with data file: {self.storage.file_path}")

    def _perform_one_time_migration(self):
        """
        执行一次性数据迁移，从旧的JSON文件到新的存储API。
        """
        old_data_file = Path(__file__).resolve().parent.parent / "data" / "replied_comments.json"
        if old_data_file.exists():
            logger.info(f"检测到旧的数据文件 '{old_data_file}'，开始执行一次性迁移...")
            try:
                with open(old_data_file, "rb") as f:
                    file_content = f.read()
                    if not file_content.strip():
                        logger.warning("旧数据文件为空，无需迁移。")
                        os.remove(old_data_file)
                        logger.info(f"空的旧数据文件 '{old_data_file}' 已被删除。")
                        return

                    old_data = orjson.loads(file_content)
                    if self._validate_data(old_data):
                        # 将数据写入新存储
                        self.storage.set("data", old_data)
                        # 立即强制保存以确保迁移完成
                        self.storage._save_data()
                        logger.info("旧数据已成功迁移到新的存储API。")
                        # 备份旧文件而不是删除
                        backup_file = old_data_file.with_suffix(f".json.bak.migrated.{int(time.time())}")
                        old_data_file.rename(backup_file)
                        logger.info(f"旧数据文件已成功迁移并备份为: {backup_file}")
                    else:
                        logger.error("旧数据文件格式无效，迁移中止。")
                        backup_file = old_data_file.with_suffix(f".json.bak.invalid.{int(time.time())}")
                        old_data_file.rename(backup_file)
                        logger.warning(f"已将无效的旧数据文件备份为: {backup_file}")

            except Exception as e:
                logger.error(f"迁移旧数据文件时发生错误: {e}", exc_info=True)

    def _validate_data(self, data: Any) -> bool:
        """验证加载的数据格式是否正确"""
        if not isinstance(data, dict):
            logger.error("加载的数据不是字典格式")
            return False

        for feed_id, comments in data.items():
            if not isinstance(feed_id, str):
                logger.error(f"无效的说说ID格式: {feed_id}")
                return False
            if not isinstance(comments, dict):
                logger.error(f"说说 {feed_id} 的评论数据不是字典格式")
                return False
            for comment_id, timestamp in comments.items():
                if not isinstance(comment_id, str | int):
                    logger.error(f"无效的评论ID格式: {comment_id}")
                    return False
                if not isinstance(timestamp, int | float):
                    logger.error(f"无效的时间戳格式: {timestamp}")
                    return False
        return True

    def _persist_data(self):
        """
        清理、验证并持久化数据到存储API。
        """
        try:
            self._cleanup_old_records()

            if not self._validate_data(self.replied_comments):
                logger.error("当前内存中的数据格式无效，取消保存")
                return

            self.storage.set("data", self.replied_comments)
            logger.debug(f"回复记录已暂存，将由存储API在后台保存")
        except Exception as e:
            logger.error(f"持久化回复记录失败: {e}", exc_info=True)

    def _cleanup_old_records(self):
        """清理超过保留期限的记录"""
        current_time = time.time()
        cutoff_time = current_time - (self.max_record_days * 24 * 60 * 60)
        total_removed = 0
        feeds_to_remove = [
            feed_id
            for feed_id, comments in self.replied_comments.items()
            if not any(timestamp >= cutoff_time for timestamp in comments.values())
        ]

        # 先移除整个过期的说说
        for feed_id in feeds_to_remove:
            total_removed += len(self.replied_comments[feed_id])
            del self.replied_comments[feed_id]

        # 再清理部分过期的评论
        for feed_id, comments in self.replied_comments.items():
            comments_to_remove = [comment_id for comment_id, timestamp in comments.items() if timestamp < cutoff_time]
            for comment_id in comments_to_remove:
                del comments[comment_id]
                total_removed += 1

        if total_removed > 0:
            logger.info(f"清理了 {total_removed} 条超过{self.max_record_days}天的过期回复记录")

    def has_replied(self, feed_id: str, comment_id: str | int) -> bool:
        """检查是否已经回复过指定的评论"""
        if not feed_id or comment_id is None:
            return False
        comment_id_str = str(comment_id)
        return feed_id in self.replied_comments and comment_id_str in self.replied_comments[feed_id]

    def mark_as_replied(self, feed_id: str, comment_id: str | int):
        """标记指定评论为已回复"""
        if not feed_id or comment_id is None:
            logger.warning("feed_id 或 comment_id 为空，无法标记为已回复")
            return

        comment_id_str = str(comment_id)
        if feed_id not in self.replied_comments:
            self.replied_comments[feed_id] = {}
        self.replied_comments[feed_id][comment_id_str] = time.time()
        self._persist_data()
        logger.info(f"已标记评论为已回复: feed_id={feed_id}, comment_id={comment_id}")

    def get_replied_comments(self, feed_id: str) -> set[str]:
        """获取指定说说下所有已回复的评论ID"""
        return {str(cid) for cid in self.replied_comments.get(feed_id, {}).keys()}

    def get_stats(self) -> dict[str, Any]:
        """获取回复记录统计信息"""
        total_feeds = len(self.replied_comments)
        total_replies = sum(len(comments) for comments in self.replied_comments.values())
        return {
            "total_feeds_with_replies": total_feeds,
            "total_replied_comments": total_replies,
            "data_file": str(self.storage.file_path),
            "max_record_days": self.max_record_days,
        }

    def remove_reply_record(self, feed_id: str, comment_id: str):
        """移除指定评论的回复记录"""
        if feed_id in self.replied_comments and comment_id in self.replied_comments[feed_id]:
            del self.replied_comments[feed_id][comment_id]
            if not self.replied_comments[feed_id]:
                del self.replied_comments[feed_id]
            self._persist_data()
            logger.debug(f"已移除回复记录: feed_id={feed_id}, comment_id={comment_id}")

    def remove_feed_records(self, feed_id: str):
        """移除指定说说的所有回复记录"""
        if feed_id in self.replied_comments:
            del self.replied_comments[feed_id]
            self._persist_data()
            logger.info(f"已移除说说 {feed_id} 的所有回复记录")
