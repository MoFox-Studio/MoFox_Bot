from src.config.config import model_config

used_client_types = {provider.client_type for provider in model_config.api_providers}

if "openai" in used_client_types:
    from . import openai_client  # noqa: F401
if "aiohttp_gemini" in used_client_types:
    from . import aiohttp_gemini_client  # noqa: F401
