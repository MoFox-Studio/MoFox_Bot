import asyncio
import inspect
import time
from dataclasses import asdict, dataclass
from typing import Any

from src.chat.utils.prompt import Prompt, global_prompt_manager
from src.common.logger import get_logger
from src.config.config import global_config, model_config
from src.llm_models.payload_content import ToolCall
from src.llm_models.utils_model import LLMRequest
from src.plugin_system.apis.tool_api import get_llm_available_tool_definitions, get_tool_instance
from src.plugin_system.base.base_tool import BaseTool
from src.plugin_system.core.global_announcement_manager import global_announcement_manager
from src.plugin_system.core.stream_tool_history import ToolCallRecord, get_stream_tool_history_manager

logger = get_logger("tool_use")


@dataclass
class ToolExecutionConfig:
    """工具执行配置"""
    enable_parallel: bool = True  # 是否启用并行执行
    max_concurrent_tools: int = 5  # 最大并发工具数量
    tool_timeout: float = 60.0  # 单个工具超时时间（秒）
    enable_dependency_check: bool = True  # 是否启用依赖检查


@dataclass
class ToolExecutionResult:
    """工具执行结果"""
    tool_call: ToolCall
    result: dict[str, Any] | None
    error: Exception | None = None
    execution_time: float = 0.0
    is_timeout: bool = False
    original_index: int = 0  # 原始索引，用于保持结果顺序


def init_tool_executor_prompt():
    """初始化工具执行器的提示词"""
    tool_executor_prompt = """
# 工具调用系统

## 📋 你的身份
- **名字**: {bot_name}
- **核心人设**: {personality_core}
- **人格特质**: {personality_side}
- **当前时间**: {time_now}

## 💬 上下文信息

### 对话历史
{chat_history}

### 当前消息
**{sender}** 说: {target_message}

{tool_history}

## 🔧 工具决策指南

**核心原则：**
- 根据上下文智能判断是否需要使用工具
- 每个工具都有详细的description说明其用途和参数
- 避免重复调用历史记录中已执行的工具（除非参数不同）
- 优先考虑使用已有的缓存结果，避免重复调用

**历史记录说明：**
- 上方显示的是**之前**的工具调用记录
- 请参考历史记录避免重复调用相同参数的工具
- 如果历史记录中已有相关结果，可以考虑直接回答而不调用工具

**⚠️ 记忆创建特别提醒：**
创建记忆时，subject（主体）必须使用对话历史中显示的**真实发送人名字**！
- ✅ 正确：从"Prou(12345678): ..."中提取"Prou"作为subject
- ❌ 错误：使用"用户"、"对方"等泛指词

**工具调用策略：**
1. **避免重复调用**：查看历史记录，如果最近已调用过相同工具且参数一致，无需重复调用
2. **智能选择工具**：根据消息内容选择最合适的工具，避免过度使用
3. **参数优化**：确保工具参数简洁有效，避免冗余信息

**执行指令：**
- 需要使用工具 → 直接调用相应的工具函数
- 不需要工具 → 输出 "No tool needed"
"""
    Prompt(tool_executor_prompt, "tool_executor_prompt")


# 初始化提示词
init_tool_executor_prompt()


class ToolExecutor:
    """独立的工具执行器组件

    可以直接输入聊天消息内容，自动判断并执行相应的工具，返回结构化的工具执行结果。
    支持并发执行多个工具，提升执行效率。
    """

    def __init__(self, chat_id: str, execution_config: ToolExecutionConfig | None = None):
        """初始化工具执行器

        Args:
            chat_id: 聊天标识符，用于日志记录
            execution_config: 工具执行配置，如果不提供则使用默认配置
        """
        self.chat_id = chat_id
        self.execution_config = execution_config or ToolExecutionConfig()

        # chat_stream 和 log_prefix 将在异步方法中初始化
        self.chat_stream = None  # type: ignore
        self.log_prefix = f"[{chat_id}]"

        self.llm_model = LLMRequest(model_set=model_config.model_task_config.tool_use, request_type="tool_executor")

        # 二步工具调用状态管理
        self._pending_step_two_tools: dict[str, dict[str, Any]] = {}
        """待处理的第二步工具调用，格式为 {tool_name: step_two_definition}"""
        self._log_prefix_initialized = False

        # 流式工具历史记录管理器
        self.history_manager = get_stream_tool_history_manager(chat_id)

        # logger.info(f"{self.log_prefix}工具执行器初始化完成")  # 移到异步初始化中

    async def _initialize_log_prefix(self):
        """异步初始化log_prefix和chat_stream"""
        if not self._log_prefix_initialized:
            from src.chat.message_receive.chat_stream import get_chat_manager

            self.chat_stream = await get_chat_manager().get_stream(self.chat_id)
            stream_name = await get_chat_manager().get_stream_name(self.chat_id)
            self.log_prefix = f"[{stream_name or self.chat_id}]"
            self._log_prefix_initialized = True
            logger.info(f"{self.log_prefix}工具执行器初始化完成")

    async def execute_from_chat_message(
        self, target_message: str, chat_history: str, sender: str, return_details: bool = False
    ) -> tuple[list[dict[str, Any]], list[str], str]:
        """从聊天消息执行工具

        Args:
            target_message: 目标消息内容
            chat_history: 聊天历史
            sender: 发送者
            return_details: 是否返回详细信息(使用的工具列表和提示词)

        Returns:
            如果return_details为False: Tuple[List[Dict], List[str], str] - (工具执行结果列表, 空, 空)
            如果return_details为True: Tuple[List[Dict], List[str], str] - (结果列表, 使用的工具, 提示词)
        """
        # 初始化log_prefix
        await self._initialize_log_prefix()

        # 获取可用工具
        tools = self._get_tool_definitions()

        # 获取当前时间
        time_now = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

        bot_name = global_config.bot.nickname

        # 构建工具调用历史文本
        tool_history = self.history_manager.format_for_prompt(max_records=5, include_results=True)

        # 获取人设信息
        personality_core = global_config.personality.personality_core
        personality_side = global_config.personality.personality_side

        # 构建工具调用提示词
        prompt = await global_prompt_manager.format_prompt(
            "tool_executor_prompt",
            target_message=target_message,
            chat_history=chat_history,
            sender=sender,
            bot_name=bot_name,
            time_now=time_now,
            tool_history=tool_history,
            personality_core=personality_core,
            personality_side=personality_side,
        )

        logger.debug(f"{self.log_prefix}开始LLM工具调用分析")

        # 调用LLM进行工具决策
        response, llm_extra_info = await self.llm_model.generate_response_async(
            prompt=prompt, tools=tools, raise_when_empty=False
        )

        tool_calls = None
        if llm_extra_info and isinstance(llm_extra_info, tuple) and len(llm_extra_info) == 3:
            _, _, tool_calls = llm_extra_info

        # 执行工具调用
        tool_results, used_tools = await self.execute_tool_calls(tool_calls)

        if used_tools:
            logger.info(f"{self.log_prefix}工具执行完成，共执行{len(used_tools)}个工具: {used_tools}")

        if return_details:
            return tool_results, used_tools, prompt
        else:
            return tool_results, [], ""

    def _get_tool_definitions(self) -> list[dict[str, Any]]:
        all_tools = get_llm_available_tool_definitions()
        user_disabled_tools = global_announcement_manager.get_disabled_chat_tools(self.chat_id)

        # 获取基础工具定义（包括二步工具的第一步）
        tool_definitions = [
            definition for definition in all_tools if definition.get("function", {}).get("name") not in user_disabled_tools
        ]

        # 检查是否有待处理的二步工具第二步调用
        pending_step_two = getattr(self, "_pending_step_two_tools", {})
        if pending_step_two:
            # 添加第二步工具定义
            tool_definitions.extend(list(pending_step_two.values()))

        return tool_definitions


    async def execute_tool_calls(self, tool_calls: list[ToolCall] | None) -> tuple[list[dict[str, Any]], list[str]]:
        """执行工具调用，支持并发执行

        Args:
            tool_calls: LLM返回的工具调用列表

        Returns:
            Tuple[List[Dict], List[str]]: (工具执行结果列表, 使用的工具名称列表)
        """
        tool_results: list[dict[str, Any]] = []
        used_tools = []

        if not tool_calls:
            logger.debug(f"{self.log_prefix}无需执行工具")
            return [], []

        # 提取tool_calls中的函数名称
        func_names = []
        valid_tool_calls = []
        for i, call in enumerate(tool_calls):
            try:
                if hasattr(call, "func_name"):
                    func_names.append(call.func_name)
                    valid_tool_calls.append(call)
            except Exception as e:
                logger.error(f"{self.log_prefix}获取工具名称失败: {e}")
                continue

        if not valid_tool_calls:
            logger.warning(f"{self.log_prefix}未找到有效的工具调用")
            return [], []

        if func_names:
            logger.info(f"{self.log_prefix}开始执行工具调用: {func_names} (模式: {'并发' if self.execution_config.enable_parallel else '串行'})")

        # 选择执行模式
        if self.execution_config.enable_parallel and len(valid_tool_calls) > 1:
            # 并发执行模式
            execution_results = await self._execute_tools_concurrently(valid_tool_calls)
        else:
            # 串行执行模式（保持原有逻辑）
            execution_results = await self._execute_tools_sequentially(valid_tool_calls)

        # 处理执行结果，保持原始顺序
        execution_results.sort(key=lambda x: x.original_index)

        for exec_result in execution_results:
            tool_name = getattr(exec_result.tool_call, "func_name", "unknown_tool")
            tool_args = getattr(exec_result.tool_call, "args", {})

            if exec_result.error:
                # 处理错误结果
                error_msg = f"工具{tool_name}执行失败"
                if exec_result.is_timeout:
                    error_msg += f" (超时: {self.execution_config.tool_timeout}s)"
                error_msg += f": {exec_result.error!s}"

                logger.error(f"{self.log_prefix}{error_msg}")
                error_info = {
                    "type": "tool_error",
                    "id": f"tool_error_{time.time()}",
                    "content": error_msg,
                    "tool_name": tool_name,
                    "timestamp": time.time(),
                }
                tool_results.append(error_info)

                # 记录失败到历史
                await self.history_manager.add_tool_call(ToolCallRecord(
                    tool_name=tool_name,
                    args=tool_args,
                    result=None,
                    status="error" if not exec_result.is_timeout else "timeout",
                    error_message=str(exec_result.error),
                    execution_time=exec_result.execution_time
                ))
            elif exec_result.result:
                # 处理成功结果
                tool_info = {
                    "type": exec_result.result.get("type", "unknown_type"),
                    "id": exec_result.result.get("id", f"tool_exec_{time.time()}"),
                    "content": exec_result.result.get("content", ""),
                    "tool_name": tool_name,
                    "timestamp": time.time(),
                }
                content = tool_info["content"]
                if not isinstance(content, str | list | tuple):
                    tool_info["content"] = str(content)

                tool_results.append(tool_info)
                used_tools.append(tool_name)
                logger.info(f"{self.log_prefix}工具{tool_name}执行成功，类型: {tool_info['type']}, 耗时: {exec_result.execution_time:.2f}s")
                preview = content[:200] if isinstance(content, str) else str(content)[:200]
                logger.debug(f"{self.log_prefix}工具{tool_name}结果内容: {preview}...")

                # 记录到历史
                await self.history_manager.add_tool_call(ToolCallRecord(
                    tool_name=tool_name,
                    args=tool_args,
                    result=exec_result.result,
                    status="success",
                    execution_time=exec_result.execution_time
                ))
            else:
                # 工具返回空结果也记录到历史
                await self.history_manager.add_tool_call(ToolCallRecord(
                    tool_name=tool_name,
                    args=tool_args,
                    result=None,
                    status="success",
                    execution_time=exec_result.execution_time
                ))

        return tool_results, used_tools

    async def _execute_tools_concurrently(self, tool_calls: list[ToolCall]) -> list[ToolExecutionResult]:
        """并发执行多个工具调用

        Args:
            tool_calls: 工具调用列表

        Returns:
            List[ToolExecutionResult]: 执行结果列表
        """
        logger.info(f"{self.log_prefix}启动并发执行，工具数量: {len(tool_calls)}, 最大并发数: {self.execution_config.max_concurrent_tools}")

        # 创建信号量控制并发数量
        semaphore = asyncio.Semaphore(self.execution_config.max_concurrent_tools)

        async def execute_with_semaphore(tool_call: ToolCall, index: int) -> ToolExecutionResult:
            """在信号量控制下执行单个工具"""
            async with semaphore:
                return await self._execute_single_tool_with_timeout(tool_call, index)

        # 创建所有任务
        tasks = [
            execute_with_semaphore(tool_call, i)
            for i, tool_call in enumerate(tool_calls)
        ]

        # 并发执行所有任务
        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # 处理异常结果
            processed_results = []
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error(f"{self.log_prefix}工具执行任务异常: {result}")
                    processed_results.append(ToolExecutionResult(
                        tool_call=tool_calls[i],
                        result=None,
                        error=result,
                        original_index=i
                    ))
                else:
                    processed_results.append(result)

            return processed_results

        except Exception as e:
            logger.error(f"{self.log_prefix}并发执行过程中发生异常: {e}")
            # 返回所有工具的错误结果
            return [
                ToolExecutionResult(
                    tool_call=tool_call,
                    result=None,
                    error=e,
                    original_index=i
                )
                for i, tool_call in enumerate(tool_calls)
            ]

    async def _execute_tools_sequentially(self, tool_calls: list[ToolCall]) -> list[ToolExecutionResult]:
        """串行执行多个工具调用（保持原有逻辑）

        Args:
            tool_calls: 工具调用列表

        Returns:
            List[ToolExecutionResult]: 执行结果列表
        """
        logger.info(f"{self.log_prefix}启动串行执行，工具数量: {len(tool_calls)}")

        results = []
        for i, tool_call in enumerate(tool_calls):
            result = await self._execute_single_tool_with_timeout(tool_call, i)
            results.append(result)

        return results

    async def _execute_single_tool_with_timeout(self, tool_call: ToolCall, index: int) -> ToolExecutionResult:
        """执行单个工具调用，支持超时控制

        Args:
            tool_call: 工具调用
            index: 原始索引

        Returns:
            ToolExecutionResult: 执行结果
        """
        tool_name = getattr(tool_call, "func_name", "unknown_tool")
        start_time = time.time()

        try:
            logger.debug(f"{self.log_prefix}开始执行工具: {tool_name}")

            # 使用 asyncio.wait_for 实现超时控制
            if self.execution_config.tool_timeout > 0:
                result = await asyncio.wait_for(
                    self.execute_tool_call(tool_call),
                    timeout=self.execution_config.tool_timeout
                )
            else:
                result = await self.execute_tool_call(tool_call)

            execution_time = time.time() - start_time
            logger.debug(f"{self.log_prefix}工具 {tool_name} 执行完成，耗时: {execution_time:.2f}s")

            return ToolExecutionResult(
                tool_call=tool_call,
                result=result,
                error=None,
                execution_time=execution_time,
                is_timeout=False,
                original_index=index
            )

        except asyncio.TimeoutError:
            execution_time = time.time() - start_time
            logger.warning(f"{self.log_prefix}工具 {tool_name} 执行超时 ({self.execution_config.tool_timeout}s)")

            return ToolExecutionResult(
                tool_call=tool_call,
                result=None,
                error=asyncio.TimeoutError(f"工具执行超时 ({self.execution_config.tool_timeout}s)"),
                execution_time=execution_time,
                is_timeout=True,
                original_index=index
            )

        except Exception as e:
            execution_time = time.time() - start_time
            logger.error(f"{self.log_prefix}工具 {tool_name} 执行失败: {e}")

            return ToolExecutionResult(
                tool_call=tool_call,
                result=None,
                error=e,
                execution_time=execution_time,
                is_timeout=False,
                original_index=index
            )

    async def execute_tool_call(
        self, tool_call: ToolCall, tool_instance: BaseTool | None = None
    ) -> dict[str, Any] | None:
        """执行单个工具调用，集成流式历史记录管理器"""

        start_time = time.time()
        function_args = tool_call.args or {}
        tool_instance = tool_instance or get_tool_instance(tool_call.func_name, self.chat_stream)

        # 尝试从历史记录管理器获取缓存结果
        if tool_instance and tool_instance.enable_cache:
            try:
                cached_result = await self.history_manager.get_cached_result(
                    tool_name=tool_call.func_name,
                    args=function_args
                )
                if cached_result:
                    execution_time = time.time() - start_time
                    logger.info(f"{self.log_prefix}使用缓存结果，跳过工具 {tool_call.func_name} 执行")

                    # 记录缓存命中到历史
                    await self.history_manager.add_tool_call(ToolCallRecord(
                        tool_name=tool_call.func_name,
                        args=function_args,
                        result=cached_result,
                        status="success",
                        execution_time=execution_time,
                        cache_hit=True
                    ))

                    return cached_result
            except Exception as e:
                logger.error(f"{self.log_prefix}检查历史缓存时出错: {e}")

        # 缓存未命中，执行工具调用
        result = await self._original_execute_tool_call(tool_call, tool_instance)

        # 记录执行结果到历史管理器
        execution_time = time.time() - start_time
        if tool_instance and result and tool_instance.enable_cache:
            try:
                tool_file_path = inspect.getfile(tool_instance.__class__)
                if tool_instance.semantic_cache_query_key:
                    function_args.get(tool_instance.semantic_cache_query_key)

                await self.history_manager.cache_result(
                    tool_name=tool_call.func_name,
                    args=function_args,
                    result=result,
                    execution_time=execution_time,
                    tool_file_path=tool_file_path,
                    ttl=tool_instance.cache_ttl
                )
            except Exception as e:
                logger.error(f"{self.log_prefix}缓存结果到历史管理器时出错: {e}")

        return result

    async def _original_execute_tool_call(
        self, tool_call: ToolCall, tool_instance: BaseTool | None = None
    ) -> dict[str, Any] | None:
        """执行单个工具调用的原始逻辑"""
        try:
            function_name = tool_call.func_name
            function_args = tool_call.args or {}
            logger.info(
                f"{self.log_prefix} 正在执行工具: [bold green]{function_name}[/bold green] | 参数: {function_args}"
            )

            # 检查是否是MCP工具
            from src.plugin_system.core import component_registry

            if component_registry.is_mcp_tool(function_name):
                logger.debug(f"{self.log_prefix}识别到 MCP 工具: {function_name}")
                # 找到对应的 MCP 工具实例
                mcp_tools = component_registry.get_mcp_tools()
                mcp_tool = next((t for t in mcp_tools if t.name == function_name), None)

                if mcp_tool:
                    logger.debug(f"{self.log_prefix}执行 MCP 工具 {function_name}")
                    result = await mcp_tool.execute(function_args)

                    if result:
                        logger.debug(f"{self.log_prefix}MCP 工具 {function_name} 执行成功")
                        return {
                            "tool_call_id": tool_call.call_id,
                            "role": "tool",
                            "name": function_name,
                            "type": "function",
                            "content": result.get("content", ""),
                        }
                else:
                    logger.warning(f"{self.log_prefix}未找到 MCP 工具: {function_name}")
                    return None

            function_args["llm_called"] = True  # 标记为LLM调用

            # 检查是否是二步工具的第二步调用
            if "_" in function_name and function_name.count("_") >= 1:
                # 可能是二步工具的第二步调用，格式为 "tool_name_sub_tool_name"
                parts = function_name.split("_", 1)
                if len(parts) == 2:
                    base_tool_name, sub_tool_name = parts
                    base_tool_instance = get_tool_instance(base_tool_name, self.chat_stream)

                    if base_tool_instance and base_tool_instance.is_two_step_tool:
                        logger.info(f"{self.log_prefix}执行二步工具第二步: {base_tool_name}.{sub_tool_name}")
                        result = await base_tool_instance.execute_step_two(sub_tool_name, function_args)

                        # 清理待处理的第二步工具
                        self._pending_step_two_tools.pop(base_tool_name, None)

                        if result:
                            logger.debug(f"{self.log_prefix}二步工具第二步 {function_name} 执行成功")
                            return {
                                "tool_call_id": tool_call.call_id,
                                "role": "tool",
                                "name": function_name,
                                "type": "function",
                                "content": result.get("content", ""),
                            }

            # 获取对应工具实例
            tool_instance = tool_instance or get_tool_instance(function_name, self.chat_stream)
            if not tool_instance:
                logger.warning(f"未知工具名称: {function_name}")
                return None

            # 执行工具并记录日志
            logger.debug(f"{self.log_prefix}执行工具 {function_name}，参数: {function_args}")
            result = await tool_instance.execute(function_args)

            # 检查是否是二步工具的第一步结果
            if result and result.get("type") == "two_step_tool_step_one":
                logger.info(f"{self.log_prefix}二步工具第一步完成: {function_name}")
                # 保存第二步工具定义
                next_tool_def = result.get("next_tool_definition")
                if next_tool_def:
                    self._pending_step_two_tools[function_name] = next_tool_def
                    logger.debug(f"{self.log_prefix}已保存第二步工具定义: {next_tool_def['name']}")

            if result:
                logger.debug(f"{self.log_prefix}工具 {function_name} 执行成功，结果: {result}")
                return {
                    "tool_call_id": tool_call.call_id,
                    "role": "tool",
                    "name": function_name,
                    "type": "function",
                    "content": result.get("content", ""),
                }
            logger.warning(f"{self.log_prefix}工具 {function_name} 返回空结果")
            return None
        except Exception as e:
            logger.error(f"执行工具调用时发生错误: {e!s}")
            raise e

    async def execute_specific_tool_simple(self, tool_name: str, tool_args: dict) -> dict | None:
        """直接执行指定工具

        Args:
            tool_name: 工具名称
            tool_args: 工具参数
            validate_args: 是否验证参数

        Returns:
            Optional[Dict]: 工具执行结果，失败时返回None
        """
        try:
            tool_call = ToolCall(
                call_id=f"direct_tool_{time.time()}",
                func_name=tool_name,
                args=tool_args,
            )

            logger.info(f"{self.log_prefix}直接执行工具: {tool_name}")

            result = await self.execute_tool_call(tool_call)

            if result:
                tool_info = {
                    "type": result.get("type", "unknown_type"),
                    "id": result.get("id", f"direct_tool_{time.time()}"),
                    "content": result.get("content", ""),
                    "tool_name": tool_name,
                    "timestamp": time.time(),
                }
                logger.info(f"{self.log_prefix}直接工具执行成功: {tool_name}")

                # 记录到历史
                await self.history_manager.add_tool_call(ToolCallRecord(
                    tool_name=tool_name,
                    args=tool_args,
                    result=result,
                    status="success"
                ))

                return tool_info

        except Exception as e:
            logger.error(f"{self.log_prefix}直接工具执行失败 {tool_name}: {e}")
            # 记录失败到历史
            await self.history_manager.add_tool_call(ToolCallRecord(
                tool_name=tool_name,
                args=tool_args,
                result=None,
                status="error",
                error_message=str(e)
            ))

        return None

    def clear_tool_history(self):
        """清除工具调用历史"""
        self.history_manager.clear_history()

    def get_tool_history(self) -> list[dict[str, Any]]:
        """获取工具调用历史

        Returns:
            工具调用历史列表
        """
        # 返回最近的历史记录
        records = self.history_manager.get_recent_history(count=10)
        return [asdict(record) for record in records]

    def get_tool_stats(self) -> dict[str, Any]:
        """获取工具统计信息

        Returns:
            工具统计信息字典
        """
        return self.history_manager.get_stats()

    def set_execution_config(self, config: ToolExecutionConfig) -> None:
        """设置工具执行配置

        Args:
            config: 新的执行配置
        """
        self.execution_config = config
        logger.info(f"{self.log_prefix}工具执行配置已更新: 并发={config.enable_parallel}, 最大并发数={config.max_concurrent_tools}, 超时={config.tool_timeout}s")

    def enable_parallel_execution(self, max_concurrent_tools: int = 5, timeout: float = 60.0) -> None:
        """启用并发执行

        Args:
            max_concurrent_tools: 最大并发工具数量
            timeout: 单个工具超时时间（秒）
        """
        self.execution_config.enable_parallel = True
        self.execution_config.max_concurrent_tools = max_concurrent_tools
        self.execution_config.tool_timeout = timeout
        logger.info(f"{self.log_prefix}已启用并发执行: 最大并发数={max_concurrent_tools}, 超时={timeout}s")

    def disable_parallel_execution(self) -> None:
        """禁用并发执行，使用串行模式"""
        self.execution_config.enable_parallel = False
        logger.info(f"{self.log_prefix}已禁用并发执行，使用串行模式")

    @classmethod
    def create_with_parallel_config(
        cls,
        chat_id: str,
        max_concurrent_tools: int = 5,
        tool_timeout: float = 60.0,
        enable_dependency_check: bool = True
    ) -> "ToolExecutor":
        """创建支持并发执行的工具执行器

        Args:
            chat_id: 聊天标识符
            max_concurrent_tools: 最大并发工具数量
            tool_timeout: 单个工具超时时间（秒）
            enable_dependency_check: 是否启用依赖检查

        Returns:
            配置好并发执行的ToolExecutor实例
        """
        config = ToolExecutionConfig(
            enable_parallel=True,
            max_concurrent_tools=max_concurrent_tools,
            tool_timeout=tool_timeout,
            enable_dependency_check=enable_dependency_check
        )
        return cls(chat_id, config)


"""
ToolExecutor使用示例：

# 1. 基础使用 - 从聊天消息执行工具
executor = ToolExecutor(chat_id=my_chat_id)
results, _, _ = await executor.execute_from_chat_message(
    target_message="今天天气怎么样？现在几点了？",
    chat_history="",
    sender="用户"
)

# 2. 并发执行配置 - 创建支持并发的执行器
parallel_executor = ToolExecutor.create_with_parallel_config(
    chat_id=my_chat_id,
    max_concurrent_tools=3,  # 最大3个工具并发
    tool_timeout=30.0  # 单个工具30秒超时
)

# 或者动态配置并发执行
executor.enable_parallel_execution(max_concurrent_tools=5, timeout=60.0)

# 3. 并发执行多个工具 - 当LLM返回多个工具调用时自动并发执行
results, used_tools, _ = await parallel_executor.execute_from_chat_message(
    target_message="帮我查询天气、新闻和股票价格",
    chat_history="",
    sender="用户"
)
# 多个工具将并发执行，显著提升性能

# 4. 获取详细信息
results, used_tools, prompt = await executor.execute_from_chat_message(
    target_message="帮我查询Python相关知识",
    chat_history="",
    sender="用户",
    return_details=True
)

# 5. 直接执行特定工具
result = await executor.execute_specific_tool_simple(
    tool_name="get_knowledge",
    tool_args={"query": "机器学习"}
)

# 6. 使用工具历史 - 连续对话中的工具调用
# 第一次调用
await executor.execute_from_chat_message(
    target_message="查询今天的天气",
    chat_history="",
    sender="用户"
)
# 第二次调用时会自动包含上次的工具调用历史
await executor.execute_from_chat_message(
    target_message="那明天呢？",
    chat_history="",
    sender="用户"
)

# 7. 配置管理
config = ToolExecutionConfig(
    enable_parallel=True,
    max_concurrent_tools=10,
    tool_timeout=120.0,
    enable_dependency_check=True
)
executor.set_execution_config(config)

# 8. 获取和清除历史
history = executor.get_tool_history()  # 获取历史记录
stats = executor.get_tool_stats()  # 获取执行统计信息
executor.clear_tool_history()  # 清除历史记录

# 9. 禁用并发执行（如需要串行执行）
executor.disable_parallel_execution()

并发执行优势：
- 🚀 性能提升：多个工具同时执行，减少总体等待时间
- 🛡️ 错误隔离：单个工具失败不影响其他工具执行
- ⏱️ 超时控制：防止单个工具无限等待
- 🔧 灵活配置：可根据需要调整并发数量和超时时间
- 📊 统计信息：提供详细的执行时间和性能数据
"""
