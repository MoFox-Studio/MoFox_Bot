"""基础CRUD API

提供通用的数据库CRUD操作，集成优化层功能：
- 自动缓存：查询结果自动缓存
- 批量处理：写操作自动批处理
- 智能预加载：关联数据自动预加载
"""

from typing import Any, Optional, Type, TypeVar

from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.database.core.models import Base
from src.common.database.core.session import get_db_session
from src.common.database.optimization import (
    BatchOperation,
    Priority,
    get_batch_scheduler,
    get_cache,
    get_preloader,
)
from src.common.logger import get_logger

logger = get_logger("database.crud")

T = TypeVar("T", bound=Base)


class CRUDBase:
    """基础CRUD操作类
    
    提供通用的增删改查操作，自动集成缓存和批处理
    """

    def __init__(self, model: Type[T]):
        """初始化CRUD操作
        
        Args:
            model: SQLAlchemy模型类
        """
        self.model = model
        self.model_name = model.__tablename__

    async def get(
        self,
        id: int,
        use_cache: bool = True,
    ) -> Optional[T]:
        """根据ID获取单条记录
        
        Args:
            id: 记录ID
            use_cache: 是否使用缓存
            
        Returns:
            模型实例或None
        """
        cache_key = f"{self.model_name}:id:{id}"
        
        # 尝试从缓存获取
        if use_cache:
            cache = await get_cache()
            cached = await cache.get(cache_key)
            if cached is not None:
                logger.debug(f"缓存命中: {cache_key}")
                return cached
        
        # 从数据库查询
        async with get_db_session() as session:
            stmt = select(self.model).where(self.model.id == id)
            result = await session.execute(stmt)
            instance = result.scalar_one_or_none()
            
            # 写入缓存
            if instance is not None and use_cache:
                cache = await get_cache()
                await cache.set(cache_key, instance)
            
            return instance

    async def get_by(
        self,
        use_cache: bool = True,
        **filters: Any,
    ) -> Optional[T]:
        """根据条件获取单条记录
        
        Args:
            use_cache: 是否使用缓存
            **filters: 过滤条件
            
        Returns:
            模型实例或None
        """
        cache_key = f"{self.model_name}:filter:{str(sorted(filters.items()))}"
        
        # 尝试从缓存获取
        if use_cache:
            cache = await get_cache()
            cached = await cache.get(cache_key)
            if cached is not None:
                logger.debug(f"缓存命中: {cache_key}")
                return cached
        
        # 从数据库查询
        async with get_db_session() as session:
            stmt = select(self.model)
            for key, value in filters.items():
                if hasattr(self.model, key):
                    stmt = stmt.where(getattr(self.model, key) == value)
            
            result = await session.execute(stmt)
            instance = result.scalar_one_or_none()
            
            if instance is not None:
                # 触发所有列的加载，避免 detached 后的延迟加载问题
                # 遍历所有列属性以确保它们被加载到内存中
                for column in self.model.__table__.columns:
                    try:
                        getattr(instance, column.name)
                    except Exception:
                        pass  # 忽略访问错误
                
                # 写入缓存
                if use_cache:
                    cache = await get_cache()
                    await cache.set(cache_key, instance)
            
            return instance

    async def get_multi(
        self,
        skip: int = 0,
        limit: int = 100,
        use_cache: bool = True,
        **filters: Any,
    ) -> list[T]:
        """获取多条记录
        
        Args:
            skip: 跳过的记录数
            limit: 返回的最大记录数
            use_cache: 是否使用缓存
            **filters: 过滤条件
            
        Returns:
            模型实例列表
        """
        cache_key = f"{self.model_name}:multi:{skip}:{limit}:{str(sorted(filters.items()))}"
        
        # 尝试从缓存获取
        if use_cache:
            cache = await get_cache()
            cached = await cache.get(cache_key)
            if cached is not None:
                logger.debug(f"缓存命中: {cache_key}")
                return cached
        
        # 从数据库查询
        async with get_db_session() as session:
            stmt = select(self.model)
            
            # 应用过滤条件
            for key, value in filters.items():
                if hasattr(self.model, key):
                    if isinstance(value, (list, tuple, set)):
                        stmt = stmt.where(getattr(self.model, key).in_(value))
                    else:
                        stmt = stmt.where(getattr(self.model, key) == value)
            
            # 应用分页
            stmt = stmt.offset(skip).limit(limit)
            
            result = await session.execute(stmt)
            instances = result.scalars().all()
            
            # 触发所有实例的列加载，避免 detached 后的延迟加载问题
            for instance in instances:
                for column in self.model.__table__.columns:
                    try:
                        getattr(instance, column.name)
                    except Exception:
                        pass  # 忽略访问错误
            
            # 写入缓存
            if use_cache:
                cache = await get_cache()
                await cache.set(cache_key, instances)
            
            return instances

    async def create(
        self,
        obj_in: dict[str, Any],
        use_batch: bool = False,
    ) -> T:
        """创建新记录
        
        Args:
            obj_in: 创建数据
            use_batch: 是否使用批处理
            
        Returns:
            创建的模型实例
        """
        if use_batch:
            # 使用批处理
            scheduler = await get_batch_scheduler()
            operation = BatchOperation(
                operation_type="insert",
                model_class=self.model,
                data=obj_in,
                priority=Priority.NORMAL,
            )
            future = await scheduler.add_operation(operation)
            await future
            
            # 批处理返回成功，创建实例
            instance = self.model(**obj_in)
            return instance
        else:
            # 直接创建
            async with get_db_session() as session:
                instance = self.model(**obj_in)
                session.add(instance)
                await session.flush()
                await session.refresh(instance)
                return instance

    async def update(
        self,
        id: int,
        obj_in: dict[str, Any],
        use_batch: bool = False,
    ) -> Optional[T]:
        """更新记录
        
        Args:
            id: 记录ID
            obj_in: 更新数据
            use_batch: 是否使用批处理
            
        Returns:
            更新后的模型实例或None
        """
        # 先获取实例
        instance = await self.get(id, use_cache=False)
        if instance is None:
            return None
        
        if use_batch:
            # 使用批处理
            scheduler = await get_batch_scheduler()
            operation = BatchOperation(
                operation_type="update",
                model_class=self.model,
                conditions={"id": id},
                data=obj_in,
                priority=Priority.NORMAL,
            )
            future = await scheduler.add_operation(operation)
            await future
            
            # 更新实例属性
            for key, value in obj_in.items():
                if hasattr(instance, key):
                    setattr(instance, key, value)
        else:
            # 直接更新
            async with get_db_session() as session:
                # 重新加载实例到当前会话
                stmt = select(self.model).where(self.model.id == id)
                result = await session.execute(stmt)
                db_instance = result.scalar_one_or_none()
                
                if db_instance:
                    for key, value in obj_in.items():
                        if hasattr(db_instance, key):
                            setattr(db_instance, key, value)
                    await session.flush()
                    await session.refresh(db_instance)
                    instance = db_instance
        
        # 清除缓存
        cache_key = f"{self.model_name}:id:{id}"
        cache = await get_cache()
        await cache.delete(cache_key)
        
        return instance

    async def delete(
        self,
        id: int,
        use_batch: bool = False,
    ) -> bool:
        """删除记录
        
        Args:
            id: 记录ID
            use_batch: 是否使用批处理
            
        Returns:
            是否成功删除
        """
        if use_batch:
            # 使用批处理
            scheduler = await get_batch_scheduler()
            operation = BatchOperation(
                operation_type="delete",
                model_class=self.model,
                conditions={"id": id},
                priority=Priority.NORMAL,
            )
            future = await scheduler.add_operation(operation)
            result = await future
            success = result > 0
        else:
            # 直接删除
            async with get_db_session() as session:
                stmt = delete(self.model).where(self.model.id == id)
                result = await session.execute(stmt)
                success = result.rowcount > 0
        
        # 清除缓存
        if success:
            cache_key = f"{self.model_name}:id:{id}"
            cache = await get_cache()
            await cache.delete(cache_key)
        
        return success

    async def count(
        self,
        **filters: Any,
    ) -> int:
        """统计记录数
        
        Args:
            **filters: 过滤条件
            
        Returns:
            记录数量
        """
        async with get_db_session() as session:
            stmt = select(func.count(self.model.id))
            
            # 应用过滤条件
            for key, value in filters.items():
                if hasattr(self.model, key):
                    if isinstance(value, (list, tuple, set)):
                        stmt = stmt.where(getattr(self.model, key).in_(value))
                    else:
                        stmt = stmt.where(getattr(self.model, key) == value)
            
            result = await session.execute(stmt)
            return result.scalar()

    async def exists(
        self,
        **filters: Any,
    ) -> bool:
        """检查记录是否存在
        
        Args:
            **filters: 过滤条件
            
        Returns:
            是否存在
        """
        count = await self.count(**filters)
        return count > 0

    async def get_or_create(
        self,
        defaults: Optional[dict[str, Any]] = None,
        **filters: Any,
    ) -> tuple[T, bool]:
        """获取或创建记录
        
        Args:
            defaults: 创建时的默认值
            **filters: 查找条件
            
        Returns:
            (实例, 是否新创建)
        """
        # 先尝试获取
        instance = await self.get_by(use_cache=False, **filters)
        if instance is not None:
            return instance, False
        
        # 创建新记录
        create_data = {**filters}
        if defaults:
            create_data.update(defaults)
        
        instance = await self.create(create_data)
        return instance, True

    async def bulk_create(
        self,
        objs_in: list[dict[str, Any]],
    ) -> list[T]:
        """批量创建记录
        
        Args:
            objs_in: 创建数据列表
            
        Returns:
            创建的模型实例列表
        """
        async with get_db_session() as session:
            instances = [self.model(**obj_data) for obj_data in objs_in]
            session.add_all(instances)
            await session.flush()
            
            for instance in instances:
                await session.refresh(instance)
            
            return instances

    async def bulk_update(
        self,
        updates: list[tuple[int, dict[str, Any]]],
    ) -> int:
        """批量更新记录
        
        Args:
            updates: (id, update_data)元组列表
            
        Returns:
            更新的记录数
        """
        async with get_db_session() as session:
            count = 0
            for id, obj_in in updates:
                stmt = (
                    update(self.model)
                    .where(self.model.id == id)
                    .values(**obj_in)
                )
                result = await session.execute(stmt)
                count += result.rowcount
                
                # 清除缓存
                cache_key = f"{self.model_name}:id:{id}"
                cache = await get_cache()
                await cache.delete(cache_key)
            
            return count
