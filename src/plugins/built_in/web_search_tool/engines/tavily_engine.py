"""
Tavily search engine implementation
"""

import asyncio
import functools
from typing import Any

from tavily import TavilyClient

from src.common.logger import get_logger
from src.plugin_system.apis import config_api

from ..utils.api_key_manager import create_api_key_manager_from_config
from .base import BaseSearchEngine

logger = get_logger("tavily_engine")


class TavilySearchEngine(BaseSearchEngine):
    """
    Tavily搜索引擎实现
    """

    def __init__(self):
        self._initialize_clients()

    def _initialize_clients(self):
        """初始化Tavily客户端"""
        # 从主配置文件读取API密钥
        tavily_api_keys = config_api.get_global_config("web_search.tavily_api_keys", None)

        # 创建API密钥管理器
        self.api_manager = create_api_key_manager_from_config(
            tavily_api_keys, lambda key: TavilyClient(api_key=key), "Tavily"
        )

    def is_available(self) -> bool:
        """检查Tavily搜索引擎是否可用"""
        return self.api_manager.is_available()

    async def search(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        """执行Tavily搜索"""
        if not self.is_available():

            return []

        query = args["query"]
        num_results = args.get("num_results", 3)
        time_range = args.get("time_range", "any")

        try:
            # 使用API密钥管理器获取下一个客户端
            tavily_client = self.api_manager.get_next_client()
            if not tavily_client:
                logger.error("无法获取Tavily客户端")
                return []

            # 构建Tavily搜索参数
            search_params = {
                "query": query,
                "max_results": num_results,
                "search_depth": "basic",
                "include_answer": False,
                "include_raw_content": False,
            }

            # 根据时间范围调整搜索参数
            if time_range == "week":
                search_params["days"] = 7
            elif time_range == "month":
                search_params["days"] = 30

            loop = asyncio.get_running_loop()
            func = functools.partial(tavily_client.search, **search_params)
            search_response = await loop.run_in_executor(None, func)

            results = []
            if search_response and "results" in search_response:
                results.extend(
                    {
                        "title": res.get("title", "无标题"),
                        "url": res.get("url", ""),
                        "snippet": res.get("content", "")[:300] + "..." if res.get("content") else "无摘要",
                        "provider": "Tavily",
                    }
                    for res in search_response["results"]
                )

            return results

        except Exception as e:
            logger.error(f"Tavily 搜索失败: {e}")
            return []
