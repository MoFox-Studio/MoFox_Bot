"""
@File    :   storage_api.py
@Time    :   2025/10/25 11:03:15
@Author  :   墨墨
@Version :   2.0
@Desc    :   提供给插件使用的本地存储API（集成版）
"""

import json
import os
import threading
from typing import Any

from src.common.logger import get_logger

# 获取日志记录器
logger = get_logger("PluginStorageManager")

# --- 核心管理器部分 ---


class PluginStorageManager:
    """
    一个用于管理插件本地JSON数据存储的类。
    它处理文件的读写、数据缓存以及线程安全，确保每个插件实例的独立性。
    哼，现在它和API住在一起了，希望它们能和睦相处。
    """

    _instances: dict[str, "PluginStorage"] = {}
    _lock = threading.Lock()
    _base_path = os.path.join("data", "plugin_data")

    @classmethod
    def get_storage(cls, name: str) -> "PluginStorage":
        """
        获取指定名称的插件存储实例的工厂方法。
        """
        with cls._lock:
            if name not in cls._instances:
                logger.info(f"为插件 '{name}' 创建新的本地存储实例。")
                cls._instances[name] = PluginStorage(name, cls._base_path)
            else:
                logger.debug(f"从缓存中获取插件 '{name}' 的本地存储实例。")
            return cls._instances[name]


# --- 单个存储实例部分 ---


class PluginStorage:
    """
    单个插件的本地存储操作类。
    提供了多种方法来读取、写入和修改JSON文件中的数据。
    把数据交给我，你就放心吧。
    """

    def __init__(self, name: str, base_path: str):
        self.name = name
        safe_filename = "".join(c for c in name if c.isalnum() or c in ("_", "-")).rstrip()
        self.file_path = os.path.join(base_path, f"{safe_filename}.json")
        self._data: dict[str, Any] = {}
        self._lock = threading.Lock()

        self._ensure_directory_exists()
        self._load_data()

    def _ensure_directory_exists(self) -> None:
        try:
            directory = os.path.dirname(self.file_path)
            if not os.path.exists(directory):
                logger.info(f"存储目录 '{directory}' 不存在，正在创建...")
                os.makedirs(directory)
                logger.info(f"目录 '{directory}' 创建成功。")
        except Exception as e:
            logger.error(f"创建存储目录时发生错误: {e}", exc_info=True)
            raise

    def _load_data(self) -> None:
        with self._lock:
            try:
                if os.path.exists(self.file_path):
                    with open(self.file_path, encoding="utf-8") as f:
                        content = f.read()
                        self._data = json.loads(content) if content else {}
                else:
                    self._data = {}
            except (json.JSONDecodeError, Exception) as e:
                logger.warning(f"从 '{self.file_path}' 加载数据失败: {e}，将初始化为空数据。")
                self._data = {}

    def _save_data(self) -> None:
        with self._lock:
            try:
                with open(self.file_path, "w", encoding="utf-8") as f:
                    json.dump(self._data, f, indent=4, ensure_ascii=False)
            except Exception as e:
                logger.error(f"向 '{self.file_path}' 保存数据时发生错误: {e}", exc_info=True)
                raise

    def get(self, key: str, default: Any | None = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """
        设置一个键值对。
        如果键已存在，则覆盖它的值；如果不存在，则创建新的键值对。
        这是“设置”或“更新”操作。
        """
        logger.debug(f"在 '{self.name}' 存储中设置值: key='{key}'。")
        self._data[key] = value
        self._save_data()

    def add(self, key: str, value: Any) -> bool:
        """
        添加一个新的键值对。
        只有当键不存在时，才会添加成功。如果键已存在，则不进行任何操作。
        这是专门的“新增”操作，满足你的要求了吧，主人？

        Returns:
            bool: 如果成功添加则返回 True，如果键已存在则返回 False。
        """
        if key not in self._data:
            logger.debug(f"在 '{self.name}' 存储中新增值: key='{key}'。")
            self._data[key] = value
            self._save_data()
            return True
        logger.warning(f"尝试为已存在的键 '{key}' 新增值，操作被忽略。")
        return False

    def update(self, data: dict[str, Any]) -> None:
        self._data.update(data)
        self._save_data()

    def delete(self, key: str) -> bool:
        if key in self._data:
            del self._data[key]
            self._save_data()
            return True
        return False

    def get_all(self) -> dict[str, Any]:
        return self._data.copy()

    def clear(self) -> None:
        logger.warning(f"插件 '{self.name}' 的本地存储将被清空！")
        self._data = {}
        self._save_data()


# --- 对外暴露的API函数 ---


def get_local_storage(name: str) -> "PluginStorage":
    """
    获取一个专属于插件的本地存储实例。
    这是插件与本地存储功能交互的唯一入口。
    """
    if not isinstance(name, str) or not name:
        logger.error("获取本地存储失败：插件名称(name)必须是一个非空字符串。")
        raise ValueError("插件名称(name)不能为空字符串。")

    try:
        storage_instance = PluginStorageManager.get_storage(name)
        return storage_instance
    except Exception as e:
        logger.critical(f"为插件 '{name}' 提供本地存储实例时发生严重错误: {e}", exc_info=True)
        raise
