# 数据库缓存系统使用指南

## 概述

MoFox Bot 数据库系统集成了多级缓存架构，用于优化高频查询性能，减少数据库压力。

## 缓存架构

### 多级缓存（Multi-Level Cache）

- **L1 缓存（热数据）**
  - 容量：1000 项
  - TTL：60 秒
  - 用途：最近访问的热点数据

- **L2 缓存（温数据）**
  - 容量：10000 项
  - TTL：300 秒
  - 用途：较常访问但不是最热的数据

### LRU 驱逐策略

两级缓存都使用 LRU（Least Recently Used）算法：
- 缓存满时自动驱逐最少使用的项
- 保证最常用数据始终在缓存中

## 使用方法

### 1. 使用 @cached 装饰器（推荐）

最简单的方式是使用 `@cached` 装饰器：

```python
from src.common.database.utils.decorators import cached

@cached(ttl=600, key_prefix="person_info")
async def get_person_info(platform: str, person_id: str):
    """获取人员信息（带10分钟缓存）"""
    return await _person_info_crud.get_by(
        platform=platform,
        person_id=person_id,
    )
```

#### 参数说明

- `ttl`: 缓存过期时间（秒），None 表示永不过期
- `key_prefix`: 缓存键前缀，用于命名空间隔离
- `use_args`: 是否将位置参数包含在缓存键中（默认 True）
- `use_kwargs`: 是否将关键字参数包含在缓存键中（默认 True）

### 2. 手动缓存管理

需要更精细控制时，可以手动管理缓存：

```python
from src.common.database.optimization.cache_manager import get_cache

async def custom_query():
    cache = await get_cache()
    
    # 尝试从缓存获取
    result = await cache.get("my_key")
    if result is not None:
        return result
    
    # 缓存未命中，执行查询
    result = await execute_database_query()
    
    # 写入缓存
    await cache.set("my_key", result)
    
    return result
```

### 3. 缓存失效

更新数据后需要主动使缓存失效：

```python
from src.common.database.optimization.cache_manager import get_cache
from src.common.database.utils.decorators import generate_cache_key

async def update_person_affinity(platform: str, person_id: str, affinity_delta: float):
    # 执行更新
    await _person_info_crud.update(person.id, {"affinity": new_affinity})
    
    # 使缓存失效
    cache = await get_cache()
    cache_key = generate_cache_key("person_info", platform, person_id)
    await cache.delete(cache_key)
```

## 已缓存的查询

### PersonInfo（人员信息）

- **函数**: `get_or_create_person()`
- **缓存时间**: 10 分钟
- **缓存键**: `person_info:args:<hash>`
- **失效时机**: `update_person_affinity()` 更新好感度时

### UserRelationships（用户关系）

- **函数**: `get_user_relationship()`
- **缓存时间**: 5 分钟
- **缓存键**: `user_relationship:args:<hash>`
- **失效时机**: `update_relationship_affinity()` 更新关系时

### ChatStreams（聊天流）

- **函数**: `get_or_create_chat_stream()`
- **缓存时间**: 5 分钟
- **缓存键**: `chat_stream:args:<hash>`
- **失效时机**: 流更新时（如有需要）

## 缓存统计

查看缓存性能统计：

```python
cache = await get_cache()
stats = await cache.get_stats()

print(f"L1 命中率: {stats['l1_hits']}/{stats['l1_hits'] + stats['l1_misses']}")
print(f"L2 命中率: {stats['l2_hits']}/{stats['l2_hits'] + stats['l2_misses']}")
print(f"总命中率: {stats['total_hits']}/{stats['total_requests']}")
```

## 最佳实践

### 1. 选择合适的 TTL

- **频繁变化的数据**: 60-300 秒（如在线状态）
- **中等变化的数据**: 300-600 秒（如用户信息、关系）
- **稳定数据**: 600-1800 秒（如配置、元数据）

### 2. 缓存键设计

- 使用有意义的前缀：`person_info:`, `user_rel:`, `chat_stream:`
- 确保唯一性：包含所有查询参数
- 避免键冲突：使用 `generate_cache_key()` 辅助函数

### 3. 及时失效

- **写入时失效**: 数据更新后立即删除缓存
- **批量失效**: 使用通配符或前缀批量删除相关缓存
- **惰性失效**: 依赖 TTL 自动过期（适用于非关键数据）

### 4. 监控缓存效果

定期检查缓存统计：
- 命中率 > 70% - 缓存效果良好
- 命中率 50-70% - 可以优化 TTL 或缓存策略
- 命中率 < 50% - 考虑是否需要缓存该查询

## 性能提升数据

基于测试结果：

- **PersonInfo 查询**: 缓存命中时减少 **90%+** 数据库访问
- **关系查询**: 高频场景下减少 **80%+** 数据库连接
- **聊天流查询**: 活跃会话期间减少 **75%+** 重复查询

## 注意事项

1. **缓存一致性**: 更新数据后务必使缓存失效
2. **内存占用**: 监控缓存大小，避免占用过多内存
3. **序列化**: 缓存的对象需要可序列化（SQLAlchemy 模型实例可能需要特殊处理）
4. **并发安全**: MultiLevelCache 是线程安全和协程安全的

## 故障排除

### 缓存未生效

1. 检查是否正确导入装饰器
2. 确认 TTL 设置合理
3. 查看日志中的 "缓存命中" 消息

### 数据不一致

1. 检查更新操作是否正确使缓存失效
2. 确认缓存键生成逻辑一致
3. 考虑缩短 TTL 时间

### 内存占用过高

1. 检查缓存统计中的项数
2. 调整 L1/L2 缓存大小（在 cache_manager.py 中配置）
3. 缩短 TTL 加快驱逐

## 扩展阅读

- [数据库优化指南](./database_optimization_guide.md)
- [多级缓存实现](../src/common/database/optimization/cache_manager.py)
- [装饰器文档](../src/common/database/utils/decorators.py)
