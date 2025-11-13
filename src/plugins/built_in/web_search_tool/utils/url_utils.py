"""
URL processing utilities
"""

import re


def parse_urls_from_input(urls_input) -> list[str]:
    """
    从输入中解析URL列表
    """
    if isinstance(urls_input, str):
        # 如果是字符串，尝试解析为URL列表
        # 提取所有HTTP/HTTPS URL
        url_pattern = r"https?://[^\s\],]+"
        urls = re.findall(url_pattern, urls_input)
        if not urls:
            # 如果没有找到标准URL，将整个字符串作为单个URL
            if urls_input.strip().startswith(("http://", "https://")):
                urls = [urls_input.strip()]
            else:
                return []
    elif isinstance(urls_input, list):
        urls = [url.strip() for url in urls_input if isinstance(url, str) and url.strip()]
    else:
        return []

    return urls


def validate_urls(urls: list[str]) -> list[str]:
    """
    验证URL格式，返回有效的URL列表
    """
    return [url for url in urls if url.startswith(("http://", "https://"))]
