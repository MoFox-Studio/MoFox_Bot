"""
Web search tool implementation
"""

import asyncio
from typing import Any, ClassVar

from src.common.cache_manager import tool_cache
from src.common.logger import get_logger
from src.plugin_system import BaseTool, ToolParamType
from src.plugin_system.apis import config_api

from ..engines.bing_engine import BingSearchEngine
from ..engines.ddg_engine import DDGSearchEngine
from ..engines.exa_engine import ExaSearchEngine
from ..engines.metaso_engine import MetasoSearchEngine
from ..engines.searxng_engine import SearXNGSearchEngine
from ..engines.tavily_engine import TavilySearchEngine
from ..utils.formatters import deduplicate_results, format_search_results

logger = get_logger("web_search_tool")


class WebSurfingTool(BaseTool):
    """
    网络搜索工具
    """

    name: str = "web_search"
    description: str = (
        "用于执行网络搜索。当用户明确要求搜索，或者需要获取关于公司、产品、事件的最新信息、新闻或动态时，必须使用此工具"
    )
    available_for_llm: bool = True
    parameters: ClassVar[list] = [
        ("query", ToolParamType.STRING, "要搜索的关键词或问题。", True, None),
        ("num_results", ToolParamType.INTEGER, "期望每个搜索引擎返回的搜索结果数量，默认为5。", False, None),
        (
            "time_range",
            ToolParamType.STRING,
            "指定搜索的时间范围，可以是 'any', 'week', 'month'。默认为 'any'。",
            False,
            ["any", "week", "month"],
        ),
        (
            "answer_mode",
            ToolParamType.BOOLEAN,
            "是否启用答案模式（仅适用于Exa搜索引擎）。启用后将返回更精简、直接的答案，减少冗余信息。默认为False。",
            False,
            None,
        ),
    ]  # type: ignore

    def __init__(self, plugin_config=None, chat_stream=None):
        super().__init__(plugin_config, chat_stream)
        # 初始化搜索引擎
        self.engines = {
            "exa": ExaSearchEngine(),
            "tavily": TavilySearchEngine(),
            "ddg": DDGSearchEngine(),
            "bing": BingSearchEngine(),
            "searxng": SearXNGSearchEngine(),
            "metaso": MetasoSearchEngine(),
        }

    async def execute(self, function_args: dict[str, Any]) -> dict[str, Any]:
        query = function_args.get("query")
        if not query:

            return {"error": "搜索查询不能为空。"}

        # 获取当前文件路径用于缓存键
        import os

        current_file_path = os.path.abspath(__file__)

        # 检查缓存
        cached_result = await tool_cache.get(self.name, function_args, current_file_path, semantic_query=query)
        if cached_result:
            logger.info(f"缓存命中: {self.name} -> {function_args}")
            return cached_result

        # 读取搜索配置
        enabled_engines = config_api.get_global_config("web_search.enabled_engines", ["ddg"])
        search_strategy = config_api.get_global_config("web_search.search_strategy", "single")

        logger.info(f"开始搜索，策略: {search_strategy}, 启用引擎: {enabled_engines}, 参数: '{function_args}'")

        # 根据策略执行搜索
        if search_strategy == "parallel":
            result = await self._execute_parallel_search(function_args, enabled_engines)
        elif search_strategy == "fallback":
            result = await self._execute_fallback_search(function_args, enabled_engines)
        else:  # single
            result = await self._execute_single_search(function_args, enabled_engines)

        # 保存到缓存
        if "error" not in result:
            await tool_cache.set(self.name, function_args, current_file_path, result, semantic_query=query)

        return result

    async def _execute_parallel_search(
        self, function_args: dict[str, Any], enabled_engines: list[str]
    ) -> dict[str, Any]:
        """并行搜索策略：同时使用所有启用的搜索引擎"""
        search_tasks = []
        answer_mode = function_args.get("answer_mode", False)

        for engine_name in enabled_engines:
            engine = self.engines.get(engine_name)
            if engine and engine.is_available():
                custom_args = function_args.copy()
                custom_args["num_results"] = custom_args.get("num_results", 5)

                # 如果启用了answer模式且是Exa引擎，使用answer_search方法
                if answer_mode and engine_name == "exa" and hasattr(engine, "answer_search"):
                    search_tasks.append(engine.answer_search(custom_args))
                else:
                    search_tasks.append(engine.search(custom_args))

        if not search_tasks:


            return {"error": "没有可用的搜索引擎。"}

        try:
            search_results_lists = await asyncio.gather(*search_tasks, return_exceptions=True)

            all_results = []
            for result in search_results_lists:
                if isinstance(result, list):
                    all_results.extend(result)
                elif isinstance(result, Exception):
                    logger.error(f"搜索时发生错误: {result}")

            # 去重并格式化
            unique_results = deduplicate_results(all_results)
            formatted_content = format_search_results(unique_results)

            return {
                "type": "web_search_result",
                "content": formatted_content,
            }

        except Exception as e:
            logger.error(f"执行并行网络搜索时发生异常: {e}", exc_info=True)
            return {"error": f"执行网络搜索时发生严重错误: {e!s}"}

    async def _execute_fallback_search(
        self, function_args: dict[str, Any], enabled_engines: list[str]
    ) -> dict[str, Any]:
        """回退搜索策略：按顺序尝试搜索引擎，失败则尝试下一个"""
        answer_mode = function_args.get("answer_mode", False)

        for engine_name in enabled_engines:
            engine = self.engines.get(engine_name)
            if not engine or not engine.is_available():
                continue

            try:
                custom_args = function_args.copy()
                custom_args["num_results"] = custom_args.get("num_results", 5)

                # 如果启用了answer模式且是Exa引擎，使用answer_search方法
                if answer_mode and engine_name == "exa" and hasattr(engine, "answer_search"):
                    logger.info("使用Exa答案模式进行搜索（fallback策略）")
                    results = await engine.answer_search(custom_args)
                else:
                    results = await engine.search(custom_args)

                if results:  # 如果有结果，直接返回
                    formatted_content = format_search_results(results)
                    return {
                        "type": "web_search_result",
                        "content": formatted_content,
                    }

            except Exception as e:
                logger.warning(f"{engine_name} 搜索失败，尝试下一个引擎: {e}")
                continue

        return {"error": "所有搜索引擎都失败了。"}

    async def _execute_single_search(self, function_args: dict[str, Any], enabled_engines: list[str]) -> dict[str, Any]:
        """单一搜索策略：只使用第一个可用的搜索引擎"""
        answer_mode = function_args.get("answer_mode", False)

        for engine_name in enabled_engines:
            engine = self.engines.get(engine_name)
            if not engine or not engine.is_available():
                continue

            try:
                custom_args = function_args.copy()
                custom_args["num_results"] = custom_args.get("num_results", 5)

                # 如果启用了answer模式且是Exa引擎，使用answer_search方法
                if answer_mode and engine_name == "exa" and hasattr(engine, "answer_search"):
                    logger.info("使用Exa答案模式进行搜索")
                    results = await engine.answer_search(custom_args)
                else:
                    results = await engine.search(custom_args)

                if results:
                    formatted_content = format_search_results(results)
                    return {
                        "type": "web_search_result",
                        "content": formatted_content,
                    }

            except Exception as e:
                logger.error(f"{engine_name} 搜索失败: {e}")
                return {"error": f"{engine_name} 搜索失败: {e!s}"}

        return {"error": "没有可用的搜索引擎。"}
