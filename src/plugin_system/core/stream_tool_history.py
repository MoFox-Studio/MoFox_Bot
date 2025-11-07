"""
流式工具历史记录管理器
用于在聊天流级别管理工具调用历史，支持智能缓存和上下文感知
"""

import time
from dataclasses import dataclass, field
from typing import Any

import orjson

from src.common.cache_manager import tool_cache
from src.common.logger import get_logger

logger = get_logger("stream_tool_history")


@dataclass
class ToolCallRecord:
    """工具调用记录"""
    tool_name: str
    args: dict[str, Any]
    result: dict[str, Any] | None = None
    status: str = "success"  # success, error, pending
    timestamp: float = field(default_factory=time.time)
    execution_time: float | None = None  # 执行耗时(秒)
    cache_hit: bool = False  # 是否命中缓存
    result_preview: str = ""  # 结果预览
    error_message: str = ""  # 错误信息

    def __post_init__(self):
        """后处理：生成结果预览"""
        if self.result and not self.result_preview:
            content = self.result.get("content", "")
            if isinstance(content, str):
                self.result_preview = content[:500] + ("..." if len(content) > 500 else "")
            elif isinstance(content, list | dict):
                try:
                    self.result_preview = orjson.dumps(content, option=orjson.OPT_NON_STR_KEYS).decode("utf-8")[:500] + "..."
                except Exception:
                    self.result_preview = str(content)[:500] + "..."
            else:
                self.result_preview = str(content)[:500] + "..."


class StreamToolHistoryManager:
    """流式工具历史记录管理器

    提供以下功能：
    1. 工具调用历史的持久化管理
    2. 智能缓存集成和结果去重
    3. 上下文感知的历史记录检索
    4. 性能监控和统计
    """

    def __init__(self, chat_id: str, max_history: int = 20, enable_memory_cache: bool = True):
        """初始化历史记录管理器

        Args:
            chat_id: 聊天ID，用于隔离不同聊天流的历史记录
            max_history: 最大历史记录数量
            enable_memory_cache: 是否启用内存缓存
        """
        self.chat_id = chat_id
        self.max_history = max_history
        self.enable_memory_cache = enable_memory_cache

        # 内存中的历史记录，按时间顺序排列
        self._history: list[ToolCallRecord] = []

        # 性能统计
        self._stats = {
            "total_calls": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "total_execution_time": 0.0,
            "average_execution_time": 0.0,
        }

        logger.info(f"[{chat_id}] 工具历史记录管理器初始化完成，最大历史: {max_history}")

    async def add_tool_call(self, record: ToolCallRecord) -> None:
        """添加工具调用记录

        Args:
            record: 工具调用记录
        """
        # 维护历史记录大小
        if len(self._history) >= self.max_history:
            # 移除最旧的记录
            removed_record = self._history.pop(0)
            logger.debug(f"[{self.chat_id}] 移除旧记录: {removed_record.tool_name}")

        # 添加新记录
        self._history.append(record)

        # 更新统计
        self._stats["total_calls"] += 1
        if record.cache_hit:
            self._stats["cache_hits"] += 1
        else:
            self._stats["cache_misses"] += 1

        if record.execution_time is not None:
            self._stats["total_execution_time"] += record.execution_time
            self._stats["average_execution_time"] = self._stats["total_execution_time"] / self._stats["total_calls"]

        logger.debug(f"[{self.chat_id}] 添加工具调用记录: {record.tool_name}, 缓存命中: {record.cache_hit}")

    async def get_cached_result(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any] | None:
        """从缓存或历史记录中获取结果

        Args:
            tool_name: 工具名称
            args: 工具参数

        Returns:
            缓存的结果，如果不存在则返回None
        """
        # 首先检查内存中的历史记录
        if self.enable_memory_cache:
            memory_result = self._search_memory_cache(tool_name, args)
            if memory_result:
                logger.info(f"[{self.chat_id}] 内存缓存命中: {tool_name}")
                return memory_result

        # 然后检查全局缓存系统
        try:
            # 这里需要工具实例来获取文件路径，但为了解耦，我们先尝试从历史记录中推断
            tool_file_path = self._infer_tool_path(tool_name)

            # 尝试语义缓存（如果可以推断出语义查询参数）
            semantic_query = self._extract_semantic_query(tool_name, args)

            cached_result = await tool_cache.get(
                tool_name=tool_name,
                function_args=args,
                tool_file_path=tool_file_path,
                semantic_query=semantic_query,
            )

            if cached_result:
                logger.info(f"[{self.chat_id}] 全局缓存命中: {tool_name}")

                # 将结果同步到内存缓存
                if self.enable_memory_cache:
                    record = ToolCallRecord(
                        tool_name=tool_name,
                        args=args,
                        result=cached_result,
                        status="success",
                        cache_hit=True,
                        timestamp=time.time(),
                    )
                    await self.add_tool_call(record)

                return cached_result

        except Exception as e:
            logger.warning(f"[{self.chat_id}] 缓存查询失败: {e}")

        return None

    async def cache_result(self, tool_name: str, args: dict[str, Any], result: dict[str, Any],
                          execution_time: float | None = None,
                          tool_file_path: str | None = None,
                          ttl: int | None = None) -> None:
        """缓存工具调用结果

        Args:
            tool_name: 工具名称
            args: 工具参数
            result: 执行结果
            execution_time: 执行耗时
            tool_file_path: 工具文件路径
            ttl: 缓存TTL
        """
        # 添加到内存历史记录
        record = ToolCallRecord(
            tool_name=tool_name,
            args=args,
            result=result,
            status="success",
            execution_time=execution_time,
            cache_hit=False,
            timestamp=time.time(),
        )
        await self.add_tool_call(record)

        # 同步到全局缓存系统
        try:
            if tool_file_path is None:
                tool_file_path = self._infer_tool_path(tool_name)

            # 尝试语义缓存
            semantic_query = self._extract_semantic_query(tool_name, args)

            await tool_cache.set(
                tool_name=tool_name,
                function_args=args,
                tool_file_path=tool_file_path,
                data=result,
                ttl=ttl,
                semantic_query=semantic_query,
            )

            logger.debug(f"[{self.chat_id}] 结果已缓存: {tool_name}")

        except Exception as e:
            logger.warning(f"[{self.chat_id}] 缓存设置失败: {e}")

    async def get_recent_history(self, count: int = 5, status_filter: str | None = None) -> list[ToolCallRecord]:
        """获取最近的历史记录

        Args:
            count: 返回的记录数量
            status_filter: 状态过滤器，可选值：success, error, pending

        Returns:
            历史记录列表
        """
        history = self._history.copy()

        # 应用状态过滤
        if status_filter:
            history = [record for record in history if record.status == status_filter]

        # 返回最近的记录
        return history[-count:] if history else []

    def format_for_prompt(self, max_records: int = 5, include_results: bool = True) -> str:
        """格式化历史记录为提示词

        Args:
            max_records: 最大记录数量
            include_results: 是否包含结果预览

        Returns:
            格式化的提示词字符串
        """
        if not self._history:
            return ""

        recent_records = self._history[-max_records:]

        lines = ["## 🔧 最近工具调用记录"]
        for i, record in enumerate(recent_records, 1):
            status_icon = "✅" if record.status == "success" else "❌" if record.status == "error" else "⏳"

            # 格式化参数
            args_preview = self._format_args_preview(record.args)

            # 基础信息
            lines.append(f"{i}. {status_icon} **{record.tool_name}**({args_preview})")

            # 添加执行时间和缓存信息
            if record.execution_time is not None:
                time_info = f"{record.execution_time:.2f}s"
                cache_info = "🎯缓存" if record.cache_hit else "🔍执行"
                lines.append(f"   ⏱️ {time_info} | {cache_info}")

            # 添加结果预览
            if include_results and record.result_preview:
                lines.append(f"   📝 结果: {record.result_preview}")

            # 添加错误信息
            if record.status == "error" and record.error_message:
                lines.append(f"   ❌ 错误: {record.error_message}")

        # 添加统计信息
        if self._stats["total_calls"] > 0:
            cache_hit_rate = (self._stats["cache_hits"] / self._stats["total_calls"]) * 100
            avg_time = self._stats["average_execution_time"]
            lines.append(f"\n📊 工具统计: 总计{self._stats['total_calls']}次 | 缓存命中率{cache_hit_rate:.1f}% | 平均耗时{avg_time:.2f}s")

        return "\n".join(lines)

    def get_stats(self) -> dict[str, Any]:
        """获取性能统计信息

        Returns:
            统计信息字典
        """
        cache_hit_rate = 0.0
        if self._stats["total_calls"] > 0:
            cache_hit_rate = (self._stats["cache_hits"] / self._stats["total_calls"]) * 100

        return {
            **self._stats,
            "cache_hit_rate": cache_hit_rate,
            "history_size": len(self._history),
            "chat_id": self.chat_id,
        }

    def clear_history(self) -> None:
        """清除历史记录"""
        self._history.clear()
        logger.info(f"[{self.chat_id}] 工具历史记录已清除")

    def _search_memory_cache(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any] | None:
        """在内存历史记录中搜索缓存

        Args:
            tool_name: 工具名称
            args: 工具参数

        Returns:
            匹配的结果，如果不存在则返回None
        """
        for record in reversed(self._history):  # 从最新的开始搜索
            if (record.tool_name == tool_name and
                record.status == "success" and
                record.args == args):
                return record.result
        return None

    def _infer_tool_path(self, tool_name: str) -> str:
        """推断工具文件路径

        Args:
            tool_name: 工具名称

        Returns:
            推断的文件路径
        """
        # 基于工具名称推断路径，这是一个简化的实现
        # 在实际使用中，可能需要更复杂的映射逻辑
        tool_path_mapping = {
            "web_search": "src/plugins/built_in/web_search_tool/tools/web_search.py",
            "memory_create": "src/memory_graph/tools/memory_tools.py",
            "memory_search": "src/memory_graph/tools/memory_tools.py",
            "user_profile_update": "src/plugins/built_in/affinity_flow_chatter/tools/user_profile_tool.py",
            "chat_stream_impression_update": "src/plugins/built_in/affinity_flow_chatter/tools/chat_stream_impression_tool.py",
        }

        return tool_path_mapping.get(tool_name, f"src/plugins/tools/{tool_name}.py")

    def _extract_semantic_query(self, tool_name: str, args: dict[str, Any]) -> str | None:
        """提取语义查询参数

        Args:
            tool_name: 工具名称
            args: 工具参数

        Returns:
            语义查询字符串，如果不存在则返回None
        """
        # 为不同工具定义语义查询参数映射
        semantic_query_mapping = {
            "web_search": "query",
            "memory_search": "query",
            "knowledge_search": "query",
        }

        query_key = semantic_query_mapping.get(tool_name)
        if query_key and query_key in args:
            return str(args[query_key])

        return None

    def _format_args_preview(self, args: dict[str, Any], max_length: int = 100) -> str:
        """格式化参数预览

        Args:
            args: 参数字典
            max_length: 最大长度

        Returns:
            格式化的参数预览字符串
        """
        if not args:
            return ""

        try:
            args_str = orjson.dumps(args, option=orjson.OPT_SORT_KEYS).decode("utf-8")
            if len(args_str) > max_length:
                args_str = args_str[:max_length] + "..."
            return args_str
        except Exception:
            # 如果序列化失败，使用简单格式
            parts = []
            for k, v in list(args.items())[:3]:  # 最多显示3个参数
                parts.append(f"{k}={str(v)[:20]}")
            result = ", ".join(parts)
            if len(parts) >= 3 or len(result) > max_length:
                result += "..."
            return result


# 全局管理器字典，按chat_id索引
_stream_managers: dict[str, StreamToolHistoryManager] = {}


def get_stream_tool_history_manager(chat_id: str) -> StreamToolHistoryManager:
    """获取指定聊天的工具历史记录管理器

    Args:
        chat_id: 聊天ID

    Returns:
        工具历史记录管理器实例
    """
    if chat_id not in _stream_managers:
        _stream_managers[chat_id] = StreamToolHistoryManager(chat_id)
    return _stream_managers[chat_id]


def cleanup_stream_manager(chat_id: str) -> None:
    """清理指定聊天的管理器

    Args:
        chat_id: 聊天ID
    """
    if chat_id in _stream_managers:
        del _stream_managers[chat_id]
        logger.info(f"已清理聊天 {chat_id} 的工具历史记录管理器")
