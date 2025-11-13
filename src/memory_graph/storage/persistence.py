"""
持久化管理：负责记忆图数据的保存和加载
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import aiofiles
import orjson

from src.common.logger import get_logger
from src.memory_graph.models import StagedMemory
from src.memory_graph.storage.graph_store import GraphStore

logger = get_logger(__name__)

# Windows 平台检测
IS_WINDOWS = sys.platform == "win32"

# Windows 平台检测
IS_WINDOWS = sys.platform == "win32"


async def safe_atomic_write(temp_path: Path, target_path: Path, max_retries: int = 5) -> None:
    """
    安全的原子写入操作（针对 Windows 文件锁优化）

    Args:
        temp_path: 临时文件路径
        target_path: 目标文件路径
        max_retries: 最大重试次数

    Raises:
        OSError: 所有重试都失败时抛出
    """
    last_error: Exception | None = None

    for attempt in range(max_retries):
        try:
            if IS_WINDOWS:
                # Windows 特殊处理：多步骤原子替换
                if target_path.exists():
                    # 策略1: 尝试直接删除
                    try:
                        os.unlink(target_path)
                    except OSError:
                        # 策略2: 重命名为 .old 文件
                        old_file = target_path.with_suffix(".old")
                        try:
                            if old_file.exists():
                                os.unlink(old_file)
                            target_path.rename(old_file)
                        except OSError:
                            # 策略3: 使用时间戳后缀
                            from datetime import datetime
                            backup_file = target_path.with_suffix(f".bak_{datetime.now().strftime('%H%M%S')}")
                            target_path.rename(backup_file)
                            # 标记稍后清理
                            asyncio.create_task(_cleanup_backup_files(target_path.parent, target_path.stem))

                # 执行重命名
                temp_path.rename(target_path)
            else:
                # Unix/Linux: 直接使用 replace (原子操作)
                temp_path.replace(target_path)

            # 成功
            return

        except OSError as e:
            last_error = e
            if attempt < max_retries - 1:
                # 指数退避重试
                wait_time = 0.05 * (2 ** attempt)
                logger.warning(
                    f"文件替换失败 (尝试 {attempt + 1}/{max_retries}), "
                    f"等待 {wait_time:.3f}s 后重试: {e}"
                )
                await asyncio.sleep(wait_time)
            else:
                logger.error(f"文件替换失败，已达到最大重试次数 ({max_retries})")

    # 所有重试失败
    if last_error:
        raise last_error
    raise OSError(f"文件替换失败: {temp_path} -> {target_path}")


async def _cleanup_backup_files(directory: Path, file_stem: str, keep_recent: int = 3) -> None:
    """
    清理临时备份文件（后台异步任务）

    Args:
        directory: 目录路径
        file_stem: 文件主名（不含扩展名）
        keep_recent: 保留最近的文件数量
    """
    try:
        # 延迟执行，避免立即清理可能仍在使用的文件
        await asyncio.sleep(5)

        # 查找所有备份文件
        pattern = f"{file_stem}.bak_*"
        backup_files = sorted(directory.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)

        # 删除超出保留数量的文件
        for old_file in backup_files[keep_recent:]:
            try:
                old_file.unlink()
                logger.debug(f"已清理旧备份文件: {old_file.name}")
            except OSError as e:
                logger.debug(f"清理备份文件失败: {old_file.name}, {e}")

    except Exception as e:
        logger.debug(f"清理备份文件任务失败: {e}")


class PersistenceManager:
    """
    持久化管理器

    负责：
    1. 图数据的保存和加载
    2. 定期自动保存
    3. 备份管理
    """

    def __init__(
        self,
        data_dir: Path,
        graph_file_name: str = "memory_graph.json",
        staged_file_name: str = "staged_memories.json",
        auto_save_interval: int = 300,  # 自动保存间隔（秒）
    ):
        """
        初始化持久化管理器

        Args:
            data_dir: 数据存储目录
            graph_file_name: 图数据文件名
            staged_file_name: 临时记忆文件名
            auto_save_interval: 自动保存间隔（秒）
        """
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.graph_file = self.data_dir / graph_file_name
        self.staged_file = self.data_dir / staged_file_name
        self.backup_dir = self.data_dir / "backups"
        self.backup_dir.mkdir(parents=True, exist_ok=True)

        self.auto_save_interval = auto_save_interval
        self._auto_save_task: asyncio.Task | None = None
        self._running = False
        self._file_lock = asyncio.Lock()  # 文件操作锁

        logger.info(f"初始化持久化管理器: data_dir={data_dir}")

    async def save_graph_store(self, graph_store: GraphStore) -> None:
        """
        保存图存储到文件

        Args:
            graph_store: 图存储对象
        """
        async with self._file_lock:  # 使用文件锁防止并发访问
            try:
                # 转换为字典
                data = graph_store.to_dict()

                # 添加元数据
                data["metadata"] = {
                    "version": "0.1.0",
                    "saved_at": datetime.now().isoformat(),
                    "statistics": graph_store.get_statistics(),
                }

                # 使用 orjson 序列化（更快）
                json_data = orjson.dumps(
                    data,
                    option=orjson.OPT_INDENT_2 | orjson.OPT_SERIALIZE_NUMPY,
                )

                # 原子写入（先写临时文件，再重命名）
                temp_file = self.graph_file.with_suffix(".tmp")
                async with aiofiles.open(temp_file, "wb") as f:
                    await f.write(json_data)

                # 使用安全的原子写入
                await safe_atomic_write(temp_file, self.graph_file)

                logger.debug(f"图数据已保存: {self.graph_file}, 大小: {len(json_data) / 1024:.2f} KB")

            except Exception as e:
                logger.error(f"保存图数据失败: {e}", exc_info=True)
                raise

    async def load_graph_store(self) -> GraphStore | None:
        """
        从文件加载图存储

        Returns:
            GraphStore 对象，如果文件不存在则返回 None
        """
        if not self.graph_file.exists():
            logger.info("图数据文件不存在，返回空图")
            return None

        async with self._file_lock:  # 使用文件锁防止并发访问
            try:
                # 读取文件，添加重试机制处理可能的文件锁定
                data = None
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        async with aiofiles.open(self.graph_file, "rb") as f:
                            json_data = await f.read()
                        data = orjson.loads(json_data)
                        break
                    except OSError as e:
                        if attempt == max_retries - 1:
                            raise
                        logger.warning(f"读取图数据文件失败 (尝试 {attempt + 1}/{max_retries}): {e}")
                        await asyncio.sleep(0.1 * (attempt + 1))

                if data is None:
                    logger.error("无法读取图数据文件")
                    return await self._load_from_backup()

                # 检查版本（未来可能需要数据迁移）
                version = data.get("metadata", {}).get("version", "unknown")
                logger.info(f"加载图数据: version={version}")

                # 恢复图存储
                graph_store = GraphStore.from_dict(data)

                logger.info(f"图数据加载完成: {graph_store.get_statistics()}")
                return graph_store

            except Exception as e:
                logger.error(f"加载图数据失败: {e}", exc_info=True)
                # 尝试加载备份
                return await self._load_from_backup()

    async def save_staged_memories(self, staged_memories: list[StagedMemory]) -> None:
        """
        保存临时记忆列表

        Args:
            staged_memories: 临时记忆列表
        """
        async with self._file_lock:  # 使用文件锁防止并发访问
            try:
                data = {
                    "metadata": {
                        "version": "0.1.0",
                        "saved_at": datetime.now().isoformat(),
                        "count": len(staged_memories),
                    },
                    "staged_memories": [sm.to_dict() for sm in staged_memories],
                }

                json_data = orjson.dumps(data, option=orjson.OPT_INDENT_2 | orjson.OPT_SERIALIZE_NUMPY)

                temp_file = self.staged_file.with_suffix(".tmp")
                async with aiofiles.open(temp_file, "wb") as f:
                    await f.write(json_data)

                # 使用安全的原子写入
                await safe_atomic_write(temp_file, self.staged_file)

                logger.info(f"临时记忆已保存: {len(staged_memories)} 条")

            except Exception as e:
                logger.error(f"保存临时记忆失败: {e}", exc_info=True)
                raise

    async def load_staged_memories(self) -> list[StagedMemory]:
        """
        加载临时记忆列表

        Returns:
            临时记忆列表
        """
        if not self.staged_file.exists():
            logger.info("临时记忆文件不存在，返回空列表")
            return []

        async with self._file_lock:  # 使用文件锁防止并发访问
            try:
                # 读取文件，添加重试机制处理可能的文件锁定
                data = None
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        async with aiofiles.open(self.staged_file, "rb") as f:
                            json_data = await f.read()
                        data = orjson.loads(json_data)
                        break
                    except OSError as e:
                        if attempt == max_retries - 1:
                            raise
                        logger.warning(f"读取临时记忆文件失败 (尝试 {attempt + 1}/{max_retries}): {e}")
                        await asyncio.sleep(0.1 * (attempt + 1))

                if data is None:
                    logger.error("无法读取临时记忆文件")
                    return []

                staged_memories = [StagedMemory.from_dict(sm) for sm in data.get("staged_memories", [])]

                logger.info(f"临时记忆加载完成: {len(staged_memories)} 条")
                return staged_memories

            except Exception as e:
                logger.error(f"加载临时记忆失败: {e}", exc_info=True)
                return []

    async def create_backup(self) -> Path | None:
        """
        创建当前数据的备份

        Returns:
            备份文件路径，如果失败则返回 None
        """
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_file = self.backup_dir / f"memory_graph_backup_{timestamp}.json"

            if self.graph_file.exists():
                # 复制图数据文件
                async with aiofiles.open(self.graph_file, "rb") as src:
                    async with aiofiles.open(backup_file, "wb") as dst:
                        while chunk := await src.read(8192):
                            await dst.write(chunk)

                # 清理旧备份（只保留最近10个）
                await self._cleanup_old_backups(keep=10)

                logger.info(f"备份创建成功: {backup_file}")
                return backup_file

            return None

        except Exception as e:
            logger.error(f"创建备份失败: {e}", exc_info=True)
            return None

    async def _load_from_backup(self) -> GraphStore | None:
        """从最新的备份加载数据"""
        try:
            # 查找最新的备份文件
            backup_files = sorted(self.backup_dir.glob("memory_graph_backup_*.json"), reverse=True)

            if not backup_files:
                logger.warning("没有可用的备份文件")
                return None

            latest_backup = backup_files[0]
            logger.warning(f"尝试从备份恢复: {latest_backup}")

            # 读取备份文件，添加重试机制
            data = None
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    async with aiofiles.open(latest_backup, "rb") as f:
                        json_data = await f.read()
                    data = orjson.loads(json_data)
                    break
                except OSError as e:
                    if attempt == max_retries - 1:
                        raise
                    logger.warning(f"读取备份文件失败 (尝试 {attempt + 1}/{max_retries}): {e}")
                    await asyncio.sleep(0.1 * (attempt + 1))

            if data is None:
                logger.error("无法从备份读取数据")
                return None

            graph_store = GraphStore.from_dict(data)
            logger.info(f"从备份恢复成功: {graph_store.get_statistics()}")

            return graph_store

        except Exception as e:
            logger.error(f"从备份恢复失败: {e}", exc_info=True)
            return None

    async def _cleanup_old_backups(self, keep: int = 10) -> None:
        """
        清理旧备份，只保留最近的几个

        Args:
            keep: 保留的备份数量
        """
        try:
            backup_files = sorted(self.backup_dir.glob("memory_graph_backup_*.json"), reverse=True)

            # 删除超出数量的备份
            for backup_file in backup_files[keep:]:
                backup_file.unlink()
                logger.debug(f"删除旧备份: {backup_file}")

        except Exception as e:
            logger.warning(f"清理旧备份失败: {e}")

    async def start_auto_save(
        self,
        graph_store: GraphStore,
        staged_memories_getter: Callable[[], list[StagedMemory]] | None = None,
    ) -> None:
        """
        启动自动保存任务

        Args:
            graph_store: 图存储对象
            staged_memories_getter: 获取临时记忆的回调函数
        """
        if self._auto_save_task and not self._auto_save_task.done():
            logger.warning("自动保存任务已在运行")
            return

        self._running = True

        async def auto_save_loop():
            logger.info(f"自动保存任务已启动，间隔: {self.auto_save_interval}秒")

            while self._running:
                try:
                    await asyncio.sleep(self.auto_save_interval)

                    if not self._running:
                        break

                    # 保存图数据
                    await self.save_graph_store(graph_store)

                    # 保存临时记忆（如果提供了获取函数）
                    if staged_memories_getter:
                        staged_memories = staged_memories_getter()
                        if staged_memories:
                            await self.save_staged_memories(staged_memories)

                    # 定期创建备份（每小时）
                    current_time = datetime.now()
                    if current_time.minute == 0:  # 每个整点
                        await self.create_backup()

                except Exception as e:
                    logger.error(f"自动保存失败: {e}", exc_info=True)

            logger.info("自动保存任务已停止")

        self._auto_save_task = asyncio.create_task(auto_save_loop())

    def stop_auto_save(self) -> None:
        """停止自动保存任务"""
        self._running = False
        if self._auto_save_task:
            self._auto_save_task.cancel()
            logger.info("自动保存任务已取消")

    async def export_to_json(self, output_file: Path, graph_store: GraphStore) -> None:
        """
        导出图数据到指定的 JSON 文件（用于数据迁移或分析）

        Args:
            output_file: 输出文件路径
            graph_store: 图存储对象
        """
        try:
            data = graph_store.to_dict()
            data["metadata"] = {
                "version": "0.1.0",
                "exported_at": datetime.now().isoformat(),
                "statistics": graph_store.get_statistics(),
            }

            # 使用标准 json 以获得更好的可读性
            output_file.parent.mkdir(parents=True, exist_ok=True)
            async with aiofiles.open(output_file, "w", encoding="utf-8") as f:
                json_str = json.dumps(data, ensure_ascii=False, indent=2)
                await f.write(json_str)

            logger.info(f"图数据已导出: {output_file}")

        except Exception as e:
            logger.error(f"导出图数据失败: {e}", exc_info=True)
            raise

    async def import_from_json(self, input_file: Path) -> GraphStore | None:
        """
        从 JSON 文件导入图数据

        Args:
            input_file: 输入文件路径

        Returns:
            GraphStore 对象
        """
        try:
            async with aiofiles.open(input_file, "r", encoding="utf-8") as f:
                content = await f.read()
                data = json.loads(content)

            graph_store = GraphStore.from_dict(data)
            logger.info(f"图数据已导入: {graph_store.get_statistics()}")

            return graph_store

        except Exception as e:
            logger.error(f"导入图数据失败: {e}", exc_info=True)
            raise

    def get_data_size(self) -> dict[str, int]:
        """
        获取数据文件的大小信息

        Returns:
            文件大小字典（字节）
        """
        sizes = {}

        if self.graph_file.exists():
            sizes["graph"] = self.graph_file.stat().st_size

        if self.staged_file.exists():
            sizes["staged"] = self.staged_file.stat().st_size

        # 计算备份文件总大小
        backup_size = sum(f.stat().st_size for f in self.backup_dir.glob("*.json"))
        sizes["backups"] = backup_size

        return sizes
