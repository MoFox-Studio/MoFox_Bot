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

# 初始化日志记录器
logger = get_logger("MaiZone.ReplyTrackerService")


class ReplyTrackerService:
    """
    评论回复跟踪服务

    本服务负责持久化存储已回复的评论ID，以防止对同一评论的重复回复。
    它利用了插件系统的 `storage_api` 来实现统一和安全的数据管理。
    在初始化时，它还会自动处理从旧版文件存储到新版API的数据迁移。
    """

    def __init__(self):
        """
        初始化回复跟踪服务。

        - 获取专用的插件存储实例。
        - 设置数据清理的配置。
        - 执行一次性数据迁移（如果需要）。
        - 从存储中加载已有的回复记录。
        """
        # 使用插件存储API，获取一个名为 "maizone_reply_tracker" 的专属存储空间
        self.storage = get_local_storage("maizone_reply_tracker")

        # 在内存中维护已回复的评论记录，以提高访问速度
        # 数据结构为: {feed_id: {comment_id: timestamp, ...}, ...}
        self.replied_comments: dict[str, dict[str, float]] = {}

        # 配置记录的最大保留天数，过期将被清理
        self.max_record_days = 30

        # --- 核心初始化流程 ---
        # 步骤1: 检查并执行从旧文件到新存储API的一次性数据迁移
        self._perform_one_time_migration()

        # 步骤2: 从新的存储API中加载数据来初始化服务状态
        initial_data = self.storage.get("data", {})
        if self._validate_data(initial_data):
            self.replied_comments = initial_data
            logger.info(
                f"已从存储API加载 {len(self.replied_comments)} 条说说的回复记录，"
                f"总计 {sum(len(comments) for comments in self.replied_comments.values())} 条评论"
            )
        else:
            # 如果数据格式校验失败，则初始化为空字典以保证服务的稳定性
            logger.error("从存储API加载的数据格式无效，将创建新的记录")
            self.replied_comments = {}

        logger.debug(f"ReplyTrackerService 初始化完成，使用数据文件: {self.storage.file_path}")

    def _perform_one_time_migration(self):
        """
        执行一次性数据迁移。

        该函数会检查是否存在旧的 `replied_comments.json` 文件。
        如果存在，它会读取数据，验证其格式，将其写入新的存储API，
        然后将旧文件重命名为备份文件，以完成迁移。
        这是一个安全操作，旨在平滑过渡。
        """
        # 定义旧数据文件的路径
        old_data_file = Path(__file__).resolve().parent.parent / "data" / "replied_comments.json"

        # 仅当旧文件存在时才执行迁移
        if old_data_file.exists():
            logger.info(f"检测到旧的数据文件 '{old_data_file}'，开始执行一次性迁移...")
            try:
                # 步骤1: 读取旧文件内容并立即关闭文件
                with open(old_data_file, "rb") as f:
                    file_content = f.read()

                # 步骤2: 处理文件内容
                # 如果文件为空，直接删除，无需迁移
                if not file_content.strip():
                    logger.warning("旧数据文件为空，无需迁移。")
                    os.remove(old_data_file)
                    logger.info(f"空的旧数据文件 '{old_data_file}' 已被删除。")
                    return

                # 解析JSON数据
                old_data = orjson.loads(file_content)

                # 步骤3: 验证数据并执行迁移/备份
                if self._validate_data(old_data):
                    # 验证通过，将数据写入新的存储API
                    self.storage.set("data", old_data)
                    # 立即强制保存，确保迁移数据落盘
                    self.storage._save_data()
                    logger.info("旧数据已成功迁移到新的存储API。")

                    # 将旧文件重命名为备份文件
                    backup_file = old_data_file.with_suffix(f".json.bak.migrated.{int(time.time())}")
                    old_data_file.rename(backup_file)
                    logger.info(f"旧数据文件已成功迁移并备份为: {backup_file}")
                else:
                    # 如果数据格式无效，迁移中止，并备份损坏的文件
                    logger.error("旧数据文件格式无效，迁移中止。")
                    backup_file = old_data_file.with_suffix(f".json.bak.invalid.{int(time.time())}")
                    old_data_file.rename(backup_file)
                    logger.warning(f"已将无效的旧数据文件备份为: {backup_file}")

            except Exception as e:
                # 捕获迁移过程中可能出现的任何异常
                logger.error(f"迁移旧数据文件时发生错误: {e}", exc_info=True)

    def _validate_data(self, data: Any) -> bool:
        """
        验证加载的数据格式是否正确。

        Args:
            data (Any): 待验证的数据。

        Returns:
            bool: 如果数据格式符合预期则返回 True，否则返回 False。
        """
        # 顶级结构必须是字典
        if not isinstance(data, dict):
            logger.error("加载的数据不是字典格式")
            return False

        # 遍历每个说说(feed)的记录
        for feed_id, comments in data.items():
            # 说说ID必须是字符串
            if not isinstance(feed_id, str):
                logger.error(f"无效的说说ID格式: {feed_id}")
                return False
            # 评论记录必须是字典
            if not isinstance(comments, dict):
                logger.error(f"说说 {feed_id} 的评论数据不是字典格式")
                return False
            # 遍历每条评论
            for comment_id, timestamp in comments.items():
                # 评论ID必须是字符串或整数
                if not isinstance(comment_id, str | int):
                    logger.error(f"无效的评论ID格式: {comment_id}")
                    return False
                # 时间戳必须是整数或浮点数
                if not isinstance(timestamp, int | float):
                    logger.error(f"无效的时间戳格式: {timestamp}")
                    return False
        return True

    def _persist_data(self):
        """
        清理、验证并持久化数据到存储API。

        这是一个核心的内部方法，用于将内存中的 `self.replied_comments` 数据
        通过 `storage_api` 保存到磁盘。它封装了清理和验证的逻辑。
        """
        try:
            # 第一步：清理内存中的过期记录
            self._cleanup_old_records()

            # 第二步：验证当前数据格式是否有效，防止坏数据写入
            if not self._validate_data(self.replied_comments):
                logger.error("当前内存中的数据格式无效，取消保存")
                return

            # 第三步：调用存储API的set方法，将数据暂存。API会处理后续的延迟写入
            self.storage.set("data", self.replied_comments)
            logger.debug("回复记录已暂存，将由存储API在后台保存")
        except Exception as e:
            logger.error(f"持久化回复记录失败: {e}", exc_info=True)

    def _cleanup_old_records(self):
        """
        清理内存中超过保留期限的回复记录。
        """
        current_time = time.time()
        # 计算N天前的时间戳，作为清理的阈值
        cutoff_time = current_time - (self.max_record_days * 24 * 60 * 60)
        total_removed = 0

        # 找出所有评论都已过期的说说记录
        feeds_to_remove = [
            feed_id
            for feed_id, comments in self.replied_comments.items()
            if not any(timestamp >= cutoff_time for timestamp in comments.values())
        ]

        # 先整体移除这些完全过期的说说记录，效率更高
        for feed_id in feeds_to_remove:
            total_removed += len(self.replied_comments[feed_id])
            del self.replied_comments[feed_id]

        # 然后遍历剩余的说说，清理其中部分过期的评论记录
        for feed_id, comments in self.replied_comments.items():
            comments_to_remove = [comment_id for comment_id, timestamp in comments.items() if timestamp < cutoff_time]
            for comment_id in comments_to_remove:
                del comments[comment_id]
                total_removed += 1

        if total_removed > 0:
            logger.info(f"清理了 {total_removed} 条超过{self.max_record_days}天的过期回复记录")

    def has_replied(self, feed_id: str, comment_id: str | int) -> bool:
        """
        检查是否已经回复过指定的评论。

        Args:
            feed_id (str): 说说ID。
            comment_id (str | int): 评论ID。

        Returns:
            bool: 如果已回复过返回True，否则返回False。
        """
        if not feed_id or comment_id is None:
            return False
        # 将评论ID统一转为字符串进行比较
        comment_id_str = str(comment_id)
        return feed_id in self.replied_comments and comment_id_str in self.replied_comments[feed_id]

    def mark_as_replied(self, feed_id: str, comment_id: str | int):
        """
        标记指定评论为已回复，并触发数据持久化。

        Args:
            feed_id (str): 说说ID。
            comment_id (str | int): 评论ID。
        """
        if not feed_id or comment_id is None:
            logger.warning("feed_id 或 comment_id 为空，无法标记为已回复")
            return

        # 将评论ID统一转为字符串作为键
        comment_id_str = str(comment_id)
        # 如果是该说说下的第一条回复，则初始化内层字典
        if feed_id not in self.replied_comments:
            self.replied_comments[feed_id] = {}
        # 记录回复时间
        self.replied_comments[feed_id][comment_id_str] = time.time()

        # 调用持久化方法保存数据
        self._persist_data()
        logger.info(f"已标记评论为已回复: feed_id={feed_id}, comment_id={comment_id}")

    def get_replied_comments(self, feed_id: str) -> set[str]:
        """
        获取指定说说下所有已回复的评论ID集合。

        Args:
            feed_id (str): 说说ID。

        Returns:
            set[str]: 已回复的评论ID集合。
        """
        # 使用 .get() 避免当 feed_id 不存在时发生KeyError
        return {str(cid) for cid in self.replied_comments.get(feed_id, {}).keys()}

    def get_stats(self) -> dict[str, Any]:
        """
        获取回复记录的统计信息。

        Returns:
            dict[str, Any]: 包含统计信息的字典。
        """
        total_feeds = len(self.replied_comments)
        total_replies = sum(len(comments) for comments in self.replied_comments.values())
        return {
            "total_feeds_with_replies": total_feeds,
            "total_replied_comments": total_replies,
            # 从存储实例获取准确的数据文件路径
            "data_file": str(self.storage.file_path),
            "max_record_days": self.max_record_days,
        }

    def remove_reply_record(self, feed_id: str, comment_id: str):
        """
        移除指定评论的回复记录。

        Args:
            feed_id (str): 说说ID。
            comment_id (str): 评论ID。
        """
        # 确保记录存在再执行删除
        if feed_id in self.replied_comments and comment_id in self.replied_comments[feed_id]:
            del self.replied_comments[feed_id][comment_id]
            # 如果该说说下已无任何回复记录，则清理掉整个条目
            if not self.replied_comments[feed_id]:
                del self.replied_comments[feed_id]
            # 调用持久化方法保存更改
            self._persist_data()
            logger.debug(f"已移除回复记录: feed_id={feed_id}, comment_id={comment_id}")

    def remove_feed_records(self, feed_id: str):
        """
        移除指定说说的所有回复记录。

        Args:
            feed_id (str): 说说ID。
        """
        # 确保记录存在再执行删除
        if feed_id in self.replied_comments:
            del self.replied_comments[feed_id]
            # 调用持久化方法保存更改
            self._persist_data()
            logger.info(f"已移除说说 {feed_id} 的所有回复记录")
