# 数据库模块重构方案

## 📋 目录
1. [重构目标](#重构目标)
2. [对外API保持兼容](#对外api保持兼容)
3. [新架构设计](#新架构设计)
4. [高频读写优化](#高频读写优化)
5. [实施计划](#实施计划)
6. [风险评估与回滚方案](#风险评估与回滚方案)

---

## 🎯 重构目标

### 核心目标
1. **架构清晰化** - 消除职责重叠，明确模块边界
2. **性能优化** - 针对高频读写场景进行深度优化
3. **向后兼容** - 保持所有对外API接口不变
4. **可维护性** - 提高代码质量和可测试性

### 关键指标
- ✅ 零破坏性变更
- ✅ 高频读取性能提升 50%+
- ✅ 写入批量化率提升至 80%+
- ✅ 连接池利用率 > 90%

---

## 🔒 对外API保持兼容

### 识别的关键API接口

#### 1. 数据库会话管理
```python
# ✅ 必须保持
from src.common.database.sqlalchemy_models import get_db_session

async with get_db_session() as session:
    # 使用session
```

#### 2. 数据操作API
```python
# ✅ 必须保持
from src.common.database.sqlalchemy_database_api import (
    db_query,    # 通用查询
    db_save,     # 保存/更新
    db_get,      # 快捷查询
    store_action_info,  # 存储动作
)
```

#### 3. 模型导入
```python
# ✅ 必须保持
from src.common.database.sqlalchemy_models import (
    ChatStreams,
    Messages,
    PersonInfo,
    LLMUsage,
    Emoji,
    Images,
    # ... 所有30+模型
)
```

#### 4. 初始化接口
```python
# ✅ 必须保持
from src.common.database.database import (
    db,
    initialize_sql_database,
    stop_database,
)
```

#### 5. 模型映射
```python
# ✅ 必须保持
from src.common.database.sqlalchemy_database_api import MODEL_MAPPING
```

### 兼容性策略
所有现有导入路径将通过 `__init__.py` 重新导出，确保零破坏性变更。

---

## 🏗️ 新架构设计

### 当前架构问题
```
❌ 当前结构 - 职责混乱
database/
├── database.py              (入口+初始化+代理)
├── sqlalchemy_init.py       (重复的初始化逻辑)
├── sqlalchemy_models.py     (模型+引擎+会话+初始化)
├── sqlalchemy_database_api.py
├── connection_pool_manager.py
├── db_batch_scheduler.py
└── db_migration.py
```

### 新架构设计
```
✅ 新结构 - 职责清晰
database/
├── __init__.py                    【统一入口】导出所有API
│
├── core/                          【核心层】
│   ├── __init__.py
│   ├── engine.py                  数据库引擎管理（单一职责）
│   ├── session.py                 会话管理（单一职责）
│   ├── models.py                  模型定义（纯模型）
│   └── migration.py               迁移工具
│
├── api/                           【API层】
│   ├── __init__.py
│   ├── crud.py                    CRUD操作（db_query/save/get）
│   ├── specialized.py             特殊操作（store_action_info等）
│   └── query_builder.py           查询构建器
│
├── optimization/                  【优化层】
│   ├── __init__.py
│   ├── connection_pool.py         连接池管理
│   ├── batch_scheduler.py         批量调度
│   ├── cache_manager.py           智能缓存
│   ├── read_write_splitter.py     读写分离
│   └── preloader.py               预加载器
│
├── config/                        【配置层】
│   ├── __init__.py
│   ├── database_config.py         数据库配置
│   └── optimization_config.py     优化配置
│
└── utils/                         【工具层】
    ├── __init__.py
    ├── exceptions.py              统一异常
    ├── decorators.py              装饰器（缓存、重试等）
    └── monitoring.py              性能监控
```

### 职责划分

#### Core 层（核心层）
| 文件 | 职责 | 依赖 |
|------|------|------|
| `engine.py` | 创建和管理数据库引擎，单例模式 | config |
| `session.py` | 提供会话工厂和上下文管理器 | engine, optimization |
| `models.py` | 定义所有SQLAlchemy模型 | engine |
| `migration.py` | 数据库结构自动迁移 | engine, models |

#### API 层（接口层）
| 文件 | 职责 | 依赖 |
|------|------|------|
| `crud.py` | 实现db_query/db_save/db_get | session, models |
| `specialized.py` | 特殊业务操作 | crud |
| `query_builder.py` | 构建复杂查询条件 | - |

#### Optimization 层（优化层）
| 文件 | 职责 | 依赖 |
|------|------|------|
| `connection_pool.py` | 透明连接复用 | session |
| `batch_scheduler.py` | 批量操作调度 | session |
| `cache_manager.py` | 多级缓存管理 | - |
| `read_write_splitter.py` | 读写分离路由 | engine |
| `preloader.py` | 数据预加载 | cache_manager |

---

## ⚡ 高频读写优化

### 问题分析

通过代码分析，识别出以下高频操作场景：

#### 高频读取场景
1. **ChatStreams 查询** - 每条消息都要查询聊天流
2. **Messages 历史查询** - 构建上下文时频繁查询
3. **PersonInfo 查询** - 每次交互都要查用户信息
4. **Emoji/Images 查询** - 发送表情时查询
5. **UserRelationships 查询** - 关系系统频繁读取

#### 高频写入场景
1. **Messages 插入** - 每条消息都要写入
2. **LLMUsage 插入** - 每次LLM调用都记录
3. **ActionRecords 插入** - 每个动作都记录
4. **ChatStreams 更新** - 更新活跃时间和状态

### 优化策略设计

#### 1️⃣ 多级缓存系统

```python
# optimization/cache_manager.py

from typing import Any, Optional, Callable
from dataclasses import dataclass
from datetime import timedelta
import asyncio
from collections import OrderedDict

@dataclass
class CacheConfig:
    """缓存配置"""
    l1_size: int = 1000           # L1缓存大小（内存LRU）
    l1_ttl: float = 60.0          # L1 TTL（秒）
    l2_size: int = 10000          # L2缓存大小（内存LRU）
    l2_ttl: float = 300.0         # L2 TTL（秒）
    enable_write_through: bool = True   # 写穿透
    enable_write_back: bool = False     # 写回（风险较高）


class MultiLevelCache:
    """多级缓存管理器
    
    L1: 热数据缓存（1000条，60秒）- 极高频访问
    L2: 温数据缓存（10000条，300秒）- 高频访问
    L3: 数据库
    
    策略：
    - 读取：L1 → L2 → DB，回填到上层
    - 写入：写穿透（同步更新所有层）
    - 失效：TTL + LRU
    """
    
    def __init__(self, config: CacheConfig):
        self.config = config
        self.l1_cache: OrderedDict = OrderedDict()
        self.l2_cache: OrderedDict = OrderedDict()
        self.l1_timestamps: dict = {}
        self.l2_timestamps: dict = {}
        self.stats = {
            "l1_hits": 0,
            "l2_hits": 0,
            "db_hits": 0,
            "writes": 0,
        }
        self._lock = asyncio.Lock()
    
    async def get(
        self, 
        key: str, 
        fetch_func: Callable,
        ttl_override: Optional[float] = None
    ) -> Any:
        """获取数据，自动回填"""
        # L1 查找
        if key in self.l1_cache:
            if self._is_valid(key, self.l1_timestamps, self.config.l1_ttl):
                self.stats["l1_hits"] += 1
                # LRU更新
                self.l1_cache.move_to_end(key)
                return self.l1_cache[key]
        
        # L2 查找
        if key in self.l2_cache:
            if self._is_valid(key, self.l2_timestamps, self.config.l2_ttl):
                self.stats["l2_hits"] += 1
                value = self.l2_cache[key]
                # 回填到L1
                await self._set_l1(key, value)
                return value
        
        # 从数据库获取
        self.stats["db_hits"] += 1
        value = await fetch_func()
        
        # 回填到L2和L1
        await self._set_l2(key, value)
        await self._set_l1(key, value)
        
        return value
    
    async def set(self, key: str, value: Any):
        """写入数据（写穿透）"""
        async with self._lock:
            self.stats["writes"] += 1
            await self._set_l1(key, value)
            await self._set_l2(key, value)
    
    async def invalidate(self, key: str):
        """失效指定key"""
        async with self._lock:
            self.l1_cache.pop(key, None)
            self.l2_cache.pop(key, None)
            self.l1_timestamps.pop(key, None)
            self.l2_timestamps.pop(key, None)
    
    async def invalidate_pattern(self, pattern: str):
        """失效匹配模式的key"""
        import re
        regex = re.compile(pattern)
        
        async with self._lock:
            for key in list(self.l1_cache.keys()):
                if regex.match(key):
                    del self.l1_cache[key]
                    self.l1_timestamps.pop(key, None)
            
            for key in list(self.l2_cache.keys()):
                if regex.match(key):
                    del self.l2_cache[key]
                    self.l2_timestamps.pop(key, None)
    
    def _is_valid(self, key: str, timestamps: dict, ttl: float) -> bool:
        """检查缓存是否有效"""
        import time
        if key not in timestamps:
            return False
        return time.time() - timestamps[key] < ttl
    
    async def _set_l1(self, key: str, value: Any):
        """设置L1缓存"""
        import time
        if len(self.l1_cache) >= self.config.l1_size:
            # LRU淘汰
            oldest = next(iter(self.l1_cache))
            del self.l1_cache[oldest]
            self.l1_timestamps.pop(oldest, None)
        
        self.l1_cache[key] = value
        self.l1_timestamps[key] = time.time()
    
    async def _set_l2(self, key: str, value: Any):
        """设置L2缓存"""
        import time
        if len(self.l2_cache) >= self.config.l2_size:
            # LRU淘汰
            oldest = next(iter(self.l2_cache))
            del self.l2_cache[oldest]
            self.l2_timestamps.pop(oldest, None)
        
        self.l2_cache[key] = value
        self.l2_timestamps[key] = time.time()
    
    def get_stats(self) -> dict:
        """获取缓存统计"""
        total_hits = self.stats["l1_hits"] + self.stats["l2_hits"] + self.stats["db_hits"]
        if total_hits == 0:
            hit_rate = 0
        else:
            hit_rate = (self.stats["l1_hits"] + self.stats["l2_hits"]) / total_hits * 100
        
        return {
            **self.stats,
            "l1_size": len(self.l1_cache),
            "l2_size": len(self.l2_cache),
            "hit_rate": f"{hit_rate:.2f}%",
            "total_requests": total_hits,
        }


# 全局缓存实例
_cache_manager: Optional[MultiLevelCache] = None


def get_cache_manager() -> MultiLevelCache:
    """获取全局缓存管理器"""
    global _cache_manager
    if _cache_manager is None:
        _cache_manager = MultiLevelCache(CacheConfig())
    return _cache_manager
```

#### 2️⃣ 智能预加载器

```python
# optimization/preloader.py

import asyncio
from typing import List, Dict, Any
from collections import defaultdict

class DataPreloader:
    """数据预加载器
    
    策略：
    1. 会话启动时预加载该聊天流的最近消息
    2. 定期预加载热门用户的PersonInfo
    3. 预加载常用表情和图片
    """
    
    def __init__(self):
        self.preload_tasks: Dict[str, asyncio.Task] = {}
        self.access_patterns = defaultdict(int)  # 访问模式统计
    
    async def preload_chat_stream_context(
        self, 
        stream_id: str,
        message_limit: int = 50
    ):
        """预加载聊天流上下文"""
        from ..api.crud import db_get
        from ..core.models import Messages, ChatStreams, PersonInfo
        from .cache_manager import get_cache_manager
        
        cache = get_cache_manager()
        
        # 1. 预加载ChatStream
        stream_key = f"chat_stream:{stream_id}"
        if stream_key not in cache.l1_cache:
            stream = await db_get(
                ChatStreams,
                filters={"stream_id": stream_id},
                single_result=True
            )
            if stream:
                await cache.set(stream_key, stream)
        
        # 2. 预加载最近消息
        messages = await db_get(
            Messages,
            filters={"chat_id": stream_id},
            order_by="-time",
            limit=message_limit
        )
        
        # 批量缓存消息
        for msg in messages:
            msg_key = f"message:{msg['message_id']}"
            await cache.set(msg_key, msg)
        
        # 3. 预加载相关用户信息
        user_ids = set()
        for msg in messages:
            if msg.get("user_id"):
                user_ids.add(msg["user_id"])
        
        # 批量查询用户信息
        if user_ids:
            users = await db_get(
                PersonInfo,
                filters={"user_id": {"$in": list(user_ids)}}
            )
            for user in users:
                user_key = f"person_info:{user['user_id']}"
                await cache.set(user_key, user)
    
    async def preload_hot_emojis(self, limit: int = 100):
        """预加载热门表情"""
        from ..api.crud import db_get
        from ..core.models import Emoji
        from .cache_manager import get_cache_manager
        
        cache = get_cache_manager()
        
        # 按使用次数排序
        hot_emojis = await db_get(
            Emoji,
            order_by="-usage_count",
            limit=limit
        )
        
        for emoji in hot_emojis:
            emoji_key = f"emoji:{emoji['emoji_hash']}"
            await cache.set(emoji_key, emoji)
    
    async def schedule_preload_task(
        self,
        task_name: str,
        coro,
        interval: float = 300.0  # 5分钟
    ):
        """定期执行预加载任务"""
        async def _task():
            while True:
                try:
                    await coro
                    await asyncio.sleep(interval)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"预加载任务 {task_name} 失败: {e}")
                    await asyncio.sleep(interval)
        
        task = asyncio.create_task(_task())
        self.preload_tasks[task_name] = task
    
    async def stop_all_tasks(self):
        """停止所有预加载任务"""
        for task in self.preload_tasks.values():
            task.cancel()
        
        await asyncio.gather(*self.preload_tasks.values(), return_exceptions=True)
        self.preload_tasks.clear()


# 全局预加载器
_preloader: Optional[DataPreloader] = None


def get_preloader() -> DataPreloader:
    """获取全局预加载器"""
    global _preloader
    if _preloader is None:
        _preloader = DataPreloader()
    return _preloader
```

#### 3️⃣ 增强批量调度器

```python
# optimization/batch_scheduler.py

from typing import List, Dict, Any, Callable
from dataclasses import dataclass
import asyncio
import time

@dataclass
class SmartBatchConfig:
    """智能批量配置"""
    # 基础配置
    batch_size: int = 100              # 增加批量大小
    max_wait_time: float = 0.05        # 减少等待时间（50ms）
    
    # 智能调整
    enable_adaptive: bool = True       # 启用自适应批量大小
    min_batch_size: int = 10
    max_batch_size: int = 500
    
    # 优先级配置
    high_priority_models: List[str] = None  # 高优先级模型
    
    # 自动降级
    enable_auto_degradation: bool = True
    degradation_threshold: float = 1.0  # 超过1秒降级为直接写入


class EnhancedBatchScheduler:
    """增强的批量调度器
    
    改进：
    1. 自适应批量大小
    2. 优先级队列
    3. 自动降级保护
    4. 写入确认机制
    """
    
    def __init__(self, config: SmartBatchConfig):
        self.config = config
        self.queues: Dict[str, asyncio.Queue] = {}
        self.pending_operations: Dict[str, List] = {}
        self.scheduler_tasks: Dict[str, asyncio.Task] = {}
        
        # 性能监控
        self.performance_stats = {
            "avg_batch_size": 0,
            "avg_latency": 0,
            "total_batches": 0,
        }
        
        self._lock = asyncio.Lock()
        self._running = False
    
    async def schedule_write(
        self,
        model_class: Any,
        operation_type: str,  # 'insert', 'update', 'delete'
        data: Dict[str, Any],
        priority: int = 0,  # 0=normal, 1=high, -1=low
    ) -> asyncio.Future:
        """调度写入操作
        
        Returns:
            Future对象，可await等待操作完成
        """
        queue_key = f"{model_class.__name__}_{operation_type}"
        
        # 确保队列存在
        if queue_key not in self.queues:
            async with self._lock:
                if queue_key not in self.queues:
                    self.queues[queue_key] = asyncio.Queue()
                    self.pending_operations[queue_key] = []
                    # 启动调度器
                    task = asyncio.create_task(
                        self._scheduler_loop(queue_key, model_class, operation_type)
                    )
                    self.scheduler_tasks[queue_key] = task
        
        # 创建Future
        future = asyncio.get_event_loop().create_future()
        
        # 加入队列
        operation = {
            "data": data,
            "priority": priority,
            "future": future,
            "timestamp": time.time(),
        }
        
        await self.queues[queue_key].put(operation)
        
        return future
    
    async def _scheduler_loop(
        self,
        queue_key: str,
        model_class: Any,
        operation_type: str
    ):
        """调度器主循环"""
        while self._running:
            try:
                # 收集一批操作
                batch = []
                deadline = time.time() + self.config.max_wait_time
                
                while len(batch) < self.config.batch_size:
                    timeout = deadline - time.time()
                    if timeout <= 0:
                        break
                    
                    try:
                        operation = await asyncio.wait_for(
                            self.queues[queue_key].get(),
                            timeout=timeout
                        )
                        batch.append(operation)
                    except asyncio.TimeoutError:
                        break
                
                if batch:
                    # 执行批量操作
                    await self._execute_batch(
                        model_class,
                        operation_type,
                        batch
                    )
            
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"批量调度器错误 [{queue_key}]: {e}")
                await asyncio.sleep(0.1)
    
    async def _execute_batch(
        self,
        model_class: Any,
        operation_type: str,
        batch: List[Dict]
    ):
        """执行批量操作"""
        start_time = time.time()
        
        try:
            from ..core.session import get_db_session
            from sqlalchemy import insert, update, delete
            
            async with get_db_session() as session:
                if operation_type == "insert":
                    # 批量插入
                    data_list = [op["data"] for op in batch]
                    stmt = insert(model_class).values(data_list)
                    await session.execute(stmt)
                    await session.commit()
                    
                    # 标记所有Future为成功
                    for op in batch:
                        if not op["future"].done():
                            op["future"].set_result(True)
                
                elif operation_type == "update":
                    # 批量更新
                    for op in batch:
                        stmt = update(model_class)
                        # 根据data中的条件更新
                        # ... 实现细节
                        await session.execute(stmt)
                    
                    await session.commit()
                    
                    for op in batch:
                        if not op["future"].done():
                            op["future"].set_result(True)
                
                # 更新性能统计
                latency = time.time() - start_time
                self._update_stats(len(batch), latency)
        
        except Exception as e:
            # 标记所有Future为失败
            for op in batch:
                if not op["future"].done():
                    op["future"].set_exception(e)
            
            logger.error(f"批量操作失败: {e}")
    
    def _update_stats(self, batch_size: int, latency: float):
        """更新性能统计"""
        n = self.performance_stats["total_batches"]
        
        # 移动平均
        self.performance_stats["avg_batch_size"] = (
            (self.performance_stats["avg_batch_size"] * n + batch_size) / (n + 1)
        )
        self.performance_stats["avg_latency"] = (
            (self.performance_stats["avg_latency"] * n + latency) / (n + 1)
        )
        self.performance_stats["total_batches"] = n + 1
        
        # 自适应调整批量大小
        if self.config.enable_adaptive:
            if latency > 0.5:  # 太慢，减小批量
                self.config.batch_size = max(
                    self.config.min_batch_size,
                    int(self.config.batch_size * 0.8)
                )
            elif latency < 0.1:  # 很快，增大批量
                self.config.batch_size = min(
                    self.config.max_batch_size,
                    int(self.config.batch_size * 1.2)
                )
    
    async def start(self):
        """启动调度器"""
        self._running = True
    
    async def stop(self):
        """停止调度器"""
        self._running = False
        
        # 取消所有任务
        for task in self.scheduler_tasks.values():
            task.cancel()
        
        await asyncio.gather(
            *self.scheduler_tasks.values(),
            return_exceptions=True
        )
        
        self.scheduler_tasks.clear()
```

#### 4️⃣ 装饰器工具

```python
# utils/decorators.py

from functools import wraps
from typing import Callable, Optional
import asyncio
import time

def cached(
    key_func: Callable = None,
    ttl: float = 60.0,
    cache_none: bool = False
):
    """缓存装饰器
    
    Args:
        key_func: 生成缓存键的函数
        ttl: 缓存时间
        cache_none: 是否缓存None值
    
    Example:
        @cached(key_func=lambda stream_id: f"stream:{stream_id}", ttl=300)
        async def get_chat_stream(stream_id: str):
            # ...
    """
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            from ..optimization.cache_manager import get_cache_manager
            
            cache = get_cache_manager()
            
            # 生成缓存键
            if key_func:
                cache_key = key_func(*args, **kwargs)
            else:
                # 默认键：函数名+参数
                cache_key = f"{func.__name__}:{args}:{kwargs}"
            
            # 尝试从缓存获取
            async def fetch():
                return await func(*args, **kwargs)
            
            result = await cache.get(cache_key, fetch, ttl_override=ttl)
            
            # 检查是否缓存None
            if result is None and not cache_none:
                result = await func(*args, **kwargs)
            
            return result
        
        return wrapper
    return decorator


def batch_write(
    model_class,
    operation_type: str = "insert",
    priority: int = 0
):
    """批量写入装饰器
    
    自动将写入操作加入批量调度器
    
    Example:
        @batch_write(Messages, operation_type="insert")
        async def save_message(data: dict):
            return data
    """
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            from ..optimization.batch_scheduler import get_batch_scheduler
            
            # 执行原函数获取数据
            data = await func(*args, **kwargs)
            
            # 加入批量调度器
            scheduler = get_batch_scheduler()
            future = await scheduler.schedule_write(
                model_class,
                operation_type,
                data,
                priority
            )
            
            # 等待完成
            result = await future
            return result
        
        return wrapper
    return decorator


def retry(
    max_attempts: int = 3,
    delay: float = 0.5,
    backoff: float = 2.0,
    exceptions: tuple = (Exception,)
):
    """重试装饰器
    
    Args:
        max_attempts: 最大重试次数
        delay: 初始延迟
        backoff: 延迟倍数
        exceptions: 需要重试的异常类型
    """
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            current_delay = delay
            
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    if attempt == max_attempts - 1:
                        raise
                    
                    logger.warning(
                        f"函数 {func.__name__} 第 {attempt + 1} 次尝试失败: {e}，"
                        f"{current_delay}秒后重试"
                    )
                    await asyncio.sleep(current_delay)
                    current_delay *= backoff
        
        return wrapper
    return decorator


def monitor_performance(func: Callable):
    """性能监控装饰器"""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        start_time = time.time()
        
        try:
            result = await func(*args, **kwargs)
            return result
        finally:
            elapsed = time.time() - start_time
            
            # 记录性能数据
            from ..utils.monitoring import record_metric
            record_metric(
                func.__name__,
                "execution_time",
                elapsed
            )
            
            # 慢查询警告
            if elapsed > 1.0:
                logger.warning(
                    f"慢操作检测: {func.__name__} 耗时 {elapsed:.2f}秒"
                )
    
    return wrapper
```

#### 5️⃣ 高频API优化版本

```python
# api/optimized_crud.py

from typing import Optional, List, Dict, Any
from ..utils.decorators import cached, batch_write, monitor_performance
from ..core.models import ChatStreams, Messages, PersonInfo, Emoji

class OptimizedCRUD:
    """优化的CRUD操作
    
    针对高频场景提供优化版本的API
    """
    
    @staticmethod
    @cached(
        key_func=lambda stream_id: f"chat_stream:{stream_id}",
        ttl=300.0
    )
    @monitor_performance
    async def get_chat_stream(stream_id: str) -> Optional[Dict]:
        """获取聊天流（高频优化）"""
        from .crud import db_get
        return await db_get(
            ChatStreams,
            filters={"stream_id": stream_id},
            single_result=True
        )
    
    @staticmethod
    @cached(
        key_func=lambda user_id: f"person_info:{user_id}",
        ttl=600.0  # 10分钟
    )
    @monitor_performance
    async def get_person_info(user_id: str) -> Optional[Dict]:
        """获取用户信息（高频优化）"""
        from .crud import db_get
        return await db_get(
            PersonInfo,
            filters={"user_id": user_id},
            single_result=True
        )
    
    @staticmethod
    @cached(
        key_func=lambda chat_id, limit: f"messages:{chat_id}:{limit}",
        ttl=120.0  # 2分钟
    )
    @monitor_performance
    async def get_recent_messages(
        chat_id: str,
        limit: int = 50
    ) -> List[Dict]:
        """获取最近消息（高频优化）"""
        from .crud import db_get
        return await db_get(
            Messages,
            filters={"chat_id": chat_id},
            order_by="-time",
            limit=limit
        )
    
    @staticmethod
    @batch_write(Messages, operation_type="insert", priority=1)
    @monitor_performance
    async def save_message(data: Dict) -> Dict:
        """保存消息（高频优化，批量写入）"""
        return data
    
    @staticmethod
    @cached(
        key_func=lambda emoji_hash: f"emoji:{emoji_hash}",
        ttl=3600.0  # 1小时
    )
    @monitor_performance
    async def get_emoji(emoji_hash: str) -> Optional[Dict]:
        """获取表情（高频优化）"""
        from .crud import db_get
        return await db_get(
            Emoji,
            filters={"emoji_hash": emoji_hash},
            single_result=True
        )
    
    @staticmethod
    async def update_chat_stream_active_time(
        stream_id: str,
        active_time: float
    ):
        """更新聊天流活跃时间（高频优化，异步批量）"""
        from ..optimization.batch_scheduler import get_batch_scheduler
        from ..optimization.cache_manager import get_cache_manager
        
        scheduler = get_batch_scheduler()
        
        # 加入批量更新
        await scheduler.schedule_write(
            ChatStreams,
            "update",
            {
                "stream_id": stream_id,
                "last_active_time": active_time
            },
            priority=0  # 低优先级
        )
        
        # 失效缓存
        cache = get_cache_manager()
        await cache.invalidate(f"chat_stream:{stream_id}")
```

---

## 📅 实施计划

### 阶段一：准备阶段（1-2天）

#### 任务清单
- [x] 完成需求分析和架构设计
- [ ] 创建新目录结构
- [ ] 编写测试用例（覆盖所有API）
- [ ] 设置性能基准测试

### 阶段二：核心层重构（2-3天）

#### 任务清单
- [ ] 创建 `core/engine.py` - 迁移引擎管理逻辑
- [ ] 创建 `core/session.py` - 迁移会话管理逻辑
- [ ] 创建 `core/models.py` - 迁移并统一所有模型定义
- [ ] 更新所有模型到 SQLAlchemy 2.0 类型注解
- [ ] 创建 `core/migration.py` - 迁移工具
- [ ] 运行测试，确保核心功能正常

### 阶段三：优化层实现（3-4天）

#### 任务清单
- [ ] 实现 `optimization/cache_manager.py` - 多级缓存
- [ ] 实现 `optimization/preloader.py` - 智能预加载
- [ ] 增强 `optimization/batch_scheduler.py` - 智能批量调度
- [ ] 实现 `optimization/connection_pool.py` - 优化连接池
- [ ] 添加性能监控和统计

### 阶段四：API层重构（2-3天）

#### 任务清单
- [ ] 创建 `api/crud.py` - 重构 CRUD 操作
- [ ] 创建 `api/optimized_crud.py` - 高频优化API
- [ ] 创建 `api/specialized.py` - 特殊业务操作
- [ ] 创建 `api/query_builder.py` - 查询构建器
- [ ] 实现向后兼容的API包装

### 阶段五：工具层完善（1-2天）

#### 任务清单
- [ ] 创建 `utils/exceptions.py` - 统一异常体系
- [ ] 创建 `utils/decorators.py` - 装饰器工具
- [ ] 创建 `utils/monitoring.py` - 性能监控
- [ ] 添加日志增强

### 阶段六：兼容层和迁移（2-3天）

#### 任务清单
- [ ] 完善 `__init__.py` - 导出所有API
- [ ] 创建兼容性适配器（如果需要）
- [ ] 逐步迁移现有代码使用新API
- [ ] 添加弃用警告（对于将来要移除的API）

### 阶段七：测试和优化（2-3天）

#### 任务清单
- [ ] 运行完整测试套件
- [ ] 性能基准测试对比
- [ ] 压力测试
- [ ] 修复发现的问题
- [ ] 性能调优

### 阶段八：文档和清理（1-2天）

#### 任务清单
- [ ] 编写使用文档
- [ ] 更新API文档
- [ ] 删除旧文件（如 .bak）
- [ ] 代码审查
- [ ] 准备发布

### 总时间估计：14-22天

---

## 🔧 具体实施步骤

### 步骤1：创建新目录结构

```bash
cd src/common/database

# 创建新目录
mkdir -p core api optimization config utils

# 创建__init__.py
touch core/__init__.py
touch api/__init__.py
touch optimization/__init__.py
touch config/__init__.py
touch utils/__init__.py
```

### 步骤2：实现核心层

#### core/engine.py
```python
"""数据库引擎管理
单一职责：创建和管理SQLAlchemy引擎
"""

from typing import Optional
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from ..config.database_config import get_database_config
from ..utils.exceptions import DatabaseInitializationError

_engine: Optional[AsyncEngine] = None
_engine_lock = None


async def get_engine() -> AsyncEngine:
    """获取全局数据库引擎（单例）"""
    global _engine, _engine_lock
    
    if _engine is not None:
        return _engine
    
    # 延迟导入避免循环依赖
    import asyncio
    if _engine_lock is None:
        _engine_lock = asyncio.Lock()
    
    async with _engine_lock:
        # 双重检查
        if _engine is not None:
            return _engine
        
        try:
            config = get_database_config()
            _engine = create_async_engine(
                config.url,
                **config.engine_kwargs
            )
            
            # SQLite优化
            if config.db_type == "sqlite":
                await _enable_sqlite_optimizations(_engine)
            
            logger.info(f"数据库引擎初始化成功: {config.db_type}")
            return _engine
        
        except Exception as e:
            raise DatabaseInitializationError(f"引擎初始化失败: {e}") from e


async def close_engine():
    """关闭数据库引擎"""
    global _engine
    
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        logger.info("数据库引擎已关闭")


async def _enable_sqlite_optimizations(engine: AsyncEngine):
    """启用SQLite性能优化"""
    from sqlalchemy import text
    
    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA journal_mode = WAL"))
        await conn.execute(text("PRAGMA synchronous = NORMAL"))
        await conn.execute(text("PRAGMA foreign_keys = ON"))
        await conn.execute(text("PRAGMA busy_timeout = 60000"))
    
    logger.info("SQLite性能优化已启用")
```

#### core/session.py
```python
"""会话管理
单一职责：提供数据库会话上下文管理器
"""

from contextlib import asynccontextmanager
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from .engine import get_engine

_session_factory: Optional[async_sessionmaker] = None


async def get_session_factory() -> async_sessionmaker:
    """获取会话工厂"""
    global _session_factory
    
    if _session_factory is None:
        engine = await get_engine()
        _session_factory = async_sessionmaker(
            bind=engine,
            class_=AsyncSession,
            expire_on_commit=False
        )
    
    return _session_factory


@asynccontextmanager
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    获取数据库会话上下文管理器
    
    使用连接池优化，透明复用连接
    
    Example:
        async with get_db_session() as session:
            result = await session.execute(select(User))
    """
    from ..optimization.connection_pool import get_connection_pool_manager
    
    session_factory = await get_session_factory()
    pool_manager = get_connection_pool_manager()
    
    async with pool_manager.get_session(session_factory) as session:
        # SQLite特定配置
        from ..config.database_config import get_database_config
        config = get_database_config()
        
        if config.db_type == "sqlite":
            from sqlalchemy import text
            try:
                await session.execute(text("PRAGMA busy_timeout = 60000"))
                await session.execute(text("PRAGMA foreign_keys = ON"))
            except Exception:
                pass  # 复用连接时可能已设置
        
        yield session
```

### 步骤3：完善 `__init__.py` 保持兼容

```python
# src/common/database/__init__.py

"""
数据库模块统一入口

导出所有对外API，确保向后兼容
"""

# === 核心层导出 ===
from .core.engine import get_engine, close_engine
from .core.session import get_db_session
from .core.models import (
    Base,
    ChatStreams,
    Messages,
    ActionRecords,
    PersonInfo,
    LLMUsage,
    Emoji,
    Images,
    Videos,
    OnlineTime,
    Memory,
    Expression,
    ThinkingLog,
    GraphNodes,
    GraphEdges,
    Schedule,
    MonthlyPlan,
    BanUser,
    PermissionNodes,
    UserPermissions,
    UserRelationships,
    ImageDescriptions,
    CacheEntries,
    MaiZoneScheduleStatus,
    AntiInjectionStats,
    # ... 所有模型
)

# === API层导出 ===
from .api.crud import (
    db_query,
    db_save,
    db_get,
)
from .api.specialized import (
    store_action_info,
)
from .api.optimized_crud import OptimizedCRUD

# === 优化层导出（可选） ===
from .optimization.cache_manager import get_cache_manager
from .optimization.batch_scheduler import get_batch_scheduler
from .optimization.preloader import get_preloader

# === 旧接口兼容 ===
from .database import (
    db,  # DatabaseProxy
    initialize_sql_database,
    stop_database,
)

# === 模型映射（向后兼容） ===
MODEL_MAPPING = {
    "Messages": Messages,
    "ActionRecords": ActionRecords,
    "PersonInfo": PersonInfo,
    "ChatStreams": ChatStreams,
    "LLMUsage": LLMUsage,
    "Emoji": Emoji,
    "Images": Images,
    "Videos": Videos,
    "OnlineTime": OnlineTime,
    "Memory": Memory,
    "Expression": Expression,
    "ThinkingLog": ThinkingLog,
    "GraphNodes": GraphNodes,
    "GraphEdges": GraphEdges,
    "Schedule": Schedule,
    "MonthlyPlan": MonthlyPlan,
    "UserRelationships": UserRelationships,
    # ... 完整映射
}

__all__ = [
    # 会话管理
    "get_db_session",
    "get_engine",
    
    # CRUD操作
    "db_query",
    "db_save",
    "db_get",
    "store_action_info",
    
    # 优化API
    "OptimizedCRUD",
    
    # 模型
    "Base",
    "ChatStreams",
    "Messages",
    # ... 所有模型
    
    # 模型映射
    "MODEL_MAPPING",
    
    # 初始化
    "db",
    "initialize_sql_database",
    "stop_database",
    
    # 优化工具
    "get_cache_manager",
    "get_batch_scheduler",
    "get_preloader",
]
```

---

## ⚠️ 风险评估与回滚方案

### 风险识别

| 风险 | 等级 | 影响 | 缓解措施 |
|------|------|------|---------|
| API接口变更 | 高 | 现有代码崩溃 | 完整的兼容层 + 测试覆盖 |
| 性能下降 | 中 | 响应变慢 | 性能基准测试 + 监控 |
| 数据不一致 | 高 | 数据损坏 | 批量操作事务保证 + 备份 |
| 内存泄漏 | 中 | 资源耗尽 | 压力测试 + 监控 |
| 缓存穿透 | 中 | 数据库压力增大 | 布隆过滤器 + 空值缓存 |

### 回滚方案

#### 快速回滚
```bash
# 如果发现重大问题，立即回滚到旧版本
git checkout <previous-commit>
# 或使用feature分支开发，随时可切换
git checkout main
```

#### 渐进式回滚
```python
# 在新代码中添加开关
from src.config.config import global_config

if global_config.database.use_legacy_mode:
    # 使用旧实现
    from .legacy.database import db_query
else:
    # 使用新实现
    from .api.crud import db_query
```

### 监控指标

重构后需要监控的关键指标：
- API响应时间（P50, P95, P99）
- 数据库连接数
- 缓存命中率
- 批量操作成功率
- 错误率和异常
- 内存使用量

---

## 📊 预期效果

### 性能提升目标

| 指标 | 当前 | 目标 | 提升 |
|------|------|------|------|
| 高频读取延迟 | ~50ms | ~10ms | 80% ↓ |
| 缓存命中率 | 0% | 85%+ | ∞ |
| 写入吞吐量 | ~100/s | ~1000/s | 10x ↑ |
| 连接池利用率 | ~60% | >90% | 50% ↑ |
| 数据库连接数 | 动态 | 稳定 | 更稳定 |

### 代码质量提升

- ✅ 减少文件数量和代码行数
- ✅ 职责更清晰，易于维护
- ✅ 完整的类型注解
- ✅ 统一的错误处理
- ✅ 完善的文档和示例

---

## ✅ 验收标准

### 功能验收
- [ ] 所有现有测试通过
- [ ] 所有API接口保持兼容
- [ ] 无数据丢失或不一致
- [ ] 无性能回归

### 性能验收
- [ ] 高频读取延迟 < 15ms（P95）
- [ ] 缓存命中率 > 80%
- [ ] 写入吞吐量 > 500/s
- [ ] 连接池利用率 > 85%

### 代码质量验收
- [ ] 类型检查无错误
- [ ] 代码覆盖率 > 80%
- [ ] 无重大代码异味
- [ ] 文档完整

---

## 📝 总结

本重构方案在保持完全向后兼容的前提下，通过以下措施优化数据库模块：

1. **架构清晰化** - 分层设计，职责明确
2. **多级缓存** - L1/L2缓存 + 智能失效
3. **智能预加载** - 减少冷启动延迟
4. **批量调度增强** - 自适应批量大小 + 优先级队列
5. **装饰器工具** - 简化高频操作的优化
6. **性能监控** - 实时监控和告警

预期可实现：
- 高频读取延迟降低 80%
- 写入吞吐量提升 10 倍
- 连接池利用率提升至 90% 以上

风险可控，可随时回滚。
