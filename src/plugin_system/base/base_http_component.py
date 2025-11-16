from abc import ABC, abstractmethod

from fastapi import APIRouter

from .component_types import ComponentType, RouterInfo


class BaseRouterComponent(ABC):
    """
    用于暴露HTTP端点的组件基类。
    插件开发者应继承此类，并实现 register_endpoints 方法来定义API路由。
    """
    # 组件元数据，由插件管理器读取
    component_name: str
    component_description: str
    component_version: str = "1.0.0"

    # 每个组件实例都会管理自己的APIRouter
    router: APIRouter

    def __init__(self):
        self.router = APIRouter()
        self.register_endpoints()

    @abstractmethod
    def register_endpoints(self) -> None:
        """
        【开发者必须实现】
        在此方法中定义所有HTTP端点。
        """
        pass

    @classmethod
    def get_router_info(cls) -> "RouterInfo":
        """从类属性生成RouterInfo"""
        return RouterInfo(
            name=cls.component_name,
            description=getattr(cls, "component_description", "路由组件"),
            component_type=ComponentType.ROUTER,
        )
