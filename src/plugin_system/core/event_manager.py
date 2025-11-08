"""
事件管理器 - 实现Event和EventHandler的单例管理
提供统一的事件注册、管理和触发接口
"""
import asyncio
from threading import Lock
from typing import Any, Optional

from src.common.logger import get_logger
from src.plugin_system.base.base_event import BaseEvent, HandlerResultsCollection
from src.plugin_system.base.base_events_handler import BaseEventHandler
from src.plugin_system.base.component_types import EventType

logger = get_logger("event_manager")


class EventManager:
    """事件管理器单例类

    负责管理所有事件和事件处理器的注册、订阅、触发等操作
    使用单例模式确保全局只有一个事件管理实例
    """

    _instance: Optional["EventManager"] = None
    _lock = Lock()

    def __new__(cls) -> "EventManager":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return

        self._events: dict[str, BaseEvent] = {}
        self._event_handlers: dict[str, BaseEventHandler] = {}
        self._pending_subscriptions: dict[str, list[str]] = {}  # 缓存失败的订阅
        self._scheduler_callback: Any | None = None  # scheduler 回调函数
        self._initialized = True
        logger.info("EventManager 单例初始化完成")

    def register_event(
        self,
        event_name: EventType | str,
        allowed_subscribers: list[str] | None = None,
        allowed_triggers: list[str] | None = None,
    ) -> bool:
        """注册一个新的事件

        Args:
            event_name Union[EventType, str]: 事件名称
            allowed_subscribers: List[str]: 事件订阅者白名单,
            allowed_triggers: List[str]: 事件触发插件白名单
        Returns:
            bool: 注册成功返回True，已存在返回False
        """
        if allowed_triggers is None:
            allowed_triggers = []
        if allowed_subscribers is None:
            allowed_subscribers = []
        _event_name = event_name.value if isinstance(event_name, EventType) else event_name
        if _event_name in self._events:
            logger.warning(f"事件 {_event_name} 已存在，跳过注册")
            return False

        event = BaseEvent(_event_name, allowed_subscribers, allowed_triggers)
        self._events[_event_name] = event
        logger.debug(f"事件 {_event_name} 注册成功")

        # 检查是否有缓存的订阅需要处理
        self._process_pending_subscriptions(_event_name)

        return True

    def get_event(self, event_name: EventType | str) -> BaseEvent | None:
        """获取指定事件实例

        Args:
            event_name Union[EventType, str]: 事件名称

        Returns:
            BaseEvent: 事件实例，不存在返回None
        """
        _event_name = event_name.value if isinstance(event_name, EventType) else event_name
        return self._events.get(_event_name)

    def get_all_events(self) -> dict[str, BaseEvent]:
        """获取所有已注册的事件

        Returns:
            Dict[str, BaseEvent]: 所有事件的字典
        """
        return self._events.copy()

    def get_enabled_events(self) -> dict[str, BaseEvent]:
        """获取所有已启用的事件

        Returns:
            Dict[str, BaseEvent]: 已启用事件的字典
        """
        return {name: event for name, event in self._events.items() if event.enabled}

    def get_disabled_events(self) -> dict[str, BaseEvent]:
        """获取所有已禁用的事件

        Returns:
            Dict[str, BaseEvent]: 已禁用事件的字典
        """
        return {name: event for name, event in self._events.items() if not event.enabled}

    def enable_event(self, event_name: EventType | str) -> bool:
        """启用指定事件

        Args:
            event_name Union[EventType, str]: 事件名称

        Returns:
            bool: 成功返回True，事件不存在返回False
        """
        event = self.get_event(event_name)
        if event is None:
            logger.error(f"事件 {event_name} 不存在，无法启用")
            return False

        event.enabled = True
        logger.info(f"事件 {event_name} 已启用")
        return True

    def disable_event(self, event_name: EventType | str) -> bool:
        """禁用指定事件

        Args:
            event_name Union[EventType, str]: 事件名称

        Returns:
            bool: 成功返回True，事件不存在返回False
        """
        event = self.get_event(event_name)
        if event is None:
            logger.error(f"事件 {event_name} 不存在，无法禁用")
            return False

        event.enabled = False
        logger.info(f"事件 {event_name} 已禁用")
        return True

    def register_event_handler(self, handler_class: type[BaseEventHandler], plugin_config: dict | None = None) -> bool:
        """注册事件处理器

        Args:
            handler_class (Type[BaseEventHandler]): 事件处理器类
            plugin_config (Optional[dict]): 插件配置字典，默认为None

        Returns:
            bool: 注册成功返回True，已存在返回False
        """
        handler_name = handler_class.handler_name or handler_class.__name__.lower().replace("handler", "")

        if EventType.UNKNOWN in handler_class.init_subscribe:
            logger.error(f"事件处理器 {handler_name} 不能订阅 UNKNOWN 事件")
            return False
        if handler_name in self._event_handlers:
            logger.warning(f"事件处理器 {handler_name} 已存在，跳过注册")
            return False

        # 创建事件处理器实例，传递插件配置
        handler_instance = handler_class()
        handler_instance.plugin_config = plugin_config
        if plugin_config is not None and hasattr(handler_instance, "set_plugin_config"):
            handler_instance.set_plugin_config(plugin_config)

        self._event_handlers[handler_name] = handler_instance

        # 处理init_subscribe，缓存失败的订阅
        if self._event_handlers[handler_name].init_subscribe:
            failed_subscriptions: list[str] = []
            for event_name in self._event_handlers[handler_name].init_subscribe:
                if not self.subscribe_handler_to_event(handler_name, event_name):
                    _event_name = event_name.value if isinstance(event_name, EventType) else event_name
                    failed_subscriptions.append(_event_name)

            # 缓存失败的订阅
            if failed_subscriptions:
                self._pending_subscriptions[handler_name] = failed_subscriptions
                logger.warning(f"事件处理器 {handler_name} 的部分订阅失败，已缓存: {failed_subscriptions}")

        logger.info(f"事件处理器 {handler_name} 注册成功")
        return True

    def get_event_handler(self, handler_name: str) -> BaseEventHandler | None:
        """获取指定事件处理器实例

        Args:
            handler_name (str): 处理器名称

        Returns:
            BaseEventHandler: 处理器实例，不存在返回None
        """
        return self._event_handlers.get(handler_name)

    def get_all_event_handlers(self) -> dict[str, BaseEventHandler]:
        """获取所有已注册的事件处理器

        Returns:
            Dict[str, BaseEventHandler]: 所有处理器的字典
        """
        return self._event_handlers.copy()

    def subscribe_handler_to_event(self, handler_name: str, event_name: EventType | str) -> bool:
        """订阅事件处理器到指定事件

        Args:
            handler_name (str): 处理器名称
            event_name Union[EventType, str]: 事件名称

        Returns:
            bool: 订阅成功返回True
        """
        handler_instance = self.get_event_handler(handler_name)
        if handler_instance is None:
            logger.error(f"事件处理器 {handler_name} 不存在，无法订阅到事件 {event_name}")
            return False

        event = self.get_event(event_name)
        if event is None:
            logger.error(f"事件 {event_name} 不存在，无法订阅事件处理器 {handler_name}")
            return False

        if handler_instance in event.subscribers:
            logger.warning(f"事件处理器 {handler_name} 已经订阅了事件 {event_name}，跳过重复订阅")
            return True

        # 白名单检查
        if event.allowed_subscribers and handler_name not in event.allowed_subscribers:
            logger.warning(f"事件处理器 {handler_name} 不在事件 {event_name} 的订阅者白名单中，无法订阅")
            return False

        event.subscribers.append(handler_instance)

        # 按权重从高到低排序订阅者
        event.subscribers.sort(key=lambda h: getattr(h, "weight", 0), reverse=True)

        logger.info(f"事件处理器 {handler_name} 成功订阅到事件 {event_name}，当前权重排序完成")
        return True

    def unsubscribe_handler_from_event(self, handler_name: str, event_name: EventType | str) -> bool:
        """从指定事件取消订阅事件处理器

        Args:
            handler_name (str): 处理器名称
            event_name Union[EventType, str]: 事件名称

        Returns:
            bool: 取消订阅成功返回True
        """
        event = self.get_event(event_name)
        if event is None:
            logger.error(f"事件 {event_name} 不存在，无法取消订阅")
            return False

        # 查找并移除处理器实例
        removed = False
        for subscriber in event.subscribers[:]:
            if hasattr(subscriber, "handler_name") and subscriber.handler_name == handler_name:
                event.subscribers.remove(subscriber)
                removed = True
                break

        if removed:
            logger.info(f"事件处理器 {handler_name} 成功从事件 {event_name} 取消订阅")
        else:
            logger.warning(f"事件处理器 {handler_name} 未订阅事件 {event_name}")

        return removed

    def get_event_subscribers(self, event_name: EventType | str) -> dict[str, BaseEventHandler]:
        """获取订阅指定事件的所有事件处理器

        Args:
            event_name Union[EventType, str]: 事件名称

        Returns:
            Dict[str, BaseEventHandler]: 处理器字典，键为处理器名称，值为处理器实例
        """
        event = self.get_event(event_name)
        if event is None:
            return {}

        return {handler.handler_name: handler for handler in event.subscribers}

    async def trigger_event(
        self, event_name: EventType | str, permission_group: str | None = "", **kwargs
    ) -> HandlerResultsCollection | None:
        """触发指定事件

        Args:
            event_name Union[EventType, str]: 事件名称
            plugin_name str: 触发事件的插件名
            **kwargs: 传递给处理器的参数

        Returns:
            HandlerResultsCollection: 所有处理器的执行结果，事件不存在返回None
        """
        params = kwargs or {}

        event = self.get_event(event_name)
        if event is None:
            logger.error(f"事件 {event_name} 不存在，无法触发")
            return None

        # 插件白名单检查
        if event.allowed_triggers and not permission_group:
            logger.warning(f"事件 {event_name} 存在触发者白名单，缺少plugin_name无法验证权限，已拒绝触发！")
            return None
        elif event.allowed_triggers and permission_group not in event.allowed_triggers:
            logger.warning(f"插件 {permission_group} 没有权限触发事件 {event_name}，已拒绝触发！")
            return None

        # 🔧 修复：异步通知 scheduler，避免阻塞当前事件流程
        if hasattr(self, "_scheduler_callback") and self._scheduler_callback:
            try:
                # 使用 create_task 异步执行，避免死锁
                asyncio.create_task(self._scheduler_callback(event_name, params))
            except Exception as e:
                logger.error(f"调用 scheduler 回调时出错: {e}", exc_info=True)

        return await event.activate(params)

    def register_scheduler_callback(self, callback) -> None:
        """注册 scheduler 回调函数

        Args:
            callback: async callable，接收 (event_name, params) 参数
        """
        self._scheduler_callback = callback
        logger.info("Scheduler 回调已注册")

    def unregister_scheduler_callback(self) -> None:
        """取消注册 scheduler 回调"""
        self._scheduler_callback = None
        logger.info("Scheduler 回调已取消注册")

    def init_default_events(self) -> None:
        """初始化默认事件"""
        default_events = [
            EventType.ON_START,
            EventType.ON_STOP,
            EventType.ON_PLAN,
            EventType.ON_MESSAGE,
            EventType.POST_LLM,
            EventType.AFTER_LLM,
            EventType.POST_SEND,
            EventType.AFTER_SEND,
        ]

        for event_name in default_events:
            self.register_event(event_name, allowed_triggers=["SYSTEM"])

        logger.info("默认事件初始化完成")

    def clear_all_events(self) -> None:
        """清除所有事件和处理器（主要用于测试）"""
        self._events.clear()
        self._event_handlers.clear()
        logger.info("所有事件和处理器已清除")

    def get_event_summary(self) -> dict[str, Any]:
        """获取事件系统摘要

        Returns:
            Dict[str, Any]: 包含事件系统统计信息的字典
        """
        enabled_events = self.get_enabled_events()
        disabled_events = self.get_disabled_events()

        return {
            "total_events": len(self._events),
            "enabled_events": len(enabled_events),
            "disabled_events": len(disabled_events),
            "total_handlers": len(self._event_handlers),
            "event_names": list(self._events.keys()),
            "handler_names": list(self._event_handlers.keys()),
            "pending_subscriptions": len(self._pending_subscriptions),
        }

    def _process_pending_subscriptions(self, event_name: str) -> None:
        """处理指定事件的缓存订阅

        Args:
            event_name Union[EventType, str]: 事件名称
        """
        handlers_to_remove = []

        for handler_name, pending_events in self._pending_subscriptions.items():
            if event_name in pending_events:
                if self.subscribe_handler_to_event(handler_name, event_name):
                    pending_events.remove(event_name)
                    logger.info(f"成功处理缓存订阅: {handler_name} -> {event_name}")

                # 如果该处理器没有更多待处理订阅，标记为移除
                if not pending_events:
                    handlers_to_remove.append(handler_name)

        # 清理已完成的处理器缓存
        for handler_name in handlers_to_remove:
            del self._pending_subscriptions[handler_name]

    def process_all_pending_subscriptions(self) -> int:
        """处理所有缓存的订阅

        Returns:
            int: 成功处理的订阅数量
        """
        processed_count = 0

        # 复制待处理订阅，避免在迭代时修改字典
        pending_copy = dict(self._pending_subscriptions)

        for handler_name, pending_events in pending_copy.items():
            for event_name in pending_events[:]:  # 使用切片避免修改列表
                if self.subscribe_handler_to_event(handler_name, event_name):
                    pending_events.remove(event_name)
                    processed_count += 1

        # 清理已完成的处理器缓存
        handlers_to_remove = [name for name, events in self._pending_subscriptions.items() if not events]
        for handler_name in handlers_to_remove:
            del self._pending_subscriptions[handler_name]

        if processed_count > 0:
            logger.info(f"批量处理缓存订阅完成，共处理 {processed_count} 个订阅")

        return processed_count


# 创建全局事件管理器实例
event_manager = EventManager()
