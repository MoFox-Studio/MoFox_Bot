"""
记忆系统管理单例

提供全局访问的 MemoryManager 实例
"""

from __future__ import annotations

from pathlib import Path

from src.common.logger import get_logger
from src.memory_graph.manager import MemoryManager

logger = get_logger(__name__)

# 全局 MemoryManager 实例
_memory_manager: MemoryManager | None = None
_initialized: bool = False


async def initialize_memory_manager(
    data_dir: Path | str | None = None,
) -> MemoryManager | None:
    """
    初始化全局 MemoryManager

    直接从 global_config.memory 读取配置

    Args:
        data_dir: 数据目录（可选，默认从配置读取）

    Returns:
        MemoryManager 实例，如果禁用则返回 None
    """
    global _memory_manager, _initialized

    if _initialized and _memory_manager:
        logger.info("MemoryManager 已经初始化，返回现有实例")
        return _memory_manager

    try:
        from src.config.config import global_config

        # 检查是否启用
        if not global_config.memory or not getattr(global_config.memory, "enable", False):
            logger.info("记忆图系统已在配置中禁用")
            _initialized = False
            _memory_manager = None
            return None

        # 处理数据目录
        if data_dir is None:
            data_dir = getattr(global_config.memory, "data_dir", "data/memory_graph")
        if isinstance(data_dir, str):
            data_dir = Path(data_dir)

        logger.info(f"正在初始化全局 MemoryManager (data_dir={data_dir})...")

        _memory_manager = MemoryManager(data_dir=data_dir)
        await _memory_manager.initialize()

        _initialized = True
        logger.info("✅ 全局 MemoryManager 初始化成功")

        return _memory_manager

    except Exception as e:
        logger.error(f"初始化 MemoryManager 失败: {e}", exc_info=True)
        _initialized = False
        _memory_manager = None
        raise


def get_memory_manager() -> MemoryManager | None:
    """
    获取全局 MemoryManager 实例

    Returns:
        MemoryManager 实例，如果未初始化则返回 None
    """
    if not _initialized or _memory_manager is None:
        logger.warning("MemoryManager 尚未初始化，请先调用 initialize_memory_manager()")
        return None

    return _memory_manager


async def shutdown_memory_manager():
    """关闭全局 MemoryManager"""
    global _memory_manager, _initialized

    if _memory_manager:
        try:
            logger.info("正在关闭全局 MemoryManager...")
            await _memory_manager.shutdown()
            logger.info("✅ 全局 MemoryManager 已关闭")
        except Exception as e:
            logger.error(f"关闭 MemoryManager 时出错: {e}", exc_info=True)
        finally:
            _memory_manager = None
            _initialized = False


def is_initialized() -> bool:
    """检查 MemoryManager 是否已初始化"""
    return _initialized and _memory_manager is not None
