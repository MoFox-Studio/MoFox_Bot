"""
适配器基类

面向不同外部平台（如 NapCat/QQ、Telegram 等）的统一抽象。
目标：高可扩展、低耦合、模块化，遵循开闭原则（对扩展开放、对修改封闭）。
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any, Awaitable, Callable, Optional

# 仅用于事件上报的类型提示，避免循环依赖带来的运行期问题
from .base_event import BaseEvent

logger = logging.getLogger(__name__)


class AdapterStatus:
    """适配器生命周期状态枚举（字符串表示，便于序列化/日志）"""

    INIT = "init"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


class SendTarget:
    """统一的发送目标抽象。

    不同平台按需使用其一：
    - user_id: 私聊/好友
    - group_id: 群聊
    - guild_id/channel_id: 频道类平台
    - chat_id/thread_id: Telegram 等平台（话题/子线程）
    """

    def __init__(
        self,
        user_id: Optional[str] = None,
        group_id: Optional[str] = None,
        guild_id: Optional[str] = None,
        channel_id: Optional[str] = None,
        chat_id: Optional[str | int] = None,
        thread_id: Optional[int] = None,
    ) -> None:
        self.user_id = user_id
        self.group_id = group_id
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.chat_id = chat_id
        self.thread_id = thread_id

    def __repr__(self) -> str:  # pragma: no cover - 仅用于调试输出
        return (
            "SendTarget("
            f"user_id={self.user_id}, group_id={self.group_id}, "
            f"guild_id={self.guild_id}, channel_id={self.channel_id}, "
            f"chat_id={self.chat_id}, thread_id={self.thread_id})"
        )


class SendResult:
    """统一的发送结果抽象。"""

    def __init__(
        self,
        success: bool,
        message_id: Optional[str] = None,
        raw: Any = None,
        error: Optional[str] = None,
    ) -> None:
        self.success = success
        self.message_id = message_id
        self.raw = raw
        self.error = error

    def __repr__(self) -> str:  # pragma: no cover - 仅用于调试输出
        return (
            f"SendResult(success={self.success}, message_id={self.message_id}, "
            f"error={self.error})"
        )


class BaseAdapter(ABC):
    """适配器基类。

    设计要点：
    - 模板方法（template method）：`start/stop` 负责状态流转，子类实现 `_start_impl/_stop_impl`。
    - 事件解耦：通过 `register_event_sink` 注入事件下游（路由/插件系统），通过 `emit` 上报。
    - 发送能力：保留标准接口，提供 `options: dict` 作为扩展点，便于承载平台特性（如 parse_mode、reply_markup）。
    - 开闭原则：新增平台能力以“实现/覆盖”方式扩展，而非修改基类签名。
    """

    adapter_name: str = "base"
    platform: str = "generic"

    def __init__(self, config: Optional[dict] = None) -> None:
        self.config: dict = config or {}
        self._status: str = AdapterStatus.INIT
        self._event_sink: Optional[Callable[[BaseEvent], Awaitable[None]]] = None

        # 运行时事件循环（在某些环境中需要显式创建）
        try:
            self._loop = asyncio.get_event_loop()
        except RuntimeError:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)

    # ------------------ 生命周期（模板方法） ------------------
    async def start(self) -> None:
        """启动与平台的连接（如 WS/HTTP/轮询）。"""
        if self._status in {AdapterStatus.RUNNING, AdapterStatus.STARTING}:
            logger.debug("Adapter already running or starting: %s", self)
            return
        self._status = AdapterStatus.STARTING
        try:
            await self._start_impl()
            self._status = AdapterStatus.RUNNING
        except Exception as e:  # pragma: no cover - 保护性日志
            self._status = AdapterStatus.ERROR
            logger.exception("Adapter start failed: %s", e)
            raise

    async def stop(self) -> None:
        """关闭与平台的连接/任务。"""
        if self._status in {AdapterStatus.STOPPED, AdapterStatus.STOPPING, AdapterStatus.INIT}:
            logger.debug("Adapter already stopped or not started: %s", self)
            return
        self._status = AdapterStatus.STOPPING
        try:
            await self._stop_impl()
            self._status = AdapterStatus.STOPPED
        except Exception as e:  # pragma: no cover - 保护性日志
            self._status = AdapterStatus.ERROR
            logger.exception("Adapter stop failed: %s", e)
            raise

    @abstractmethod
    async def _start_impl(self) -> None:
        """子类实现：建立会话/连接、注册回调等。"""

    @abstractmethod
    async def _stop_impl(self) -> None:
        """子类实现：断开会话/连接、清理任务。"""

    # ------------------ 事件上报 ------------------
    def register_event_sink(self, sink: Callable[[BaseEvent], Awaitable[None]]) -> None:
        """注入事件下游（例如消息路由），适配器收到平台事件后调用 `emit` 上报。"""

        self._event_sink = sink

    async def emit(self, event: BaseEvent) -> None:
        """向框架上报标准化事件。"""

        if not self._event_sink:
            logger.warning("No event sink registered; dropping event: %s", event)
            return
        await self._event_sink(event)

    # ------------------ 发送能力（可扩展） ------------------
    async def send_text(
        self,
        target: SendTarget,
        content: str,
        reply_to: Optional[str] = None,
        options: Optional[dict] = None,
    ) -> SendResult:
        """发送文本消息。

        默认委托至子类实现 `_send_text_impl`，`options` 用于承载平台特性：
        - Telegram: parse_mode/entities/reply_markup/message_thread_id 等
        - QQ/OneBot: at/extra/auto_escape 等
        """

        return await self._send_text_impl(target, content, reply_to=reply_to, options=options or {})

    async def send_image(
        self,
        target: SendTarget,
        image: bytes | str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        options: Optional[dict] = None,
    ) -> SendResult:
        """发送图片（字节/路径/URL 由子类决定支持范围）。

        默认抛出未实现，子类按需覆盖。
        """

        raise NotImplementedError

    async def recall(self, message_id: str) -> bool:
        """撤回/删除消息。子类按平台能力覆盖。"""

        raise NotImplementedError

    @abstractmethod
    async def _send_text_impl(
        self,
        target: SendTarget,
        content: str,
        reply_to: Optional[str] = None,
        options: Optional[dict] = None,
    ) -> SendResult:
        """子类实现：文本消息的实际发送。"""

    # ------------------ 健康/能力/配置 ------------------
    def capabilities(self) -> frozenset[str]:
        """返回适配器能力集合。

        示例：{"webhook", "polling", "edit", "delete", "inline", "media_group"}
        上层可据此做能力判断，而无需关心具体平台细节。
        """

        return frozenset()

    async def health(self) -> dict[str, Any]:
        """返回适配器健康/元信息。"""

        return {
            "status": self._status,
            "adapter": self.adapter_name,
            "platform": self.platform,
        }

    def update_config(self, new_config: dict) -> None:
        """在运行时更新配置，鼓励子类按需重载并应用差异。"""

        self.config.update(new_config or {})

    # ------------------ 调试/表示 ------------------
    def __repr__(self) -> str:  # pragma: no cover - 仅用于调试输出
        return f"<{self.__class__.__name__} platform={self.platform} status={self._status}>"

