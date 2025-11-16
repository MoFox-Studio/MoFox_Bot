from fastapi import Depends, HTTPException, Security
from fastapi.security.api_key import APIKeyHeader
from starlette.status import HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN

from src.common.logger import get_logger
from src.config.config import global_config as bot_config

logger = get_logger("security")

API_KEY_HEADER = "X-API-Key"
api_key_header_auth = APIKeyHeader(name=API_KEY_HEADER, auto_error=True)


async def get_api_key(api_key: str = Security(api_key_header_auth)) -> str:
    """
    FastAPI 依赖项，用于验证API密钥。
    从请求头中提取 X-API-Key 并验证它是否存在于配置的有效密钥列表中。
    """
    valid_keys = bot_config.plugin_http_system.plugin_api_valid_keys
    if not valid_keys:
        logger.warning("API密钥认证已启用，但未配置任何有效的API密钥。所有请求都将被拒绝。")
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail="服务未正确配置API密钥",
        )
    if api_key not in valid_keys:
        logger.warning(f"无效的API密钥: {api_key}")
        raise HTTPException(
            status_code=HTTP_403_FORBIDDEN,
            detail="无效的API密钥",
        )
    return api_key