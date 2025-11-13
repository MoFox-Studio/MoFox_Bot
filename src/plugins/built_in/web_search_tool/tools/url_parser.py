"""
URL parser tool implementation
"""

import asyncio
import functools
from typing import Any, ClassVar

import httpx
from bs4 import BeautifulSoup
from exa_py import Exa

from src.common.cache_manager import tool_cache
from src.common.logger import get_logger
from src.plugin_system import BaseTool, ToolParamType, llm_api
from src.plugin_system.apis import config_api

from ..utils.api_key_manager import create_api_key_manager_from_config
from ..utils.formatters import format_url_parse_results
from ..utils.url_utils import parse_urls_from_input, validate_urls

logger = get_logger("url_parser_tool")


class URLParserTool(BaseTool):
    """
    一个用于解析和总结一个或多个网页URL内容的工具。
    """

    name: str = "parse_url"
    description: str = "当需要理解一个或多个特定网页链接的内容时，使用此工具。例如：'这些网页讲了什么？[https://example.com, https://example2.com]' 或 '帮我总结一下这些文章'"
    available_for_llm: bool = True
    parameters: ClassVar[list] = [
        ("urls", ToolParamType.STRING, "要理解的网站", True, None),
    ]

    def __init__(self, plugin_config=None, chat_stream=None):
        super().__init__(plugin_config, chat_stream)
        self._initialize_exa_clients()

    def _initialize_exa_clients(self):
        """初始化Exa客户端"""
        # 优先从主配置文件读取，如果没有则从插件配置文件读取
        exa_api_keys = config_api.get_global_config("exa.api_keys", None)
        if exa_api_keys is None:
            # 从插件配置文件读取
            exa_api_keys = self.get_config("exa.api_keys", [])

        # 创建API密钥管理器
        self.api_manager = create_api_key_manager_from_config(
            exa_api_keys, lambda key: Exa(api_key=key), "Exa URL Parser"
        )

    async def _local_parse_and_summarize(self, url: str) -> dict[str, Any]:
        """
        使用本地库(httpx, BeautifulSoup)解析URL，并调用LLM进行总结。
        """
        try:
            # 读取代理配置
            enable_proxy = self.get_config("proxy.enable_proxy", False)
            proxies = None

            if enable_proxy:
                socks5_proxy = self.get_config("proxy.socks5_proxy", None)
                http_proxy = self.get_config("proxy.http_proxy", None)
                https_proxy = self.get_config("proxy.https_proxy", None)

                # 优先使用SOCKS5代理（全协议代理）
                if socks5_proxy:
                    proxies = socks5_proxy
                    logger.info(f"使用SOCKS5代理: {socks5_proxy}")
                elif http_proxy or https_proxy:
                    proxies = {}
                    if http_proxy:
                        proxies["http://"] = http_proxy
                    if https_proxy:
                        proxies["https://"] = https_proxy
                    logger.info(f"使用HTTP/HTTPS代理配置: {proxies}")

            client_kwargs = {"timeout": 15.0, "follow_redirects": True}
            if proxies:
                client_kwargs["proxies"] = proxies

            async with httpx.AsyncClient(**client_kwargs) as client:
                response = await client.get(url)
                response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")

            title = soup.title.string if soup.title else "无标题"
            for script in soup(["script", "style"]):
                script.extract()
            text = soup.get_text(strip=True)

            if not text:


                return {"error": "无法从页面提取有效文本内容。"}

            summary_prompt = f"请根据以下网页内容，生成一段不超过300字的中文摘要，保留核心信息和关键点:\n\n---\n\n标题: {title}\n\n内容:\n{text[:4000]}\n\n---\n\n摘要:"

            text_model = str(self.get_config("models.text_model", "replyer_1"))
            models = llm_api.get_available_models()
            model_config = models.get(text_model)
            if not model_config:
                logger.error("未配置LLM模型")
                return {"error": "未配置LLM模型"}

            success, summary, reasoning, model_name = await llm_api.generate_with_model(
                prompt=summary_prompt,
                model_config=model_config,
                request_type="story.generate",
                temperature=0.3,
                max_tokens=1000,
            )

            if not success:
                logger.info(f"生成摘要失败: {summary}")
                return {"error": "发生ai错误"}

            logger.info(f"成功生成摘要内容：'{summary}'")

            return {"title": title, "url": url, "snippet": summary, "source": "local"}

        except httpx.HTTPStatusError as e:
            logger.warning(f"本地解析URL '{url}' 失败 (HTTP {e.response.status_code})")
            return {"error": f"请求失败，状态码: {e.response.status_code}"}
        except Exception as e:
            logger.error(f"本地解析或总结URL '{url}' 时发生未知异常: {e}", exc_info=True)
            return {"error": f"发生未知错误: {e!s}"}

    async def execute(self, function_args: dict[str, Any]) -> dict[str, Any]:
        """
        执行URL内容提取和总结。优先使用Exa，失败后尝试本地解析。
        """
        # 获取当前文件路径用于缓存键
        import os

        current_file_path = os.path.abspath(__file__)

        # 检查缓存
        cached_result = await tool_cache.get(self.name, function_args, current_file_path)
        if cached_result:
            logger.info(f"缓存命中: {self.name} -> {function_args}")
            return cached_result

        urls_input = function_args.get("urls")
        if not urls_input:

            return {"error": "URL列表不能为空。"}

        # 处理URL输入，确保是列表格式
        urls = parse_urls_from_input(urls_input)
        if not urls:

            return {"error": "提供的字符串中未找到有效的URL。"}

        # 验证URL格式
        valid_urls = validate_urls(urls)
        if not valid_urls:

            return {"error": "未找到有效的URL。"}

        urls = valid_urls
        logger.info(f"准备解析 {len(urls)} 个URL: {urls}")

        successful_results = []
        error_messages = []
        urls_to_retry_locally = []

        # 步骤 1: 尝试使用 Exa API 进行解析
        contents_response = None
        if self.api_manager.is_available():
            logger.info(f"开始使用 Exa API 解析URL: {urls}")
            try:
                # 使用API密钥管理器获取下一个客户端
                exa_client = self.api_manager.get_next_client()
                if not exa_client:
                    logger.error("无法获取Exa客户端")
                else:
                    loop = asyncio.get_running_loop()
                    exa_params = {"text": True, "summary": True}
                    func = functools.partial(exa_client.get_contents, urls, **exa_params)
                    contents_response = await loop.run_in_executor(None, func)
            except Exception as e:
                logger.error(f"执行 Exa URL解析时发生严重异常: {e}", exc_info=True)
                contents_response = None  # 确保异常后为None

        # 步骤 2: 处理Exa的响应
        if contents_response and hasattr(contents_response, "statuses"):
            results_map = (
                {res.url: res for res in contents_response.results} if hasattr(contents_response, "results") else {}
            )
            if contents_response.statuses:
                for status in contents_response.statuses:
                    if status.status == "success":
                        res = results_map.get(status.id)
                        if res:
                            summary = getattr(res, "summary", "")
                            text_snippet = (getattr(res, "text", "")[:300] + "...") if getattr(res, "text", "") else ""
                            snippet = summary or text_snippet or "无摘要"

                            successful_results.append(
                                {
                                    "title": getattr(res, "title", "无标题"),
                                    "url": getattr(res, "url", status.id),
                                    "snippet": snippet,
                                    "source": "exa",
                                }
                            )
                    else:
                        error_tag = getattr(status, "error", "未知错误")
                        logger.warning(f"Exa解析URL '{status.id}' 失败: {error_tag}。准备本地重试。")
                        urls_to_retry_locally.append(status.id)
        else:
            # 如果Exa未配置、API调用失败或返回无效响应，则所有URL都进入本地重试
            urls_to_retry_locally.extend(url for url in urls if url not in [res["url"] for res in successful_results])

        # 步骤 3: 对失败的URL进行本地解析
        if urls_to_retry_locally:
            logger.info(f"开始本地解析以下URL: {urls_to_retry_locally}")
            local_tasks = [self._local_parse_and_summarize(url) for url in urls_to_retry_locally]
            local_results = await asyncio.gather(*local_tasks)

            for i, res in enumerate(local_results):
                url = urls_to_retry_locally[i]
                if "error" in res:
                    error_messages.append(f"URL: {url} - 解析失败: {res['error']}")
                else:
                    successful_results.append(res)

        if not successful_results:


            return {"error": "无法从所有给定的URL获取内容。", "details": error_messages}

        formatted_content = format_url_parse_results(successful_results)

        result = {"type": "url_parse_result", "content": formatted_content, "errors": error_messages}

        # 保存到缓存
        if "error" not in result:
            await tool_cache.set(self.name, function_args, current_file_path, result)

        return result
