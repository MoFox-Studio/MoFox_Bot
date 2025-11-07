"""
Serper search engine implementation
Google Search via Serper.dev API
"""

import aiohttp
from typing import Any

from src.common.logger import get_logger
from src.plugin_system.apis import config_api

from ..utils.api_key_manager import create_api_key_manager_from_config
from .base import BaseSearchEngine

logger = get_logger("serper_engine")


class SerperSearchEngine(BaseSearchEngine):
    """
    Serper搜索引擎实现 (Google Search via Serper.dev)
    免费额度：每月2500次查询
    """

    def __init__(self):
        self.base_url = "https://google.serper.dev"
        self._initialize_api_manager()

    def _initialize_api_manager(self):
        """初始化API密钥管理器"""
        # 从主配置文件读取API密钥
        serper_api_keys = config_api.get_global_config("web_search.serper_api_keys", None)

        # 创建API密钥管理器（不需要创建客户端，只管理key）
        self.api_manager = create_api_key_manager_from_config(
            serper_api_keys,
            lambda key: key,  # 直接返回key，不创建客户端
            "Serper"
        )

    def is_available(self) -> bool:
        """检查Serper搜索引擎是否可用"""
        return self.api_manager.is_available()

    async def search(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        """执行Serper搜索

        Args:
            args: 搜索参数，包含:
                - query: 搜索查询
                - num_results: 返回结果数量
                - time_range: 时间范围（暂不支持）

        Returns:
            搜索结果列表，每个结果包含 title、url、snippet、provider 字段
        """
        if not self.is_available():
            logger.warning("Serper API密钥未配置")
            return []

        query = args["query"]
        num_results = args.get("num_results", 10)

        # 获取下一个API key
        api_key = self.api_manager.get_next_client()
        if not api_key:
            logger.error("无法获取Serper API密钥")
            return []

        # 构建请求
        url = f"{self.base_url}/search"
        headers = {
            "X-API-KEY": api_key,
            "Content-Type": "application/json"
        }
        payload = {
            "q": query,
            "num": min(num_results, 20),  # 限制最大20个结果
        }

        try:
            # 执行搜索请求
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"Serper API错误: {response.status} - {error_text}")
                        return []

                    data = await response.json()

            # 处理搜索结果
            results = []

            # 添加答案框（如果有）
            if "answerBox" in data:
                answer = data["answerBox"]
                if "answer" in answer or "snippet" in answer:
                    results.append({
                        "title": "直接答案",
                        "url": answer.get("link", ""),
                        "snippet": answer.get("answer") or answer.get("snippet", ""),
                        "provider": "Serper (Answer Box)",
                    })

            # 添加知识图谱（如果有）
            if "knowledgeGraph" in data:
                kg = data["knowledgeGraph"]
                if "description" in kg:
                    results.append({
                        "title": kg.get("title", "知识图谱"),
                        "url": kg.get("website", ""),
                        "snippet": kg.get("description", ""),
                        "provider": "Serper (Knowledge Graph)",
                    })

            # 添加有机搜索结果
            if "organic" in data:
                for result in data["organic"][:num_results]:
                    results.append({
                        "title": result.get("title", "无标题"),
                        "url": result.get("link", ""),
                        "snippet": result.get("snippet", ""),
                        "provider": "Serper",
                    })

            logger.info(f"Serper搜索成功: 查询='{query}', 结果数={len(results)}")
            return results

        except aiohttp.ClientError as e:
            logger.error(f"Serper 网络请求失败: {e}")
            return []
        except Exception as e:
            logger.error(f"Serper 搜索失败: {e}")
            return []
