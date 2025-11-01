"""
透明连接复用管理器

在不改变原有API的情况下，实现数据库连接的智能复用
"""

import asyncio
import time
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.common.logger import get_logger

logger = get_logger("database.connection_pool")


class ConnectionInfo:
    """连接信息包装器"""

    def __init__(self, session: AsyncSession, created_at: float):
        self.session = session
        self.created_at = created_at
        self.last_used = created_at
        self.in_use = False
        self.ref_count = 0

    def mark_used(self):
        """标记连接被使用"""
        self.last_used = time.time()
        self.in_use = True
        self.ref_count += 1

    def mark_released(self):
        """标记连接被释放"""
        self.in_use = False
        self.ref_count = max(0, self.ref_count - 1)

    def is_expired(self, max_lifetime: float = 300.0, max_idle: float = 60.0) -> bool:
        """检查连接是否过期"""
        current_time = time.time()

        # 检查总生命周期
        if current_time - self.created_at > max_lifetime:
            return True

        # 检查空闲时间
        if not self.in_use and current_time - self.last_used > max_idle:
            return True

        return False

    async def close(self):
        """关闭连接"""
        try:
            # 使用 shield 保护 close 操作，确保即使任务被取消也能完成关闭
            from typing import cast
            await cast(asyncio.Future, asyncio.shield(self.session.close()))
            logger.debug("连接已关闭")
        except asyncio.CancelledError:
            # 这是一个预期的行为，例如在流式聊天中断时
            logger.debug("关闭连接时任务被取消")
            raise
        except Exception as e:
            logger.warning(f"关闭连接时出错: {e}")


class ConnectionPoolManager:
    """透明的连接池管理器"""

    def __init__(self, max_pool_size: int = 10, max_lifetime: float = 300.0, max_idle: float = 60.0):
        self.max_pool_size = max_pool_size
        self.max_lifetime = max_lifetime
        self.max_idle = max_idle

        # 连接池
        self._connections: set[ConnectionInfo] = set()
        self._lock = asyncio.Lock()

        # 统计信息
        self._stats = {
            "total_created": 0,
            "total_reused": 0,
            "total_expired": 0,
            "active_connections": 0,
            "pool_hits": 0,
            "pool_misses": 0,
        }

        # 后台清理任务
        self._cleanup_task: asyncio.Task | None = None
        self._should_cleanup = False

        logger.info(f"连接池管理器初始化完成 (最大池大小: {max_pool_size})")

    async def start(self):
        """启动连接池管理器"""
        if self._cleanup_task is None:
            self._should_cleanup = True
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
            logger.info("✅ 连接池管理器已启动")

    async def stop(self):
        """停止连接池管理器"""
        self._should_cleanup = False

        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None

        # 关闭所有连接
        await self._close_all_connections()
        logger.info("✅ 连接池管理器已停止")

    @asynccontextmanager
    async def get_session(self, session_factory: async_sessionmaker[AsyncSession]):
        """
        获取数据库会话的透明包装器
        如果有可用连接则复用，否则创建新连接
        """
        connection_info = None

        try:
            # 尝试获取现有连接
            connection_info = await self._get_reusable_connection(session_factory)

            if connection_info:
                # 复用现有连接
                connection_info.mark_used()
                self._stats["total_reused"] += 1
                self._stats["pool_hits"] += 1
                logger.debug(f"♻️ 复用连接 (池大小: {len(self._connections)})")
            else:
                # 创建新连接
                session = session_factory()
                connection_info = ConnectionInfo(session, time.time())

                async with self._lock:
                    self._connections.add(connection_info)

                connection_info.mark_used()
                self._stats["total_created"] += 1
                self._stats["pool_misses"] += 1
                logger.debug(f"🆕 创建连接 (池大小: {len(self._connections)})")

            yield connection_info.session
            
            # 🔧 修复：正常退出时提交事务
            # 这对SQLite至关重要，因为SQLite没有autocommit
            if connection_info and connection_info.session:
                try:
                    await connection_info.session.commit()
                except Exception as commit_error:
                    logger.warning(f"提交事务时出错: {commit_error}")
                    await connection_info.session.rollback()
                    raise

        except Exception:
            # 发生错误时回滚连接
            if connection_info and connection_info.session:
                try:
                    await connection_info.session.rollback()
                except Exception as rollback_error:
                    logger.warning(f"回滚连接时出错: {rollback_error}")
            raise
        finally:
            # 释放连接回池中
            if connection_info:
                connection_info.mark_released()

    async def _get_reusable_connection(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> ConnectionInfo | None:
        """获取可复用的连接"""
        async with self._lock:
            # 清理过期连接
            await self._cleanup_expired_connections_locked()

            # 查找可复用的连接
            for connection_info in list(self._connections):
                if not connection_info.in_use and not connection_info.is_expired(self.max_lifetime, self.max_idle):
                    # 验证连接是否仍然有效
                    try:
                        # 执行一个简单的查询来验证连接
                        await connection_info.session.execute(text("SELECT 1"))
                        return connection_info
                    except Exception as e:
                        logger.debug(f"连接验证失败，将移除: {e}")
                        await connection_info.close()
                        self._connections.remove(connection_info)
                        self._stats["total_expired"] += 1

            # 检查是否可以创建新连接
            if len(self._connections) >= self.max_pool_size:
                logger.warning(f"⚠️ 连接池已满 ({len(self._connections)}/{self.max_pool_size})")
                return None

            return None

    async def _cleanup_expired_connections_locked(self):
        """清理过期连接（需要在锁内调用）"""
        expired_connections = [
            connection_info for connection_info in list(self._connections)
            if connection_info.is_expired(self.max_lifetime, self.max_idle) and not connection_info.in_use
        ]

        for connection_info in expired_connections:
            await connection_info.close()
            self._connections.remove(connection_info)
            self._stats["total_expired"] += 1

        if expired_connections:
            logger.debug(f"🧹 清理了 {len(expired_connections)} 个过期连接")

    async def _cleanup_loop(self):
        """后台清理循环"""
        while self._should_cleanup:
            try:
                await asyncio.sleep(30.0)  # 每30秒清理一次

                async with self._lock:
                    await self._cleanup_expired_connections_locked()

                    # 更新统计信息
                    self._stats["active_connections"] = len(self._connections)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"连接池清理循环出错: {e}")
                await asyncio.sleep(10.0)

    async def _close_all_connections(self):
        """关闭所有连接"""
        async with self._lock:
            for connection_info in list(self._connections):
                await connection_info.close()

            self._connections.clear()
            logger.info("所有连接已关闭")

    def get_stats(self) -> dict[str, Any]:
        """获取连接池统计信息"""
        total_requests = self._stats["pool_hits"] + self._stats["pool_misses"]
        pool_efficiency = (self._stats["pool_hits"] / max(1, total_requests)) * 100 if total_requests > 0 else 0
        
        return {
            **self._stats,
            "active_connections": len(self._connections),
            "max_pool_size": self.max_pool_size,
            "pool_efficiency": f"{pool_efficiency:.2f}%",
        }


# 全局连接池管理器实例
_connection_pool_manager: ConnectionPoolManager | None = None


def get_connection_pool_manager() -> ConnectionPoolManager:
    """获取全局连接池管理器实例"""
    global _connection_pool_manager
    if _connection_pool_manager is None:
        _connection_pool_manager = ConnectionPoolManager()
    return _connection_pool_manager


async def start_connection_pool():
    """启动连接池"""
    manager = get_connection_pool_manager()
    await manager.start()


async def stop_connection_pool():
    """停止连接池"""
    global _connection_pool_manager
    if _connection_pool_manager:
        await _connection_pool_manager.stop()
        _connection_pool_manager = None
