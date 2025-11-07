"""
@File    :   storage_api.py
@Time    :   2025/10/25 11:03:15
@Author  :   墨墨
@Version :   2.0
@Desc    :   提供给插件使用的本地存储API（集成版）
"""

import atexit
import os
import threading
from typing import Any, ClassVar

import orjson

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

    _instances: ClassVar[dict[str, "PluginStorage"] ] = {}
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

    @classmethod
    def shutdown(cls):
        """
        在程序退出时，强制保存所有插件实例中未保存的数据。
        哼，别想留下任何烂摊子给我。
        """
        logger.info("正在执行存储管理器关闭程序，检查并保存所有未写入的数据...")
        with cls._lock:
            for name, instance in cls._instances.items():
                logger.debug(f"正在检查插件 '{name}' 的数据...")
                # 直接调用实例的_save_data，它会检查_dirty标志
                instance._save_data()
        logger.info("所有插件数据均已妥善保存。")


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
        # --- 延迟写入新增属性 ---
        self._dirty = False  # 数据是否被修改过的标志
        self._write_timer: threading.Timer | None = None  # 延迟写入的计时器
        self.save_delay = 2  # 延迟2秒写入

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
                        self._data = orjson.loads(content) if content else {}
                else:
                    self._data = {}
            except (orjson.JSONDecodeError, Exception) as e:
                logger.warning(f"从 '{self.file_path}' 加载数据失败: {e}，将初始化为空数据。")
                self._data = {}

    def _schedule_save(self) -> None:
        """安排一次延迟保存操作。"""
        with self._lock:
            self._dirty = True
            # 如果已经有计时器在跑，就取消它，用新的覆盖
            if self._write_timer:
                self._write_timer.cancel()
            self._write_timer = threading.Timer(self.save_delay, self._save_data)
            self._write_timer.start()
            logger.debug(f"插件 '{self.name}' 的数据修改已暂存，计划在 {self.save_delay} 秒后写入磁盘。")

    def _save_data(self) -> None:
        with self._lock:
            if not self._dirty:
                return  # 数据没有被修改，不需要保存

            try:
                with open(self.file_path, "w", encoding="utf-8") as f:
                    f.write(orjson.dumps(self._data, option=orjson.OPT_INDENT_2 | orjson.OPT_NON_STR_KEYS).decode("utf-8"))
                self._dirty = False  # 保存后重置标志
                logger.debug(f"插件 '{self.name}' 的数据已成功保存到磁盘。")
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
        self._schedule_save()

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
            self._schedule_save()
            return True
        logger.warning(f"尝试为已存在的键 '{key}' 新增值，操作被忽略。")
        return False

    def update(self, data: dict[str, Any]) -> None:
        self._data.update(data)
        self._schedule_save()

    def delete(self, key: str) -> bool:
        if key in self._data:
            del self._data[key]
            self._schedule_save()
            return True
        return False

    def get_all(self) -> dict[str, Any]:
        return self._data.copy()

    def clear(self) -> None:
        logger.warning(f"插件 '{self.name}' 的本地存储将被清空！")
        self._data = {}
        self._schedule_save()


# --- 对外暴露的API函数 ---


# 注册退出时的清理函数
atexit.register(PluginStorageManager.shutdown)


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
