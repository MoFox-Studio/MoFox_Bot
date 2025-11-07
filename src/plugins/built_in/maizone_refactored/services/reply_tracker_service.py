"""
评论回复跟踪服务
负责记录和管理已回复过的评论ID，避免重复回复
"""

import time
from pathlib import Path
from typing import Any

import orjson

from src.common.logger import get_logger

logger = get_logger("MaiZone.ReplyTrackerService")


class ReplyTrackerService:
    """
    评论回复跟踪服务
    使用本地JSON文件持久化存储已回复的评论ID
    """

    def __init__(self):
        # 数据存储路径
        self.data_dir = Path(__file__).resolve().parent.parent / "data"
        self.data_dir.mkdir(exist_ok=True, parents=True)
        self.reply_record_file = self.data_dir / "replied_comments.json"

        # 内存中的已回复评论记录
        # 格式: {feed_id: {comment_id: timestamp, ...}, ...}
        self.replied_comments: dict[str, dict[str, float]] = {}

        # 数据清理配置
        self.max_record_days = 30  # 保留30天的记录

        # 加载已有数据
        self._load_data()
        logger.debug(f"ReplyTrackerService initialized with data file: {self.reply_record_file}")

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
                # 确保comment_id是字符串格式，如果是数字则转换为字符串
                if not isinstance(comment_id, str | int):
                    logger.error(f"无效的评论ID格式: {comment_id}")
                    return False
                if not isinstance(timestamp, int | float):
                    logger.error(f"无效的时间戳格式: {timestamp}")
                    return False
        return True

    def _load_data(self):
        """从文件加载已回复评论数据"""
        try:
            if self.reply_record_file.exists():
                try:
                    with open(self.reply_record_file, encoding="utf-8") as f:
                        file_content = f.read().strip()
                        if not file_content:  # 文件为空
                            logger.warning("回复记录文件为空，将创建新的记录")
                            self.replied_comments = {}
                            return

                        data = orjson.loads(file_content)
                        if self._validate_data(data):
                            self.replied_comments = data
                            logger.info(
                                f"已加载 {len(self.replied_comments)} 条说说的回复记录，"
                                f"总计 {sum(len(comments) for comments in self.replied_comments.values())} 条评论"
                            )
                        else:
                            logger.error("加载的数据格式无效，将创建新的记录")
                            self.replied_comments = {}
                except orjson.JSONDecodeError as e:
                    logger.error(f"解析回复记录文件失败: {e}")
                    self._backup_corrupted_file()
                    self.replied_comments = {}
            else:
                logger.info("未找到回复记录文件，将创建新的记录")
                self.replied_comments = {}
        except Exception as e:
            logger.error(f"加载回复记录失败: {e}", exc_info=True)
            self.replied_comments = {}

    def _backup_corrupted_file(self):
        """备份损坏的数据文件"""
        try:
            if self.reply_record_file.exists():
                backup_file = self.reply_record_file.with_suffix(f".json.bak.{int(time.time())}")
                self.reply_record_file.rename(backup_file)
                logger.warning(f"已将损坏的数据文件备份为: {backup_file}")
        except Exception as e:
            logger.error(f"备份损坏的数据文件失败: {e}")

    def _save_data(self):
        """保存已回复评论数据到文件"""
        try:
            # 验证数据格式
            if not self._validate_data(self.replied_comments):
                logger.error("当前数据格式无效，取消保存")
                return

            # 清理过期数据
            self._cleanup_old_records()

            # 创建临时文件
            temp_file = self.reply_record_file.with_suffix(".tmp")

            # 先写入临时文件
            with open(temp_file, "w", encoding="utf-8"):
                orjson.dumps(self.replied_comments, option=orjson.OPT_INDENT_2 | orjson.OPT_NON_STR_KEYS).decode("utf-8")

            # 如果写入成功，重命名为正式文件
            if temp_file.stat().st_size > 0:  # 确保写入成功
                # 在Windows上，如果目标文件已存在，需要先删除它
                if self.reply_record_file.exists():
                    self.reply_record_file.unlink()
                temp_file.rename(self.reply_record_file)
                logger.debug(f"回复记录已保存，包含 {len(self.replied_comments)} 条说说的记录")
            else:
                logger.error("临时文件写入失败，文件大小为0")
                temp_file.unlink()  # 删除空的临时文件

        except Exception as e:
            logger.error(f"保存回复记录失败: {e}", exc_info=True)
            # 尝试删除可能存在的临时文件
            try:
                if temp_file.exists():
                    temp_file.unlink()
            except Exception:
                pass

    def _cleanup_old_records(self):
        """清理超过保留期限的记录"""
        current_time = time.time()
        cutoff_time = current_time - (self.max_record_days * 24 * 60 * 60)

        feeds_to_remove = []
        total_removed = 0

        # 仅清理超过保留期限的记录，不根据API返回结果清理
        for feed_id, comments in self.replied_comments.items():
            comments_to_remove = []

            # 仅清理超过指定天数的记录
            for comment_id, timestamp in comments.items():
                if timestamp < cutoff_time:
                    comments_to_remove.append(comment_id)

            # 移除过期的评论记录
            for comment_id in comments_to_remove:
                del comments[comment_id]
                total_removed += 1

            # 如果该说说下没有任何记录了，标记删除整个说说记录
            if not comments:
                feeds_to_remove.append(feed_id)

        # 移除空的说说记录
        for feed_id in feeds_to_remove:
            del self.replied_comments[feed_id]

        if total_removed > 0:
            logger.info(f"清理了 {total_removed} 条超过{self.max_record_days}天的过期回复记录")

    def has_replied(self, feed_id: str, comment_id: str | int) -> bool:
        """
        检查是否已经回复过指定的评论

        Args:
            feed_id: 说说ID
            comment_id: 评论ID (可以是字符串或数字)

        Returns:
            bool: 如果已回复过返回True，否则返回False
        """
        if not feed_id or comment_id is None:
            return False

        comment_id_str = str(comment_id)
        return feed_id in self.replied_comments and comment_id_str in self.replied_comments[feed_id]

    def mark_as_replied(self, feed_id: str, comment_id: str | int):
        """
        标记指定评论为已回复

        Args:
            feed_id: 说说ID
            comment_id: 评论ID (可以是字符串或数字)
        """
        if not feed_id or comment_id is None:
            logger.warning("feed_id 或 comment_id 为空，无法标记为已回复")
            return

        current_time = time.time()

        # 确保将comment_id转换为字符串格式
        comment_id_str = str(comment_id)

        if feed_id not in self.replied_comments:
            self.replied_comments[feed_id] = {}

        self.replied_comments[feed_id][comment_id_str] = current_time

        # 验证数据并保存到文件
        if self._validate_data(self.replied_comments):
            self._save_data()
            logger.info(f"已标记评论为已回复: feed_id={feed_id}, comment_id={comment_id}")
        else:
            logger.error(f"标记评论时数据验证失败: feed_id={feed_id}, comment_id={comment_id}")

    def get_replied_comments(self, feed_id: str) -> set[str]:
        """
        获取指定说说下所有已回复的评论ID

        Args:
            feed_id: 说说ID

        Returns:
            Set[str]: 已回复的评论ID集合
        """
        if feed_id in self.replied_comments:
            # 确保所有评论ID都是字符串格式
            return {str(comment_id) for comment_id in self.replied_comments[feed_id].keys()}
        return set()

    def get_stats(self) -> dict[str, Any]:
        """
        获取回复记录统计信息

        Returns:
            Dict: 包含统计信息的字典
        """
        total_feeds = len(self.replied_comments)
        total_replies = sum(len(comments) for comments in self.replied_comments.values())

        return {
            "total_feeds_with_replies": total_feeds,
            "total_replied_comments": total_replies,
            "data_file": str(self.reply_record_file),
            "max_record_days": self.max_record_days,
        }

    def remove_reply_record(self, feed_id: str, comment_id: str):
        """
        移除指定评论的回复记录

        Args:
            feed_id: 说说ID
            comment_id: 评论ID
        """
        if feed_id in self.replied_comments and comment_id in self.replied_comments[feed_id]:
            del self.replied_comments[feed_id][comment_id]

            # 如果该说说下没有任何回复记录了，删除整个说说记录
            if not self.replied_comments[feed_id]:
                del self.replied_comments[feed_id]

            self._save_data()
            logger.debug(f"已移除回复记录: feed_id={feed_id}, comment_id={comment_id}")

    def remove_feed_records(self, feed_id: str):
        """
        移除指定说说的所有回复记录

        Args:
            feed_id: 说说ID
        """
        if feed_id in self.replied_comments:
            del self.replied_comments[feed_id]
            self._save_data()
            logger.info(f"已移除说说 {feed_id} 的所有回复记录")
