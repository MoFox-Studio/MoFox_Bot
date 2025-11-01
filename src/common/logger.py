# 使用基于时间戳的文件处理器，简单的轮转份数限制

import logging
import tarfile
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import orjson
import structlog
import tomlkit

# 创建logs目录
LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

# 全局handler实例，避免重复创建（可能为None表示禁用文件日志）
_file_handler: logging.Handler | None = None
_console_handler: logging.Handler | None = None

# 动态 logger 元数据注册表 (name -> {alias:str|None, color:str|None})
_LOGGER_META_LOCK = threading.Lock()
_LOGGER_META: dict[str, dict[str, str | None]] = {}


def _normalize_color(color: str | None) -> str | None:
    """接受 ANSI 码 / #RRGGBB / rgb(r,g,b) / 颜色名(直接返回) -> ANSI 码.
    不做复杂解析，只支持 #RRGGBB 转 24bit ANSI。
    """
    if not color:
        return None
    color = color.strip()
    if color.startswith("\033["):
        return color  # 已经是ANSI
    if color.startswith("#") and len(color) == 7:
        try:
            r = int(color[1:3], 16)
            g = int(color[3:5], 16)
            b = int(color[5:7], 16)
            return f"\033[38;2;{r};{g};{b}m"
        except ValueError:
            return None
    # 简单 rgb(r,g,b)
    if color.lower().startswith("rgb(") and color.endswith(")"):
        try:
            nums = color[color.find("(") + 1 : -1].split(",")
            r, g, b = (int(x) for x in nums[:3])
            return f"\033[38;2;{r};{g};{b}m"
        except Exception:
            return None
    # 其他情况直接返回，假设是短ANSI或名称（控制台渲染器不做翻译，仅输出）
    return color


def _register_logger_meta(name: str, *, alias: str | None = None, color: str | None = None):
    """注册/更新 logger 元数据。"""
    if not name:
        return
    with _LOGGER_META_LOCK:
        meta = _LOGGER_META.setdefault(name, {"alias": None, "color": None})
        if alias is not None:
            meta["alias"] = alias
        if color is not None:
            meta["color"] = _normalize_color(color)


def get_logger_meta(name: str) -> dict[str, str | None]:
    with _LOGGER_META_LOCK:
        return _LOGGER_META.get(name, {"alias": None, "color": None}).copy()


def get_file_handler():
    """获取文件handler单例; 当 retention=0 时返回 None (禁用文件输出)。"""
    global _file_handler

    retention_days = LOG_CONFIG.get("file_retention_days", 30)
    if retention_days == 0:
        return None

    if _file_handler is None:
        # 确保日志目录存在
        LOG_DIR.mkdir(exist_ok=True)

        # 检查现有handler，避免重复创建
        root_logger = logging.getLogger()
        for handler in root_logger.handlers:
            if isinstance(handler, TimestampedFileHandler):
                _file_handler = handler
                return _file_handler

        _file_handler = TimestampedFileHandler(
            log_dir=LOG_DIR,
            max_bytes=5 * 1024 * 1024,  # 5MB
            backup_count=30,
            encoding="utf-8",
        )
        file_level = LOG_CONFIG.get("file_log_level", LOG_CONFIG.get("log_level", "INFO"))
        _file_handler.setLevel(getattr(logging, file_level.upper(), logging.INFO))
    return _file_handler


def get_console_handler():
    """获取控制台handler单例"""
    global _console_handler
    if _console_handler is None:
        _console_handler = logging.StreamHandler()
        # 设置控制台handler的日志级别
        console_level = LOG_CONFIG.get("console_log_level", LOG_CONFIG.get("log_level", "INFO"))
        _console_handler.setLevel(getattr(logging, console_level.upper(), logging.INFO))
    return _console_handler


class TimestampedFileHandler(logging.Handler):
    """基于时间戳的文件处理器，带简单大小轮转 + 旧文件压缩/保留策略。

    新策略:
      - 日志文件命名 app_YYYYmmdd_HHMMSS.log.jsonl
      - 轮转时会尝试压缩所有不再写入的 .log.jsonl -> .tar.gz
      - retention:
          file_retention_days = -1  永不删除
          file_retention_days = 0   上层禁用文件日志(不会实例化此类)
          file_retention_days = N>0 删除早于 N 天 (针对 .tar.gz 与遗留未压缩文件)
    """

    def __init__(self, log_dir, max_bytes=5 * 1024 * 1024, backup_count=30, encoding="utf-8"):
        super().__init__()
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True)
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self.encoding = encoding
        self._lock = threading.Lock()

        # 当前活跃的日志文件
        self.current_file = None
        self.current_stream = None
        self._init_current_file()

    def _init_current_file(self):
        """初始化当前日志文件"""
        # 使用微秒保证同一秒内多次轮转也获得不同文件名
        while True:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            candidate = self.log_dir / f"app_{timestamp}.log.jsonl"
            if not candidate.exists():
                self.current_file = candidate
                break
            # 极低概率碰撞，稍作等待
            time.sleep(0.001)
        self.current_stream = open(self.current_file, "a", encoding=self.encoding)

    def _should_rollover(self):
        """检查是否需要轮转"""
        if self.current_file and self.current_file.exists():
            return self.current_file.stat().st_size >= self.max_bytes
        return False

    def _do_rollover(self):
        """执行轮转：关闭当前文件 -> 立即创建新文件 -> 压缩旧文件 -> 清理过期。

        这样可以避免旧文件因为 self.current_file 仍指向它而被 _compress_stale_logs 跳过。
        """
        if self.current_stream:
            self.current_stream.close()
        # 记录旧文件引用，方便调试（暂不使用变量）
        self._init_current_file()  # 先创建新文件，确保后续压缩不会跳过刚关闭的旧文件
        try:
            self._compress_stale_logs()
            self._cleanup_old_files()
        except Exception as e:
            print(f"[日志轮转] 轮转过程出错: {e}")

    def _compress_stale_logs(self):  # sourcery skip: extract-method
        """将不再写入且未压缩的 .log.jsonl 文件压缩成 .tar.gz。"""
        try:
            for f in self.log_dir.glob("app_*.log.jsonl"):
                if f == self.current_file:
                    continue
                tar_path = f.with_suffix(f.suffix + ".tar.gz")  # .log.jsonl.tar.gz
                if tar_path.exists():
                    continue
                # 压缩
                try:
                    with tarfile.open(tar_path, "w:gz") as tf:
                        tf.add(f, arcname=f.name)
                    f.unlink(missing_ok=True)
                except Exception as e:
                    print(f"[日志压缩] 压缩 {f.name} 失败: {e}")
        except Exception as e:
            print(f"[日志压缩] 过程出错: {e}")

    def _cleanup_old_files(self):
        """按 retention 天数删除压缩包/遗留文件。"""
        retention_days = LOG_CONFIG.get("file_retention_days", 30)
        if retention_days in (-1, 0):
            return  # -1 永不删除；0 在外层已禁用
        cutoff = datetime.now() - timedelta(days=retention_days)
        try:
            for f in self.log_dir.glob("app_*.log.jsonl*"):
                if f == self.current_file:
                    continue
                try:
                    mtime = datetime.fromtimestamp(f.stat().st_mtime)
                    if mtime < cutoff:
                        f.unlink(missing_ok=True)
                except Exception as e:
                    print(f"[日志清理] 删除 {f} 失败: {e}")
        except Exception as e:
            print(f"[日志清理] 清理过程出错: {e}")

    def emit(self, record):
        """发出日志记录"""
        try:
            with self._lock:
                # 检查是否需要轮转
                if self._should_rollover():
                    self._do_rollover()

                # 写入日志
                if self.current_stream:
                    msg = self.format(record)
                    self.current_stream.write(msg + "\n")
                    self.current_stream.flush()

        except Exception:
            self.handleError(record)

    def close(self):
        """关闭处理器"""
        with self._lock:
            if self.current_stream:
                self.current_stream.close()
                self.current_stream = None
        super().close()


# 旧的轮转文件处理器已移除，现在使用基于时间戳的处理器


def close_handlers():
    """安全关闭所有handler"""
    global _file_handler, _console_handler

    if _file_handler:
        _file_handler.close()
        _file_handler = None

    if _console_handler:
        _console_handler.close()
        _console_handler = None


def remove_duplicate_handlers():  # sourcery skip: for-append-to-extend, list-comprehension
    """移除重复的handler，特别是文件handler"""
    root_logger = logging.getLogger()

    # 收集所有时间戳文件handler
    file_handlers = [handler for handler in root_logger.handlers[:] if isinstance(handler, TimestampedFileHandler)]

    # 如果有多个文件handler，保留第一个，关闭其他的
    if len(file_handlers) > 1:
        print(f"[日志系统] 检测到 {len(file_handlers)} 个重复的文件handler，正在清理...")
        for i, handler in enumerate(file_handlers[1:], 1):
            print(f"[日志系统] 关闭重复的文件handler {i}")
            root_logger.removeHandler(handler)
            handler.close()

        # 更新全局引用
        global _file_handler
        _file_handler = file_handlers[0]


# 读取日志配置
def load_log_config():  # sourcery skip: use-contextlib-suppress
    """从配置文件加载日志设置"""
    config_path = Path("config/bot_config.toml")
    default_config = {
        "date_style": "m-d H:i:s",
        "log_level_style": "lite",
        "color_text": "full",
        "log_level": "INFO",  # 全局日志级别（向下兼容）
        "console_log_level": "INFO",  # 控制台日志级别
        "file_log_level": "DEBUG",  # 文件日志级别
        "file_retention_days": 30,  # 文件日志保留天数，0=禁用文件日志，-1=永不删除
        "suppress_libraries": [
            "faiss",
            "httpx",
            "urllib3",
            "asyncio",
            "websockets",
            "httpcore",
            "requests",
            "peewee",
            "openai",
            "uvicorn",
            "rjieba",
        ],
        "library_log_levels": {"aiohttp": "WARNING"},
    }

    # 误加的即刻线程启动已移除；真正的线程在 start_log_cleanup_task 中按午夜调度

    try:
        if config_path.exists():
            with open(config_path, encoding="utf-8") as f:
                config = tomlkit.load(f)
                return config.get("log", default_config)
    except Exception as e:
        print(f"[日志系统] 加载日志配置失败: {e}")
        pass

    return default_config


LOG_CONFIG = load_log_config()


def get_timestamp_format():
    """将配置中的日期格式转换为Python格式"""
    date_style = LOG_CONFIG.get("date_style", "Y-m-d H:i:s")
    # 转换PHP风格的日期格式到Python格式
    format_map = {
        "Y": "%Y",  # 4位年份
        "m": "%m",  # 月份（01-12）
        "d": "%d",  # 日期（01-31）
        "H": "%H",  # 小时（00-23）
        "i": "%M",  # 分钟（00-59）
        "s": "%S",  # 秒数（00-59）
    }

    python_format = date_style
    for php_char, python_char in format_map.items():
        python_format = python_format.replace(php_char, python_char)

    return python_format


def configure_third_party_loggers():
    """配置第三方库的日志级别"""
    # 设置根logger级别为所有handler中最低的级别，确保所有日志都能被捕获
    console_level = LOG_CONFIG.get("console_log_level", LOG_CONFIG.get("log_level", "INFO"))
    file_level = LOG_CONFIG.get("file_log_level", LOG_CONFIG.get("log_level", "INFO"))

    # 获取最低级别（DEBUG < INFO < WARNING < ERROR < CRITICAL）
    console_level_num = getattr(logging, console_level.upper(), logging.INFO)
    file_level_num = getattr(logging, file_level.upper(), logging.INFO)
    min_level = min(console_level_num, file_level_num)

    root_logger = logging.getLogger()
    root_logger.setLevel(min_level)

    # 完全屏蔽的库
    suppress_libraries = LOG_CONFIG.get("suppress_libraries", [])
    for lib_name in suppress_libraries:
        lib_logger = logging.getLogger(lib_name)
        lib_logger.setLevel(logging.CRITICAL + 1)  # 设置为比CRITICAL更高的级别，基本屏蔽所有日志
        lib_logger.propagate = False  # 阻止向上传播

    # 设置特定级别的库
    library_log_levels = LOG_CONFIG.get("library_log_levels", {})
    for lib_name, level_name in library_log_levels.items():
        lib_logger = logging.getLogger(lib_name)
        level = getattr(logging, level_name.upper(), logging.WARNING)
        lib_logger.setLevel(level)


def reconfigure_existing_loggers():
    """重新配置所有已存在的logger，解决加载顺序问题"""
    # 获取根logger
    root_logger = logging.getLogger()

    # 重新设置根logger的所有handler的格式化器
    for handler in root_logger.handlers:
        if isinstance(handler, TimestampedFileHandler):
            handler.setFormatter(file_formatter)
        elif isinstance(handler, logging.StreamHandler):
            handler.setFormatter(console_formatter)

    # 遍历所有已存在的logger并重新配置
    logger_dict = logging.getLogger().manager.loggerDict
    for name, logger_obj in logger_dict.items():
        if isinstance(logger_obj, logging.Logger):
            # 检查是否是第三方库logger
            suppress_libraries = LOG_CONFIG.get("suppress_libraries", [])
            library_log_levels = LOG_CONFIG.get("library_log_levels", {})

            # 如果在屏蔽列表中
            if any(name.startswith(lib) for lib in suppress_libraries):
                logger_obj.setLevel(logging.CRITICAL + 1)
                logger_obj.propagate = False
                continue

            # 如果在特定级别设置中
            for lib_name, level_name in library_log_levels.items():
                if name.startswith(lib_name):
                    level = getattr(logging, level_name.upper(), logging.WARNING)
                    logger_obj.setLevel(level)
                    break

            # 强制清除并重新设置所有handler
            original_handlers = logger_obj.handlers[:]
            for handler in original_handlers:
                # 安全关闭handler
                if hasattr(handler, "close"):
                    handler.close()
                logger_obj.removeHandler(handler)

            # 如果logger没有handler，让它使用根logger的handler（propagate=True）
            if not logger_obj.handlers:
                logger_obj.propagate = True

            # 如果logger有自己的handler，重新配置它们（避免重复创建文件handler）
            for handler in original_handlers:
                if isinstance(handler, TimestampedFileHandler):
                    # 不重新添加，让它使用根logger的文件handler
                    continue
                elif isinstance(handler, logging.StreamHandler):
                    handler.setFormatter(console_formatter)
                    logger_obj.addHandler(handler)


###########################
# 默认颜色 / 别名 (仍然保留但可被动态覆盖)
###########################

DEFAULT_MODULE_COLORS = {
    # 核心模块
    "main": "\033[1;97m",  # 亮白色+粗体 (主程序)
    "api": "\033[92m",  # 亮绿色
    "emoji": "\033[38;5;214m",  # 橙黄色，偏向橙色但与replyer和action_manager不同
    "chat": "\033[92m",  # 亮蓝色
    "config": "\033[93m",  # 亮黄色
    "common": "\033[95m",  # 亮紫色
    "tools": "\033[96m",  # 亮青色
    "lpmm": "\033[96m",
    "plugin_system": "\033[91m",  # 亮红色
    "person_info": "\033[32m",  # 绿色
    "individuality": "\033[94m",  # 显眼的亮蓝色
    "manager": "\033[35m",  # 紫色
    "llm_models": "\033[36m",  # 青色
    "remote": "\033[38;5;242m",  # 深灰色，更不显眼
    "planner": "\033[36m",
    "memory": "\033[38;5;117m",  # 天蓝色
    "hfc": "\033[38;5;81m",  # 稍微暗一些的青色，保持可读
    "action_manager": "\033[38;5;208m",  # 橙色，不与replyer重复
    "message_manager": "\033[38;5;27m",  # 深蓝色，消息管理器
    "chatter_manager": "\033[38;5;129m",  # 紫色，聊天管理器
    "chatter_interest_scoring": "\033[38;5;214m",  # 橙黄色，兴趣评分
    "plan_executor": "\033[38;5;172m",  # 橙褐色，计划执行器
    # 关系系统
    "relation": "\033[38;5;139m",  # 柔和的紫色，不刺眼
    # 聊天相关模块
    "normal_chat": "\033[38;5;81m",  # 亮蓝绿色
    "heartflow": "\033[38;5;175m",  # 柔和的粉色，不显眼但保持粉色系
    "sub_heartflow": "\033[38;5;207m",  # 粉紫色
    "subheartflow_manager": "\033[38;5;201m",  # 深粉色
    "background_tasks": "\033[38;5;240m",  # 灰色
    "chat_message": "\033[38;5;45m",  # 青色
    "chat_stream": "\033[38;5;51m",  # 亮青色
    "sender": "\033[38;5;67m",  # 稍微暗一些的蓝色，不显眼
    "message_storage": "\033[38;5;33m",  # 深蓝色
    "expressor": "\033[38;5;166m",  # 橙色
    # 专注聊天模块
    "replyer": "\033[38;5;166m",  # 橙色
    "memory_activator": "\033[38;5;117m",  # 天蓝色
    # 插件系统
    "plugins": "\033[31m",  # 红色
    "plugin_api": "\033[33m",  # 黄色
    "plugin_manager": "\033[38;5;208m",  # 红色
    "base_plugin": "\033[38;5;202m",  # 橙红色
    "send_api": "\033[38;5;208m",  # 橙色
    "base_command": "\033[38;5;208m",  # 橙色
    "component_registry": "\033[38;5;214m",  # 橙黄色
    "stream_api": "\033[38;5;220m",  # 黄色
    "plugin_hot_reload": "\033[38;5;226m",  # 品红色
    "config_api": "\033[38;5;226m",  # 亮黄色
    "heartflow_api": "\033[38;5;154m",  # 黄绿色
    "action_apis": "\033[38;5;118m",  # 绿色
    "independent_apis": "\033[38;5;82m",  # 绿色
    "llm_api": "\033[38;5;46m",  # 亮绿色
    "database_api": "\033[38;5;10m",  # 绿色
    "utils_api": "\033[38;5;14m",  # 青色
    "message_api": "\033[38;5;6m",  # 青色
    # 管理器模块
    "async_task_manager": "\033[38;5;129m",  # 紫色
    "mood": "\033[38;5;135m",  # 紫红色
    "local_storage": "\033[38;5;141m",  # 紫色
    "willing": "\033[38;5;147m",  # 浅紫色
    # 工具模块
    "tool_use": "\033[38;5;172m",  # 橙褐色
    "tool_executor": "\033[38;5;172m",  # 橙褐色
    "base_tool": "\033[38;5;178m",  # 金黄色
    # 工具和实用模块
    "prompt_build": "\033[38;5;105m",  # 紫色
    "chat_utils": "\033[38;5;111m",  # 蓝色
    "chat_image": "\033[38;5;117m",  # 浅蓝色
    "maibot_statistic": "\033[38;5;129m",  # 紫色
    # 特殊功能插件
    "mute_plugin": "\033[38;5;240m",  # 灰色
    "core_actions": "\033[38;5;117m",  # 深红色
    "tts_action": "\033[38;5;58m",  # 深黄色
    "doubao_pic_plugin": "\033[38;5;64m",  # 深绿色
    # Action组件
    "no_reply_action": "\033[38;5;214m",  # 亮橙色，显眼但不像警告
    "reply_action": "\033[38;5;46m",  # 亮绿色
    "base_action": "\033[38;5;250m",  # 浅灰色
    # 数据库和消息
    "database_model": "\033[38;5;94m",  # 橙褐色
    "database": "\033[38;5;46m",  # 橙褐色
    "maim_message": "\033[38;5;140m",  # 紫褐色
    # 日志系统
    "logger": "\033[38;5;8m",  # 深灰色
    "confirm": "\033[1;93m",  # 黄色+粗体
    # 模型相关
    "model_utils": "\033[38;5;164m",  # 紫红色
    "relationship_fetcher": "\033[38;5;170m",  # 浅紫色
    "relationship_builder": "\033[38;5;93m",  # 浅蓝色
    "sqlalchemy_init": "\033[38;5;105m",  #
    "sqlalchemy_models": "\033[38;5;105m",
    "sqlalchemy_database_api": "\033[38;5;105m",
    # s4u
    "context_web_api": "\033[38;5;240m",  # 深灰色
    "S4U_chat": "\033[92m",  # 亮绿色
    # API相关扩展
    "chat_api": "\033[38;5;34m",  # 深绿色
    "emoji_api": "\033[38;5;40m",  # 亮绿色
    "generator_api": "\033[38;5;28m",  # 森林绿
    "person_api": "\033[38;5;22m",  # 深绿色
    "tool_api": "\033[38;5;76m",  # 绿色
    "OpenAI客户端": "\033[38;5;81m",
    "Gemini客户端": "\033[38;5;81m",
    # 插件系统扩展
    "plugin_base": "\033[38;5;196m",  # 红色
    "base_event_handler": "\033[38;5;203m",  # 粉红色
    "events_manager": "\033[38;5;209m",  # 橙红色
    "global_announcement_manager": "\033[38;5;215m",  # 浅橙色
    # 工具和依赖管理
    "dependency_config": "\033[38;5;24m",  # 深蓝色
    "dependency_manager": "\033[38;5;30m",  # 深青色
    "manifest_utils": "\033[38;5;39m",  # 蓝色
    "schedule_manager": "\033[38;5;27m",  # 深蓝色
    "monthly_plan_manager": "\033[38;5;171m",
    "plan_manager": "\033[38;5;171m",
    "llm_generator": "\033[38;5;171m",
    "schedule_bridge": "\033[38;5;171m",
    "sleep_manager": "\033[38;5;171m",
    "official_configs": "\033[38;5;171m",
    "mmc_com_layer": "\033[38;5;67m",
    # 聊天和多媒体扩展
    "chat_voice": "\033[38;5;87m",  # 浅青色
    "typo_gen": "\033[38;5;123m",  # 天蓝色
    "utils_video": "\033[38;5;75m",  # 亮蓝色
    "ReplyerManager": "\033[38;5;173m",  # 浅橙色
    "relationship_builder_manager": "\033[38;5;176m",  # 浅紫色
    "expression_selector": "\033[38;5;176m",
    "chat_message_builder": "\033[38;5;176m",
    # MaiZone QQ空间相关
    "MaiZone": "\033[38;5;98m",  # 紫色
    "MaiZone-Monitor": "\033[38;5;104m",  # 深紫色
    "MaiZone.ConfigLoader": "\033[38;5;110m",  # 蓝紫色
    "MaiZone-Scheduler": "\033[38;5;134m",  # 紫红色
    "MaiZone-Utils": "\033[38;5;140m",  # 浅紫色
    # MaiZone Refactored
    "MaiZone.HistoryUtils": "\033[38;5;140m",
    "MaiZone.SchedulerService": "\033[38;5;134m",
    "MaiZone.QZoneService": "\033[38;5;98m",
    "MaiZone.MonitorService": "\033[38;5;104m",
    "MaiZone.ImageService": "\033[38;5;110m",
    "MaiZone.CookieService": "\033[38;5;140m",
    "MaiZone.ContentService": "\033[38;5;110m",
    "MaiZone.Plugin": "\033[38;5;98m",
    "MaiZone.SendFeedCommand": "\033[38;5;134m",
    "MaiZone.SendFeedAction": "\033[38;5;134m",
    "MaiZone.ReadFeedAction": "\033[38;5;134m",
    # 网络工具
    "web_surfing_tool": "\033[38;5;130m",  # 棕色
    "tts": "\033[38;5;136m",  # 浅棕色
    "poke_plugin": "\033[38;5;136m",
    "set_emoji_like_plugin": "\033[38;5;136m",
    # mais4u系统扩展
    "s4u_config": "\033[38;5;18m",  # 深蓝色
    "action": "\033[38;5;52m",  # 深红色（mais4u的action）
    "context_web": "\033[38;5;58m",  # 深黄色
    "gift_manager": "\033[38;5;161m",  # 粉红色
    "prompt": "\033[38;5;99m",  # 紫色（mais4u的prompt）
    "super_chat_manager": "\033[38;5;125m",  # 紫红色
    "watching": "\033[38;5;131m",  # 深橙色
    "offline_llm": "\033[38;5;236m",  # 深灰色
    "s4u_stream_generator": "\033[38;5;60m",  # 深紫色
    # 其他工具
    "消息压缩工具": "\033[38;5;244m",  # 灰色
    "lpmm_get_knowledge_tool": "\033[38;5;102m",  # 绿色
    "message_chunker": "\033[38;5;244m",
    "plan_generator": "\033[38;5;171m",
    "Permission": "\033[38;5;196m",
    "web_search_plugin": "\033[38;5;130m",
    "url_parser_tool": "\033[38;5;130m",
    "api_key_manager": "\033[38;5;130m",
    "tavily_engine": "\033[38;5;130m",
    "exa_engine": "\033[38;5;130m",
    "ddg_engine": "\033[38;5;130m",
    "bing_engine": "\033[38;5;130m",
    "vector_instant_memory_v2": "\033[38;5;117m",
    "async_memory_optimizer": "\033[38;5;117m",
    "async_instant_memory_wrapper": "\033[38;5;117m",
    "action_diagnostics": "\033[38;5;214m",
    "anti_injector.message_processor": "\033[38;5;196m",
    "anti_injector.user_ban": "\033[38;5;196m",
    "anti_injector.statistics": "\033[38;5;196m",
    "anti_injector.decision_maker": "\033[38;5;196m",
    "anti_injector.counter_attack": "\033[38;5;196m",
    "hfc.processor": "\033[38;5;81m",
    "hfc.normal_mode": "\033[38;5;81m",
    "wakeup": "\033[38;5;81m",
    "cache_manager": "\033[38;5;244m",
    "monthly_plan_db": "\033[38;5;94m",
    "db_migration": "\033[38;5;94m",
    "小彩蛋": "\033[38;5;214m",
    "AioHTTP-Gemini客户端": "\033[38;5;81m",
    "napcat_adapter": "\033[38;5;67m",  # 柔和的灰蓝色，不刺眼且低调
    "event_manager": "\033[38;5;79m",  # 柔和的蓝绿色，稍微醒目但不刺眼
}

DEFAULT_MODULE_ALIASES = {
    # 核心模块
    "individuality": "人格特质",
    "emoji": "表情包",
    "no_reply_action": "摸鱼",
    "reply_action": "回复",
    "action_manager": "动作",
    "memory_activator": "记忆",
    "tool_use": "工具",
    "expressor": "表达方式",
    "plugin_hot_reload": "热重载",
    "database": "数据库",
    "database_model": "数据库",
    "mood": "情绪",
    "memory": "记忆",
    "tool_executor": "工具",
    "hfc": "聊天节奏",
    "chat": "所见",
    "anti_injector": "反注入",
    "anti_injector.detector": "反注入检测",
    "anti_injector.shield": "反注入加盾",
    "plugin_manager": "插件",
    "relationship_builder": "关系",
    "llm_models": "模型",
    "person_info": "人物",
    "chat_stream": "聊天流",
    "message_manager": "消息管理",
    "chatter_manager": "聊天管理",
    "chatter_interest_scoring": "兴趣评分",
    "plan_executor": "计划执行",
    "planner": "规划器",
    "replyer": "言语",
    "config": "配置",
    "main": "主程序",
    # API相关扩展
    "chat_api": "聊天接口",
    "emoji_api": "表情接口",
    "generator_api": "生成接口",
    "person_api": "人物接口",
    "tool_api": "工具接口",
    # 插件系统扩展
    "plugin_base": "插件基类",
    "base_event_handler": "事件处理",
    "event_manager": "事件管理器",
    "global_announcement_manager": "全局通知",
    # 工具和依赖管理
    "dependency_config": "依赖配置",
    "dependency_manager": "依赖管理",
    "manifest_utils": "清单工具",
    "schedule_manager": "规划系统-日程表管理",
    "monthly_plan_manager": "规划系统-月度计划",
    "plan_manager": "规划系统-计划管理",
    "llm_generator": "规划系统-LLM生成",
    "schedule_bridge": "计划桥接",
    "sleep_manager": "睡眠管理",
    "official_configs": "官方配置",
    "mmc_com_layer": "MMC通信层",
    # 聊天和多媒体扩展
    "chat_voice": "语音处理",
    "typo_gen": "错字生成",
    "src.chat.utils.utils_video": "视频分析",
    "ReplyerManager": "回复管理",
    "relationship_builder_manager": "关系管理",
    # MaiZone QQ空间相关
    "MaiZone": "Mai空间",
    "MaiZone-Monitor": "Mai空间监控",
    "MaiZone.ConfigLoader": "Mai空间配置",
    "MaiZone-Scheduler": "Mai空间调度",
    "MaiZone-Utils": "Mai空间工具",
    # MaiZone Refactored
    "MaiZone.HistoryUtils": "Mai空间历史",
    "MaiZone.SchedulerService": "Mai空间调度",
    "MaiZone.QZoneService": "Mai空间服务",
    "MaiZone.MonitorService": "Mai空间监控",
    "MaiZone.ImageService": "Mai空间图片",
    "MaiZone.CookieService": "Mai空间饼干",
    "MaiZone.ContentService": "Mai空间内容",
    "MaiZone.Plugin": "Mai空间插件",
    "MaiZone.SendFeedCommand": "Mai空间发说说",
    "MaiZone.SendFeedAction": "Mai空间发说说",
    "MaiZone.ReadFeedAction": "Mai空间读说说",
    # 网络工具
    "web_surfing_tool": "网络搜索",
    # napcat ada
    "napcat_adapter": "Napcat 适配器",
    "tts": "语音合成",
    # mais4u系统扩展
    "s4u_config": "直播配置",
    "action": "直播动作",
    "context_web": "网络上下文",
    "gift_manager": "礼物管理",
    "prompt": "直播提示",
    "super_chat_manager": "醒目留言",
    "watching": "观看状态",
    "offline_llm": "离线模型",
    "s4u_stream_generator": "直播生成",
    # 其他工具
    "消息压缩工具": "消息压缩",
    "lpmm_get_knowledge_tool": "知识获取",
    "message_chunker": "消息分块",
    "plan_generator": "计划生成",
    "Permission": "权限管理",
    "web_search_plugin": "网页搜索插件",
    "url_parser_tool": "URL解析工具",
    "api_key_manager": "API密钥管理",
    "tavily_engine": "Tavily引擎",
    "exa_engine": "Exa引擎",
    "ddg_engine": "DDG引擎",
    "bing_engine": "Bing引擎",
    "vector_instant_memory_v2": "向量瞬时记忆",
    "async_memory_optimizer": "异步记忆优化器",
    "async_instant_memory_wrapper": "异步瞬时记忆包装器",
    "action_diagnostics": "动作诊断",
    "anti_injector.message_processor": "反注入消息处理器",
    "anti_injector.user_ban": "反注入用户封禁",
    "anti_injector.statistics": "反注入统计",
    "anti_injector.decision_maker": "反注入决策者",
    "anti_injector.counter_attack": "反注入反击",
    "hfc.processor": "聊天节奏处理器",
    "hfc.normal_mode": "聊天节奏普通模式",
    "wakeup": "唤醒",
    "cache_manager": "缓存管理",
    "monthly_plan_db": "月度计划数据库",
    "db_migration": "数据库迁移",
    "小彩蛋": "小彩蛋",
    "AioHTTP-Gemini客户端": "AioHTTP-Gemini客户端",
}

RESET_COLOR = "\033[0m"


class ModuleColoredConsoleRenderer:
    """自定义控制台渲染器，为不同模块提供不同颜色"""

    def __init__(self, colors=True):
        # sourcery skip: merge-duplicate-blocks, remove-redundant-if
        self._colors = colors
        self._config = LOG_CONFIG

        # 日志级别颜色
        self._level_colors = {
            "debug": "\033[38;5;208m",  # 橙色
            "info": "\033[38;5;117m",  # 天蓝色
            "success": "\033[32m",  # 绿色
            "warning": "\033[33m",  # 黄色
            "error": "\033[31m",  # 红色
            "critical": "\033[35m",  # 紫色
        }

        # 根据配置决定是否启用颜色
        color_text = self._config.get("color_text", "title")
        if color_text == "none":
            self._colors = False
        elif color_text == "title":
            self._enable_module_colors = True
            self._enable_level_colors = False
            self._enable_full_content_colors = False
        elif color_text == "full":
            self._enable_module_colors = True
            self._enable_level_colors = True
            self._enable_full_content_colors = True
        else:
            self._enable_module_colors = True
            self._enable_level_colors = False
            self._enable_full_content_colors = False

    def __call__(self, logger, method_name, event_dict):
        # sourcery skip: merge-duplicate-blocks
        """渲染日志消息"""
        # 获取基本信息
        timestamp = event_dict.get("timestamp", "")
        level = event_dict.get("level", "info")
        logger_name = event_dict.get("logger_name", "")
        event = event_dict.get("event", "")

        # 构建输出
        parts = []

        # 日志级别样式配置
        log_level_style = self._config.get("log_level_style", "lite")
        level_color = self._level_colors.get(level.lower(), "") if self._colors else ""

        # 时间戳（lite模式下按级别着色）
        if timestamp:
            if log_level_style == "lite" and level_color:
                timestamp_part = f"{level_color}{timestamp}{RESET_COLOR}"
            else:
                timestamp_part = timestamp
            parts.append(timestamp_part)

        # 日志级别显示（根据配置样式）
        if log_level_style == "full":
            # 显示完整级别名并着色
            level_text = level.upper()
            if level_color:
                level_part = f"{level_color}[{level_text:>8}]{RESET_COLOR}"
            else:
                level_part = f"[{level_text:>8}]"
            parts.append(level_part)

        elif log_level_style == "compact":
            # 只显示首字母并着色
            level_text = level.upper()[0]
            if level_color:
                level_part = f"{level_color}[{level_text:>8}]{RESET_COLOR}"
            else:
                level_part = f"[{level_text:>8}]"
            parts.append(level_part)

        # lite模式不显示级别，只给时间戳着色

        # 获取模块颜色，用于full模式下的整体着色
        module_color = ""
        if self._colors and self._enable_module_colors and logger_name:
            # 动态优先，其次默认表
            meta = get_logger_meta(logger_name)
            module_color = meta.get("color") or DEFAULT_MODULE_COLORS.get(logger_name, "")

        # 模块名称（带颜色和别名支持）
        if logger_name:
            # 获取别名，如果没有别名则使用原名称
            # 若上面条件不成立需要再次获取 meta
            if "meta" not in locals():
                meta = get_logger_meta(logger_name)
            display_name = meta.get("alias") or DEFAULT_MODULE_ALIASES.get(logger_name, logger_name)

            if self._colors and self._enable_module_colors:
                if module_color:
                    module_part = f"{module_color}[{display_name}]{RESET_COLOR}"
                else:
                    module_part = f"[{display_name}]"
            else:
                module_part = f"[{display_name}]"
            parts.append(module_part)

        # 消息内容（确保转换为字符串）
        event_content = ""
        if isinstance(event, str):
            event_content = event
        elif isinstance(event, dict):
            # 如果是字典，格式化为可读字符串
            try:
                event_content = orjson.dumps(event).decode("utf-8")
            except (TypeError, ValueError):
                event_content = str(event)
        else:
            # 其他类型直接转换为字符串
            event_content = str(event)

        # 在full模式下为消息内容着色
        if self._colors and self._enable_full_content_colors:
            # 检查是否包含“内心思考:”
            if "内心思考:" in event_content:
                # 使用明亮的粉色
                thought_color = "\033[38;5;218m"
                # 分割消息内容
                prefix, thought = event_content.split("内心思考:", 1)

                # 前缀部分（“决定进行回复，”）使用模块颜色
                if module_color:
                    prefix_colored = f"{module_color}{prefix.strip()}{RESET_COLOR}"
                else:
                    prefix_colored = prefix.strip()

                # “内心思考”部分换行并使用专属颜色
                thought_colored = f"\n\n{thought_color}内心思考:{thought.strip()}{RESET_COLOR}\n"

                # 重新组合
                # parts.append(prefix_colored + thought_colored)
                # 将前缀和思考内容作为独立的part添加，避免它们之间出现多余的空格
                if prefix_colored:
                    parts.append(prefix_colored)
                parts.append(thought_colored)

            elif module_color:
                event_content = f"{module_color}{event_content}{RESET_COLOR}"
                parts.append(event_content)
            else:
                parts.append(event_content)
        else:
            parts.append(event_content)

        # 处理其他字段
        extras = []
        for key, value in event_dict.items():
            if key not in ("timestamp", "level", "logger_name", "event") and key not in ("color", "alias"):
                # 确保值也转换为字符串
                if isinstance(value, dict | list):
                    try:
                        value_str = orjson.dumps(value).decode("utf-8")
                    except (TypeError, ValueError):
                        value_str = str(value)
                else:
                    value_str = str(value)

                # 在full模式下为额外字段着色
                extra_field = f"{key}={value_str}"
                if self._colors and self._enable_full_content_colors and module_color:
                    extra_field = f"{module_color}{extra_field}{RESET_COLOR}"

                extras.append(extra_field)

        if extras:
            parts.append(" ".join(extras))

        return " ".join(parts)


# 配置标准logging以支持文件输出和压缩
# 使用单例handler避免重复创建
file_handler = get_file_handler()
console_handler = get_console_handler()

handlers = [h for h in (file_handler, console_handler) if h is not None]
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=handlers,
)


def add_logger_metadata(logger: Any, method_name: str, event_dict: dict):  # type: ignore[override]
    """structlog 自定义处理器: 注入 color / alias 字段 (用于 JSON 输出)。"""
    name = event_dict.get("logger_name")
    if name:
        meta = get_logger_meta(name)
        # 默认 fallback
        if meta.get("color") is None and name in DEFAULT_MODULE_COLORS:
            meta["color"] = DEFAULT_MODULE_COLORS[name]
        if meta.get("alias") is None and name in DEFAULT_MODULE_ALIASES:
            meta["alias"] = DEFAULT_MODULE_ALIASES[name]
        # 注入
        if meta.get("color"):
            event_dict["color"] = meta["color"]
        if meta.get("alias"):
            event_dict["alias"] = meta["alias"]
    return event_dict


def configure_structlog():
    """配置structlog，加入自定义 metadata 处理器。"""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.TimeStamper(fmt=get_timestamp_format(), utc=False),
            add_logger_metadata,  # 注入 color/alias
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


# 配置structlog
configure_structlog()

# 为文件输出配置JSON格式
file_formatter = structlog.stdlib.ProcessorFormatter(
    processor=structlog.processors.JSONRenderer(ensure_ascii=False),
    foreign_pre_chain=[
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ],
)

# 为控制台输出配置可读格式
console_formatter = structlog.stdlib.ProcessorFormatter(
    processor=ModuleColoredConsoleRenderer(colors=True),
    foreign_pre_chain=[
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt=get_timestamp_format(), utc=False),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ],
)

# 获取根logger并配置格式化器
root_logger = logging.getLogger()
for handler in root_logger.handlers:
    if isinstance(handler, TimestampedFileHandler):
        handler.setFormatter(file_formatter)
    else:
        handler.setFormatter(console_formatter)


# 立即配置日志系统，确保最早期的日志也使用正确格式
def _immediate_setup():
    """立即设置日志系统，在模块导入时就生效"""
    # 重新配置structlog
    configure_structlog()

    # 清除所有已有的handler，重新配置
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # 使用单例handler避免重复创建
    file_handler_local = get_file_handler()
    console_handler_local = get_console_handler()

    for h in (file_handler_local, console_handler_local):
        if h is not None:
            root_logger.addHandler(h)

    # 设置格式化器
    if file_handler_local is not None:
        file_handler_local.setFormatter(file_formatter)
    console_handler_local.setFormatter(console_formatter)

    # 清理重复的handler
    remove_duplicate_handlers()

    # 配置第三方库日志
    configure_third_party_loggers()

    # 重新配置所有已存在的logger
    reconfigure_existing_loggers()


# 立即执行配置
_immediate_setup()

raw_logger: structlog.stdlib.BoundLogger = structlog.get_logger()

binds: dict[str, Callable] = {}


def get_logger(name: str | None, *, color: str | None = None, alias: str | None = None) -> structlog.stdlib.BoundLogger:
    """获取/创建 structlog logger。

    新增:
      - color: 传入 ANSI / #RRGGBB / rgb(r,g,b) 以注册显示颜色
      - alias: 别名, 控制台模块显示 & JSON 中 alias 字段
    多次调用可更新元数据 (后调用覆盖之前的 color/alias, 仅覆盖给定的)
    """
    if name is None:
        return raw_logger
    if color is not None or alias is not None:
        _register_logger_meta(name, alias=alias, color=color)
    logger = binds.get(name)  # type: ignore
    if logger is None:
        logger = structlog.get_logger(name).bind(logger_name=name)  # type: ignore[assignment]
        binds[name] = logger
    return logger  # type: ignore[return-value]


def initialize_logging():
    """手动初始化日志系统，确保所有logger都使用正确的配置

    在应用程序的早期调用此函数，确保所有模块都使用统一的日志配置
    """
    global LOG_CONFIG
    LOG_CONFIG = load_log_config()
    # print(LOG_CONFIG)
    configure_third_party_loggers()
    reconfigure_existing_loggers()

    # 启动日志清理任务
    start_log_cleanup_task()

    # 输出初始化信息
    logger = get_logger("logger")
    console_level = LOG_CONFIG.get("console_log_level", LOG_CONFIG.get("log_level", "INFO"))
    file_level = LOG_CONFIG.get("file_log_level", LOG_CONFIG.get("log_level", "INFO"))

    logger.info("日志系统已初始化:")
    logger.info(f"  - 控制台级别: {console_level}")
    logger.info(f"  - 文件级别: {file_level}")
    retention_days = LOG_CONFIG.get("file_retention_days", 30)
    if retention_days == 0:
        retention_desc = "文件日志已禁用"
    elif retention_days == -1:
        retention_desc = "永不删除 (仅压缩旧文件)"
    else:
        retention_desc = f"保留 {retention_days} 天"
    logger.info(f"  - 文件保留策略: {retention_desc}")


def cleanup_old_logs():
    """压缩遗留未压缩的日志并按 retention 策略删除。"""
    retention_days = LOG_CONFIG.get("file_retention_days", 30)
    if retention_days == 0:
        return  # 已禁用
    try:
        # 先压缩(复用 handler 的逻辑, 但 handler 可能未创建——手动调用)
        try:
            for f in LOG_DIR.glob("app_*.log.jsonl"):
                # 当前写入文件无法可靠识别(仅 handler 知道); 粗略策略: 如果修改时间>5分钟也压缩
                if time.time() - f.stat().st_mtime < 300:
                    continue
                tar_path = f.with_suffix(f.suffix + ".tar.gz")
                if tar_path.exists():
                    continue
                with tarfile.open(tar_path, "w:gz") as tf:
                    tf.add(f, arcname=f.name)
                f.unlink(missing_ok=True)
        except Exception as e:
            logger = get_logger("logger")
            logger.warning(f"周期压缩日志时出错: {e}")

        if retention_days == -1:
            return  # 永不删除
        cutoff_date = datetime.now() - timedelta(days=retention_days)
        deleted_count = 0
        deleted_size = 0
        for log_file in LOG_DIR.glob("app_*.log.jsonl*"):
            try:
                file_time = datetime.fromtimestamp(log_file.stat().st_mtime)
                if file_time < cutoff_date:
                    size = log_file.stat().st_size
                    log_file.unlink(missing_ok=True)
                    deleted_count += 1
                    deleted_size += size
            except Exception as e:
                logger = get_logger("logger")
                logger.warning(f"清理日志文件 {log_file} 时出错: {e}")
        if deleted_count:
            logger = get_logger("logger")
            logger.info(
                f"清理 {deleted_count} 个过期日志 (≈{deleted_size / 1024 / 1024:.2f}MB), 保留策略={retention_days}天"
            )
    except Exception as e:
        logger = get_logger("logger")
        logger.error(f"清理旧日志文件时出错: {e}")


def start_log_cleanup_task():
    """启动日志压缩/清理任务：每天本地时间 00:00 运行一次。"""
    retention_days = LOG_CONFIG.get("file_retention_days", 30)
    if retention_days == 0:
        return  # 文件日志禁用无需周期任务

    def seconds_until_next_midnight() -> float:
        now = datetime.now()
        tomorrow = now + timedelta(days=1)
        midnight = datetime(year=tomorrow.year, month=tomorrow.month, day=tomorrow.day)
        return (midnight - now).total_seconds()

    def cleanup_task():
        # 首次等待到下一个本地午夜
        time.sleep(max(1, seconds_until_next_midnight()))
        while True:
            try:
                cleanup_old_logs()
            except Exception as e:
                print(f"[日志任务] 执行清理出错: {e}")
            # 再次等待到下一个午夜
            time.sleep(max(1, seconds_until_next_midnight()))

    threading.Thread(target=cleanup_task, daemon=True, name="log-cleanup").start()
    logger = get_logger("logger")
    if retention_days == -1:
        logger.info("已启动日志任务: 每天 00:00 压缩旧日志(不删除)")
    else:
        logger.info(f"已启动日志任务: 每天 00:00 压缩并删除早于 {retention_days} 天的日志")


def shutdown_logging():
    """优雅关闭日志系统，释放所有文件句柄"""
    logger = get_logger("logger")
    logger.info("正在关闭日志系统...")

    # 关闭所有handler
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        if hasattr(handler, "close"):
            handler.close()
        root_logger.removeHandler(handler)

    # 关闭全局handler
    close_handlers()

    # 关闭所有其他logger的handler
    logger_dict = logging.getLogger().manager.loggerDict
    for logger_obj in logger_dict.values():
        if isinstance(logger_obj, logging.Logger):
            for handler in logger_obj.handlers[:]:
                if hasattr(handler, "close"):
                    handler.close()
                logger_obj.removeHandler(handler)

    logger.info("日志系统已关闭")
