"""
Bot服务UI日志适配器
在最小侵入的情况下捕获Bot的日志并发送到UI
"""

import logging
import os
import sys
import threading
import time

# 添加MoFox-UI路径以导入ui_logger
ui_path = os.path.join(os.path.dirname(__file__), "..", "MoFox-UI")
if os.path.exists(ui_path):
    sys.path.insert(0, ui_path)
    try:
        from ui_logger import get_ui_logger
        ui_logger = get_ui_logger("Bot")
        UI_LOGGER_AVAILABLE = True
    except ImportError:
        UI_LOGGER_AVAILABLE = False
else:
    UI_LOGGER_AVAILABLE = False


class UILogHandler(logging.Handler):
    """自定义日志处理器，将日志发送到UI"""

    def __init__(self, max_retries=3):
        """
        初始化UI日志处理器

        Args:
            max_retries: 最大重试次数，默认3次
        """
        super().__init__()
        self.ui_logger = ui_logger if UI_LOGGER_AVAILABLE else None
        self.max_retries = max_retries
        self.retry_delay = 0.1  # 重试延迟时间（秒）

    def _send_log_with_retry(self, msg, level):
        """
        带重试机制的日志发送方法

        Args:
            msg: 日志消息
            level: 日志级别（'info', 'warning', 'error', 'debug'）

        Returns:
            bool: 发送是否成功
        """
        if not self.ui_logger:
            return False

        for attempt in range(self.max_retries):
            try:
                if level == "info":
                    self.ui_logger.info(msg)
                elif level == "warning":
                    self.ui_logger.warning(msg)
                elif level == "error":
                    self.ui_logger.error(msg)
                elif level == "debug":
                    self.ui_logger.debug(msg)
                else:
                    self.ui_logger.info(msg)

                return True
            except Exception as e:
                if attempt == self.max_retries - 1:
                    print(f"[UI日志适配器] 发送日志失败，已达最大重试次数 {self.max_retries}: {e}")
                    return False
                time.sleep(self.retry_delay)

        return False

    def emit(self, record):
        """
        处理日志记录（重写父类方法）

        Args:
            record: 日志记录对象
        """
        if not self.ui_logger:
            return

        try:
            msg = self.format(record)
            level_mapping = {
                "DEBUG": "debug",
                "INFO": "info",
                "WARNING": "warning",
                "ERROR": "error",
                "CRITICAL": "error",
            }
            ui_level = level_mapping.get(record.levelname, "info")

            # 过滤掉DEBUG日志
            if record.levelname == "DEBUG":
                return

            emoji_map = {"info": "📝", "warning": "⚠️", "error": "❌", "debug": "🔍"}
            formatted_msg = f"{emoji_map.get(ui_level, '📝')} {msg}"

            success = self._send_log_with_retry(formatted_msg, ui_level)
            # 可选：记录发送状态
            # if not success:
            #     print(f"[UI日志适配器] 日志发送失败: {ui_level} - {formatted_msg[:50]}...")

        except Exception:
            # 静默失败，不影响主程序
            pass


def setup_ui_logging():
    """设置UI日志处理器"""
    if not UI_LOGGER_AVAILABLE:
        print("[UI日志适配器] UI Logger不可用，跳过设置")
        return

    try:
        print("[UI日志适配器] 开始设置UI日志处理器...")

        root_logger = logging.getLogger()
        for handler in root_logger.handlers:
            if isinstance(handler, UILogHandler):
                print("[UI日志适配器] UI日志处理器已存在，跳过重复添加")
                return

        ui_handler = UILogHandler(max_retries=3)
        ui_handler.setLevel(logging.INFO)
        root_logger.addHandler(ui_handler)

        print(f"[UI日志适配器] UI日志处理器已添加到根日志器，当前处理器数量: {len(root_logger.handlers)}")
        print(f"[UI日志适配器] 最大重试次数: {ui_handler.max_retries}")

        # 发送启动信息
        if UI_LOGGER_AVAILABLE:
            success = ui_handler._send_log_with_retry("📝 Bot服务日志适配器已启动", "info")
            print("[UI日志适配器] 启动信息已发送到UI" if success else "[UI日志适配器] 启动信息发送失败")

    except Exception as e:
        print(f"[UI日志适配器] 设置失败: {e}")


# 自动设置：模块被导入时执行
if __name__ != "__main__":
    print("[UI日志适配器] 模块被导入，准备设置UI日志...")

    try:
        setup_ui_logging()
    except Exception as e:
        print(f"[UI日志适配器] 立即设置失败，将延迟执行: {e}")

        def delayed_setup():
            """延迟设置函数，在独立线程中执行"""
            time.sleep(0.5)  # 等待日志系统初始化
            print("[UI日志适配器] 执行延迟设置...")
            setup_ui_logging()

        threading.Thread(target=delayed_setup, daemon=True).start()
