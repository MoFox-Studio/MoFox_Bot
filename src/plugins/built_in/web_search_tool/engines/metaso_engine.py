"""
Metaso Search Engine (Chat Completions Mode)
"""
from typing import Any

import httpx
import orjson

from src.common.logger import get_logger
from src.plugin_system.apis import config_api

from ..utils.api_key_manager import create_api_key_manager_from_config
from .base import BaseSearchEngine

logger = get_logger(__name__)


class MetasoClient:
    """A client to interact with the Metaso API."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://metaso.cn/api/v1"
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def search(self, query: str, **kwargs) -> list[dict[str, Any]]:
        """Perform a search using the Metaso Chat Completions API."""
        payload = {"model": "fast", "stream": True, "messages": [{"role": "user", "content": query}]}
        search_url = f"{self.base_url}/chat/completions"
        full_response_content = ""

        async with httpx.AsyncClient(timeout=90.0) as client:
            try:
                async with client.stream("POST", search_url, headers=self.headers, json=payload) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if line.startswith("data:"):
                            data_str = line[len("data:") :].strip()
                            if data_str == "[DONE]":
                                break
                            try:
                                data = orjson.loads(data_str)
                                delta = data.get("choices", [{}])[0].get("delta", {})
                                content_chunk = delta.get("content")
                                if content_chunk:
                                    full_response_content += content_chunk
                            except orjson.JSONDecodeError:
                                logger.warning(f"Metaso stream: could not decode JSON line: {data_str}")
                                continue

                if not full_response_content:
                    logger.warning("Metaso search returned an empty stream.")
                    return []

                return [
                    {
                        "title": query,
                        "url": "https://metaso.cn/",
                        "snippet": full_response_content,
                        "provider": "Metaso (Chat)",
                    }
                ]
            except httpx.HTTPStatusError as e:
                logger.error(f"HTTP error occurred while searching with Metaso Chat: {e.response.text}")
                return []
            except Exception as e:
                logger.error(f"An error occurred while searching with Metaso Chat: {e}", exc_info=True)
                return []


class MetasoSearchEngine(BaseSearchEngine):
    """Metaso Search Engine implementation."""

    def __init__(self):
        self._initialize_clients()

    def _initialize_clients(self):
        """Initialize Metaso clients."""
        metaso_api_keys = config_api.get_global_config("web_search.metaso_api_keys", None)
        self.api_manager = create_api_key_manager_from_config(
            metaso_api_keys, lambda key: MetasoClient(api_key=key), "Metaso"
        )

    def is_available(self) -> bool:
        """Check if the Metaso search engine is available."""
        return self.api_manager.is_available()

    async def search(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        """Execute a Metaso search."""
        if not self.is_available():
            return []

        query = args["query"]
        try:
            metaso_client = self.api_manager.get_next_client()
            if not metaso_client:
                logger.error("Could not get Metaso client.")
                return []

            return await metaso_client.search(query)
        except Exception as e:
            logger.error(f"Metaso search failed: {e}", exc_info=True)
            return []
