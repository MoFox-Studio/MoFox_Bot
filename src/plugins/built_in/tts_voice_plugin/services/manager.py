"""
服务管理器

用于注册和获取插件内部使用的服务实例。
"""

from typing import Any

_services: dict[str, Any] = {}


def register_service(name: str, instance: Any) -> None:
    """注册一个服务实例"""
    _services[name] = instance


def get_service(name: str) -> Any:
    """获取一个已注册的服务实例"""
    return _services.get(name)
