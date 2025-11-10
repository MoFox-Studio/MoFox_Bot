"""
Exa search engine implementation
"""

import asyncio
import functools
from datetime import datetime, timedelta
from typing import Any

from exa_py import Exa

from src.common.logger import get_logger
from src.plugin_system.apis import config_api

from ..utils.api_key_manager import create_api_key_manager_from_config
from .base import BaseSearchEngine

logger = get_logger("exa_engine")


class ExaSearchEngine(BaseSearchEngine):
    """
    Exa搜索引擎实现
    """

    def __init__(self):
        self._initialize_clients()

    def _initialize_clients(self):
        """初始化Exa客户端"""
        # 从主配置文件读取API密钥
        exa_api_keys = config_api.get_global_config("web_search.exa_api_keys", None)

        # 创建API密钥管理器
        self.api_manager = create_api_key_manager_from_config(exa_api_keys, lambda key: Exa(api_key=key), "Exa")

    def is_available(self) -> bool:
        """检查Exa搜索引擎是否可用"""
        return self.api_manager.is_available()

    async def search(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        """执行优化的Exa搜索（使用新的search API）"""
        if not self.is_available():
            return []

        query = args["query"]
        num_results = min(args.get("num_results", 5), 5)  # 默认5个结果，但限制最多5个
        time_range = args.get("time_range", "any")

        # 使用新的搜索参数格式
        exa_args = {
            "num_results": num_results,
            "contents": {
                "text": True,
                "summary": True,  # 启用自动摘要
            },
        }

        # 时间范围过滤
        if time_range != "any":
            today = datetime.now()
            start_date = today - timedelta(days=7 if time_range == "week" else 30)
            exa_args["start_published_date"] = start_date.strftime("%Y-%m-%d")

        try:
            # 使用API密钥管理器获取下一个客户端
            exa_client = self.api_manager.get_next_client()
            if not exa_client:
                logger.error("无法获取Exa客户端")
                return []

            loop = asyncio.get_running_loop()
            # 使用新的search方法
            func = functools.partial(exa_client.search, query, **exa_args)
            search_response = await loop.run_in_executor(None, func)

            # 优化结果处理 - 更注重答案质量
            results = []
            for res in search_response.results:
                # 获取最佳内容片段
                summary = getattr(res, "summary", "")
                text = getattr(res, "text", "")

                # 智能内容选择：摘要 > 文本开头
                if summary and len(summary) > 50:
                    snippet = summary.strip()
                elif text:
                    snippet = text[:300] + "..." if len(text) > 300 else text
                else:
                    snippet = "内容获取失败"

                # 只保留有意义的摘要
                if len(snippet) < 30:
                    snippet = text[:200] + "..." if text and len(text) > 200 else snippet

                results.append({
                    "title": res.title,
                    "url": res.url,
                    "snippet": snippet,
                    "provider": "Exa",
                    "answer_focused": True,  # 标记为答案导向的搜索
                })

            return results
        except Exception as e:
            logger.error(f"Exa搜索失败: {e}")
            return []

    async def answer_search(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        """执行Exa快速答案搜索 - 最精简的搜索模式"""
        if not self.is_available():
            return []

        query = args["query"]
        num_results = min(args.get("num_results", 3), 3)  # answer模式默认3个结果，专注质量

        # 精简的搜索参数 - 专注快速答案
        exa_args = {
            "num_results": num_results,
            "contents": {
                "text": False,  # 不需要全文
                "summary": True,  # 优先摘要
            },
        }

        try:
            exa_client = self.api_manager.get_next_client()
            if not exa_client:
                return []

            loop = asyncio.get_running_loop()
            func = functools.partial(exa_client.search, query, **exa_args)
            search_response = await loop.run_in_executor(None, func)

            # 极简结果处理 - 只保留最核心信息
            results = []
            for res in search_response.results:
                summary = getattr(res, "summary", "")

                # 使用摘要作为答案
                answer_text = summary.strip() if summary else ""

                if answer_text and len(answer_text) > 20:
                    results.append({
                        "title": res.title,
                        "url": res.url,
                        "snippet": answer_text[:400] + "..." if len(answer_text) > 400 else answer_text,
                        "provider": "Exa-Answer",
                        "answer_mode": True  # 标记为纯答案模式
                    })

            return results
        except Exception as e:
            logger.error(f"Exa快速答案搜索失败: {e}")
            return []
