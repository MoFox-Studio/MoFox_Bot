"""高级查询API

提供复杂的查询操作：
- MongoDB风格的查询操作符
- 聚合查询
- 排序和分页
- 关联查询
"""

from typing import Any, Generic, TypeVar

from sqlalchemy import and_, asc, desc, func, or_, select

# 导入 CRUD 辅助函数以避免重复定义
from src.common.database.api.crud import _dict_to_model, _model_to_dict
from src.common.database.core.models import Base
from src.common.database.core.session import get_db_session
from src.common.database.optimization import get_cache
from src.common.logger import get_logger

logger = get_logger("database.query")

T = TypeVar("T", bound="Base")


class QueryBuilder(Generic[T]):
    """查询构建器

    支持链式调用，构建复杂查询
    """

    def __init__(self, model: type[T]):
        """初始化查询构建器

        Args:
            model: SQLAlchemy模型类
        """
        self.model = model
        self.model_name = model.__tablename__
        self._stmt = select(model)
        self._use_cache = True
        self._cache_key_parts: list[str] = [self.model_name]

    def filter(self, **conditions: Any) -> "QueryBuilder":
        """添加过滤条件

        支持的操作符：
        - 直接相等: field=value
        - 大于: field__gt=value
        - 小于: field__lt=value
        - 大于等于: field__gte=value
        - 小于等于: field__lte=value
        - 不等于: field__ne=value
        - 包含: field__in=[values]
        - 不包含: field__nin=[values]
        - 模糊匹配: field__like='%pattern%'
        - 为空: field__isnull=True

        Args:
            **conditions: 过滤条件

        Returns:
            self，支持链式调用
        """
        for key, value in conditions.items():
            # 解析字段和操作符
            if "__" in key:
                field_name, operator = key.rsplit("__", 1)
            else:
                field_name, operator = key, "eq"

            if not hasattr(self.model, field_name):
                logger.warning(f"模型 {self.model_name} 没有字段 {field_name}")
                continue

            field = getattr(self.model, field_name)

            # 应用操作符
            if operator == "eq":
                self._stmt = self._stmt.where(field == value)
            elif operator == "gt":
                self._stmt = self._stmt.where(field > value)
            elif operator == "lt":
                self._stmt = self._stmt.where(field < value)
            elif operator == "gte":
                self._stmt = self._stmt.where(field >= value)
            elif operator == "lte":
                self._stmt = self._stmt.where(field <= value)
            elif operator == "ne":
                self._stmt = self._stmt.where(field != value)
            elif operator == "in":
                self._stmt = self._stmt.where(field.in_(value))
            elif operator == "nin":
                self._stmt = self._stmt.where(~field.in_(value))
            elif operator == "like":
                self._stmt = self._stmt.where(field.like(value))
            elif operator == "isnull":
                if value:
                    self._stmt = self._stmt.where(field.is_(None))
                else:
                    self._stmt = self._stmt.where(field.isnot(None))
            else:
                logger.warning(f"未知操作符: {operator}")

        # 更新缓存键
        self._cache_key_parts.append(f"filter:{sorted(conditions.items())!s}")
        return self

    def filter_or(self, **conditions: Any) -> "QueryBuilder":
        """添加OR过滤条件

        Args:
            **conditions: OR条件

        Returns:
            self，支持链式调用
        """
        or_conditions = []
        for key, value in conditions.items():
            if hasattr(self.model, key):
                field = getattr(self.model, key)
                or_conditions.append(field == value)

        if or_conditions:
            self._stmt = self._stmt.where(or_(*or_conditions))
            self._cache_key_parts.append(f"or:{sorted(conditions.items())!s}")

        return self

    def order_by(self, *fields: str) -> "QueryBuilder":
        """添加排序

        Args:
            *fields: 排序字段，'-'前缀表示降序

        Returns:
            self，支持链式调用
        """
        for field_name in fields:
            if field_name.startswith("-"):
                field_name = field_name[1:]
                if hasattr(self.model, field_name):
                    self._stmt = self._stmt.order_by(desc(getattr(self.model, field_name)))
            else:
                if hasattr(self.model, field_name):
                    self._stmt = self._stmt.order_by(asc(getattr(self.model, field_name)))

        self._cache_key_parts.append(f"order:{','.join(fields)}")
        return self

    def limit(self, limit: int) -> "QueryBuilder":
        """限制结果数量

        Args:
            limit: 最大数量

        Returns:
            self，支持链式调用
        """
        self._stmt = self._stmt.limit(limit)
        self._cache_key_parts.append(f"limit:{limit}")
        return self

    def offset(self, offset: int) -> "QueryBuilder":
        """跳过指定数量

        Args:
            offset: 跳过数量

        Returns:
            self，支持链式调用
        """
        self._stmt = self._stmt.offset(offset)
        self._cache_key_parts.append(f"offset:{offset}")
        return self

    def no_cache(self) -> "QueryBuilder":
        """禁用缓存

        Returns:
            self，支持链式调用
        """
        self._use_cache = False
        return self

    async def all(self) -> list[T]:
        """获取所有结果

        Returns:
            模型实例列表
        """
        cache_key = ":".join(self._cache_key_parts) + ":all"

        # 尝试从缓存获取 (缓存的是字典列表)
        if self._use_cache:
            cache = await get_cache()
            cached_dicts = await cache.get(cache_key)
            if cached_dicts is not None:
                logger.debug(f"缓存命中: {cache_key}")
                # 从字典列表恢复对象列表
                return [_dict_to_model(self.model, d) for d in cached_dicts]

        # 从数据库查询
        async with get_db_session() as session:
            result = await session.execute(self._stmt)
            instances = list(result.scalars().all())

            # 预加载所有列以避免detached对象的lazy loading问题
            for instance in instances:
                for column in self.model.__table__.columns:
                    try:
                        getattr(instance, column.name)
                    except Exception:
                        pass

            # 转换为字典列表并写入缓存
            if self._use_cache:
                instances_dicts = [_model_to_dict(inst) for inst in instances]
                cache = await get_cache()
                await cache.set(cache_key, instances_dicts)

            return instances

    async def first(self) -> T | None:
        """获取第一个结果

        Returns:
            模型实例或None
        """
        cache_key = ":".join(self._cache_key_parts) + ":first"

        # 尝试从缓存获取 (缓存的是字典)
        if self._use_cache:
            cache = await get_cache()
            cached_dict = await cache.get(cache_key)
            if cached_dict is not None:
                logger.debug(f"缓存命中: {cache_key}")
                # 从字典恢复对象
                return _dict_to_model(self.model, cached_dict)

        # 从数据库查询
        async with get_db_session() as session:
            result = await session.execute(self._stmt)
            instance = result.scalars().first()

            # 预加载所有列以避免detached对象的lazy loading问题
            if instance is not None:
                for column in self.model.__table__.columns:
                    try:
                        getattr(instance, column.name)
                    except Exception:
                        pass

            # 转换为字典并写入缓存
            if instance is not None and self._use_cache:
                instance_dict = _model_to_dict(instance)
                cache = await get_cache()
                await cache.set(cache_key, instance_dict)

            return instance

    async def count(self) -> int:
        """统计数量

        Returns:
            记录数量
        """
        cache_key = ":".join(self._cache_key_parts) + ":count"

        # 尝试从缓存获取
        if self._use_cache:
            cache = await get_cache()
            cached = await cache.get(cache_key)
            if cached is not None:
                logger.debug(f"缓存命中: {cache_key}")
                return cached

        # 构建count查询
        count_stmt = select(func.count()).select_from(self._stmt.subquery())

        # 从数据库查询
        async with get_db_session() as session:
            result = await session.execute(count_stmt)
            count = result.scalar() or 0

            # 写入缓存
            if self._use_cache:
                cache = await get_cache()
                await cache.set(cache_key, count)

            return count

    async def exists(self) -> bool:
        """检查是否存在

        Returns:
            是否存在记录
        """
        count = await self.count()
        return count > 0

    async def paginate(
        self,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[T], int]:
        """分页查询

        Args:
            page: 页码（从1开始）
            page_size: 每页数量

        Returns:
            (结果列表, 总数量)
        """
        # 计算偏移量
        offset = (page - 1) * page_size

        # 获取总数
        total = await self.count()

        # 获取当前页数据
        self._stmt = self._stmt.offset(offset).limit(page_size)
        self._cache_key_parts.append(f"page:{page}:{page_size}")

        items = await self.all()

        return items, total


class AggregateQuery:
    """聚合查询

    提供聚合操作如sum、avg、max、min等
    """

    def __init__(self, model: type[T]):
        """初始化聚合查询

        Args:
            model: SQLAlchemy模型类
        """
        self.model = model
        self.model_name = model.__tablename__
        self._conditions = []

    def filter(self, **conditions: Any) -> "AggregateQuery":
        """添加过滤条件

        Args:
            **conditions: 过滤条件

        Returns:
            self，支持链式调用
        """
        for key, value in conditions.items():
            if hasattr(self.model, key):
                field = getattr(self.model, key)
                self._conditions.append(field == value)
        return self

    async def sum(self, field: str) -> float:
        """求和

        Args:
            field: 字段名

        Returns:
            总和
        """
        if not hasattr(self.model, field):
            raise ValueError(f"字段 {field} 不存在")

        async with get_db_session() as session:
            stmt = select(func.sum(getattr(self.model, field)))

            if self._conditions:
                stmt = stmt.where(and_(*self._conditions))

            result = await session.execute(stmt)
            return result.scalar() or 0

    async def avg(self, field: str) -> float:
        """求平均值

        Args:
            field: 字段名

        Returns:
            平均值
        """
        if not hasattr(self.model, field):
            raise ValueError(f"字段 {field} 不存在")

        async with get_db_session() as session:
            stmt = select(func.avg(getattr(self.model, field)))

            if self._conditions:
                stmt = stmt.where(and_(*self._conditions))

            result = await session.execute(stmt)
            return result.scalar() or 0

    async def max(self, field: str) -> Any:
        """求最大值

        Args:
            field: 字段名

        Returns:
            最大值
        """
        if not hasattr(self.model, field):
            raise ValueError(f"字段 {field} 不存在")

        async with get_db_session() as session:
            stmt = select(func.max(getattr(self.model, field)))

            if self._conditions:
                stmt = stmt.where(and_(*self._conditions))

            result = await session.execute(stmt)
            return result.scalar()

    async def min(self, field: str) -> Any:
        """求最小值

        Args:
            field: 字段名

        Returns:
            最小值
        """
        if not hasattr(self.model, field):
            raise ValueError(f"字段 {field} 不存在")

        async with get_db_session() as session:
            stmt = select(func.min(getattr(self.model, field)))

            if self._conditions:
                stmt = stmt.where(and_(*self._conditions))

            result = await session.execute(stmt)
            return result.scalar()

    async def group_by_count(
        self,
        *fields: str,
    ) -> list[tuple[Any, ...]]:
        """分组统计

        Args:
            *fields: 分组字段

        Returns:
            [(分组值1, 分组值2, ..., 数量), ...]
        """
        if not fields:
            raise ValueError("至少需要一个分组字段")

        group_columns = []
        for field_name in fields:
            if hasattr(self.model, field_name):
                group_columns.append(getattr(self.model, field_name))

        if not group_columns:
            return []

        async with get_db_session() as session:
            stmt = select(*group_columns, func.count(self.model.id))

            if self._conditions:
                stmt = stmt.where(and_(*self._conditions))

            stmt = stmt.group_by(*group_columns)

            result = await session.execute(stmt)
            return [tuple(row) for row in result.all()]
