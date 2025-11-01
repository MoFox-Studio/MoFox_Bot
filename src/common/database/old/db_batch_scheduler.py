"""
数据库批量调度器
实现多个数据库请求的智能合并和批量处理，减少数据库连接竞争
"""

import asyncio
import time
from collections import defaultdict, deque
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, TypeVar

from sqlalchemy import delete, insert, select, update

from src.common.database.sqlalchemy_database_api import get_db_session
from src.common.logger import get_logger

logger = get_logger("db_batch_scheduler")

T = TypeVar("T")


@dataclass
class BatchOperation:
    """批量操作基础类"""

    operation_type: str  # 'select', 'insert', 'update', 'delete'
    model_class: Any
    conditions: dict[str, Any]
    data: dict[str, Any] | None = None
    callback: Callable | None = None
    future: asyncio.Future | None = None
    timestamp: float = 0.0

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()


@dataclass
class BatchResult:
    """批量操作结果"""

    success: bool
    data: Any = None
    error: str | None = None


class DatabaseBatchScheduler:
    """数据库批量调度器"""

    def __init__(
        self,
        batch_size: int = 50,
        max_wait_time: float = 0.1,  # 100ms
        max_queue_size: int = 1000,
    ):
        self.batch_size = batch_size
        self.max_wait_time = max_wait_time
        self.max_queue_size = max_queue_size

        # 操作队列，按操作类型和模型分类
        self.operation_queues: dict[str, deque] = defaultdict(deque)

        # 调度控制
        self._scheduler_task: asyncio.Task | None = None
        self._is_running = False
        self._lock = asyncio.Lock()

        # 统计信息
        self.stats = {"total_operations": 0, "batched_operations": 0, "cache_hits": 0, "execution_time": 0.0}

        # 简单的结果缓存（用于频繁的查询）
        self._result_cache: dict[str, tuple[Any, float]] = {}
        self._cache_ttl = 5.0  # 5秒缓存

    async def start(self):
        """启动调度器"""
        if self._is_running:
            return

        self._is_running = True
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())
        logger.info("数据库批量调度器已启动")

    async def stop(self):
        """停止调度器"""
        if not self._is_running:
            return

        self._is_running = False
        if self._scheduler_task:
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass

        # 处理剩余的操作
        await self._flush_all_queues()
        logger.info("数据库批量调度器已停止")

    def _generate_cache_key(self, operation_type: str, model_class: Any, conditions: dict[str, Any]) -> str:
        """生成缓存键"""
        # 简单的缓存键生成，实际可以根据需要优化
        key_parts = [operation_type, model_class.__name__, str(sorted(conditions.items()))]
        return "|".join(key_parts)

    def _get_from_cache(self, cache_key: str) -> Any | None:
        """从缓存获取结果"""
        if cache_key in self._result_cache:
            result, timestamp = self._result_cache[cache_key]
            if time.time() - timestamp < self._cache_ttl:
                self.stats["cache_hits"] += 1
                return result
            else:
                # 清理过期缓存
                del self._result_cache[cache_key]
        return None

    def _set_cache(self, cache_key: str, result: Any):
        """设置缓存"""
        self._result_cache[cache_key] = (result, time.time())

    async def add_operation(self, operation: BatchOperation) -> asyncio.Future:
        """添加操作到队列"""
        # 检查是否可以立即返回缓存结果
        if operation.operation_type == "select":
            cache_key = self._generate_cache_key(operation.operation_type, operation.model_class, operation.conditions)
            cached_result = self._get_from_cache(cache_key)
            if cached_result is not None:
                if operation.callback:
                    operation.callback(cached_result)
                future = asyncio.get_event_loop().create_future()
                future.set_result(cached_result)
                return future

        # 创建future用于返回结果
        future = asyncio.get_event_loop().create_future()
        operation.future = future

        # 添加到队列
        queue_key = f"{operation.operation_type}_{operation.model_class.__name__}"

        async with self._lock:
            if len(self.operation_queues[queue_key]) >= self.max_queue_size:
                # 队列满了，直接执行
                await self._execute_operations([operation])
            else:
                self.operation_queues[queue_key].append(operation)
                self.stats["total_operations"] += 1

        return future

    async def _scheduler_loop(self):
        """调度器主循环"""
        while self._is_running:
            try:
                await asyncio.sleep(self.max_wait_time)
                await self._flush_all_queues()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"调度器循环异常: {e}", exc_info=True)

    async def _flush_all_queues(self):
        """刷新所有队列"""
        async with self._lock:
            if not any(self.operation_queues.values()):
                return

            # 复制队列内容，避免长时间占用锁
            queues_copy = {key: deque(operations) for key, operations in self.operation_queues.items()}
            # 清空原队列
            for queue in self.operation_queues.values():
                queue.clear()

        # 批量执行各队列的操作
        for operations in queues_copy.values():
            if operations:
                await self._execute_operations(list(operations))

    async def _execute_operations(self, operations: list[BatchOperation]):
        """执行批量操作"""
        if not operations:
            return

        start_time = time.time()

        try:
            # 按操作类型分组
            op_groups = defaultdict(list)
            for op in operations:
                op_groups[op.operation_type].append(op)

            # 为每种操作类型创建批量执行任务
            tasks = []
            for op_type, ops in op_groups.items():
                if op_type == "select":
                    tasks.append(self._execute_select_batch(ops))
                elif op_type == "insert":
                    tasks.append(self._execute_insert_batch(ops))
                elif op_type == "update":
                    tasks.append(self._execute_update_batch(ops))
                elif op_type == "delete":
                    tasks.append(self._execute_delete_batch(ops))

            # 并发执行所有操作
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # 处理结果
            for i, result in enumerate(results):
                operation = operations[i]
                if isinstance(result, Exception):
                    if operation.future and not operation.future.done():
                        operation.future.set_exception(result)
                else:
                    if operation.callback:
                        try:
                            operation.callback(result)
                        except Exception as e:
                            logger.warning(f"操作回调执行失败: {e}")

                    if operation.future and not operation.future.done():
                        operation.future.set_result(result)

                    # 缓存查询结果
                    if operation.operation_type == "select":
                        cache_key = self._generate_cache_key(
                            operation.operation_type, operation.model_class, operation.conditions
                        )
                        self._set_cache(cache_key, result)

            self.stats["batched_operations"] += len(operations)

        except Exception as e:
            logger.error(f"批量操作执行失败: {e}", exc_info="")
            # 设置所有future的异常状态
            for operation in operations:
                if operation.future and not operation.future.done():
                    operation.future.set_exception(e)
        finally:
            self.stats["execution_time"] += time.time() - start_time

    async def _execute_select_batch(self, operations: list[BatchOperation]):
        """批量执行查询操作"""
        # 合并相似的查询条件
        merged_conditions = self._merge_select_conditions(operations)

        async with get_db_session() as session:
            results = []
            for conditions, ops in merged_conditions.items():
                try:
                    # 构建查询
                    query = select(ops[0].model_class)
                    for field_name, value in conditions.items():
                        model_attr = getattr(ops[0].model_class, field_name)
                        if isinstance(value, list | tuple | set):
                            query = query.where(model_attr.in_(value))
                        else:
                            query = query.where(model_attr == value)

                    # 执行查询
                    result = await session.execute(query)
                    data = result.scalars().all()

                    # 分发结果到各个操作
                    for op in ops:
                        if len(conditions) == 1 and len(ops) == 1:
                            # 单个查询，直接返回所有结果
                            op_result = data
                        else:
                            # 需要根据条件过滤结果
                            op_result = [
                                item
                                for item in data
                                if all(getattr(item, k) == v for k, v in op.conditions.items() if hasattr(item, k))
                            ]
                        results.append(op_result)

                except Exception as e:
                    logger.error(f"批量查询失败: {e}", exc_info=True)
                    results.append([])

            return results if len(results) > 1 else results[0] if results else []

    async def _execute_insert_batch(self, operations: list[BatchOperation]):
        """批量执行插入操作"""
        async with get_db_session() as session:
            try:
                # 收集所有要插入的数据
                all_data = [op.data for op in operations if op.data]
                if not all_data:
                    return []

                # 批量插入
                stmt = insert(operations[0].model_class).values(all_data)
                result = await session.execute(stmt)
                await session.commit()

                return [result.rowcount] * len(operations)

            except Exception as e:
                await session.rollback()
                logger.error(f"批量插入失败: {e}", exc_info=True)
                return [0] * len(operations)

    async def _execute_update_batch(self, operations: list[BatchOperation]):
        """批量执行更新操作"""
        async with get_db_session() as session:
            try:
                results = []
                for op in operations:
                    if not op.data or not op.conditions:
                        results.append(0)
                        continue

                    stmt = update(op.model_class)
                    for field_name, value in op.conditions.items():
                        model_attr = getattr(op.model_class, field_name)
                        if isinstance(value, list | tuple | set):
                            stmt = stmt.where(model_attr.in_(value))
                        else:
                            stmt = stmt.where(model_attr == value)

                    stmt = stmt.values(**op.data)
                    result = await session.execute(stmt)
                    results.append(result.rowcount)

                await session.commit()
                return results

            except Exception as e:
                await session.rollback()
                logger.error(f"批量更新失败: {e}", exc_info=True)
                return [0] * len(operations)

    async def _execute_delete_batch(self, operations: list[BatchOperation]):
        """批量执行删除操作"""
        async with get_db_session() as session:
            try:
                results = []
                for op in operations:
                    if not op.conditions:
                        results.append(0)
                        continue

                    stmt = delete(op.model_class)
                    for field_name, value in op.conditions.items():
                        model_attr = getattr(op.model_class, field_name)
                        if isinstance(value, list | tuple | set):
                            stmt = stmt.where(model_attr.in_(value))
                        else:
                            stmt = stmt.where(model_attr == value)

                    result = await session.execute(stmt)
                    results.append(result.rowcount)

                await session.commit()
                return results

            except Exception as e:
                await session.rollback()
                logger.error(f"批量删除失败: {e}", exc_info=True)
                return [0] * len(operations)

    def _merge_select_conditions(self, operations: list[BatchOperation]) -> dict[tuple, list[BatchOperation]]:
        """合并相似的查询条件"""
        merged = {}

        for op in operations:
            # 生成条件键
            condition_key = tuple(sorted(op.conditions.keys()))

            if condition_key not in merged:
                merged[condition_key] = {}

            # 尝试合并相同字段的值
            for field_name, value in op.conditions.items():
                if field_name not in merged[condition_key]:
                    merged[condition_key][field_name] = []

                if isinstance(value, list | tuple | set):
                    merged[condition_key][field_name].extend(value)
                else:
                    merged[condition_key][field_name].append(value)

            # 记录操作
            if condition_key not in merged:
                merged[condition_key] = {"_operations": []}
            if "_operations" not in merged[condition_key]:
                merged[condition_key]["_operations"] = []
            merged[condition_key]["_operations"].append(op)

        # 去重并构建最终条件
        final_merged = {}
        for condition_key, conditions in merged.items():
            operations = conditions.pop("_operations")

            # 去重
            for field_name, values in conditions.items():
                conditions[field_name] = list(set(values))

            final_merged[condition_key] = operations

        return final_merged

    def get_stats(self) -> dict[str, Any]:
        """获取统计信息"""
        return {
            **self.stats,
            "cache_size": len(self._result_cache),
            "queue_sizes": {k: len(v) for k, v in self.operation_queues.items()},
            "is_running": self._is_running,
        }


# 全局数据库批量调度器实例
db_batch_scheduler = DatabaseBatchScheduler()


@asynccontextmanager
async def get_batch_session():
    """获取批量会话上下文管理器"""
    if not db_batch_scheduler._is_running:
        await db_batch_scheduler.start()

    try:
        yield db_batch_scheduler
    finally:
        pass


# 便捷函数
async def batch_select(model_class: Any, conditions: dict[str, Any]) -> Any:
    """批量查询"""
    operation = BatchOperation(operation_type="select", model_class=model_class, conditions=conditions)
    return await db_batch_scheduler.add_operation(operation)


async def batch_insert(model_class: Any, data: dict[str, Any]) -> int:
    """批量插入"""
    operation = BatchOperation(operation_type="insert", model_class=model_class, conditions={}, data=data)
    return await db_batch_scheduler.add_operation(operation)


async def batch_update(model_class: Any, conditions: dict[str, Any], data: dict[str, Any]) -> int:
    """批量更新"""
    operation = BatchOperation(operation_type="update", model_class=model_class, conditions=conditions, data=data)
    return await db_batch_scheduler.add_operation(operation)


async def batch_delete(model_class: Any, conditions: dict[str, Any]) -> int:
    """批量删除"""
    operation = BatchOperation(operation_type="delete", model_class=model_class, conditions=conditions)
    return await db_batch_scheduler.add_operation(operation)


def get_db_batch_scheduler() -> DatabaseBatchScheduler:
    """获取数据库批量调度器实例"""
    return db_batch_scheduler
