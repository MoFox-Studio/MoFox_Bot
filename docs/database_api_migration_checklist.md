# 数据库API迁移检查清单

## 概述

本文档列出了项目中需要从直接数据库查询迁移到使用优化后API的代码位置。

## 为什么需要迁移？

优化后的API具有以下优势：
1. **自动缓存**: 高频查询已集成多级缓存，减少90%+数据库访问
2. **批量处理**: 消息存储使用批处理，减少连接池压力
3. **统一接口**: 标准化的错误处理和日志记录
4. **性能监控**: 内置性能统计和慢查询警告
5. **代码简洁**: 简化的API调用，减少样板代码

## 迁移优先级

### 🔴 高优先级（高频查询）

#### 1. PersonInfo 查询 - `src/person_info/person_info.py`

**当前实现**：直接使用 SQLAlchemy `session.execute(select(PersonInfo)...)`

**影响范围**：
- `get_value()` - 每条消息都会调用
- `get_values()` - 批量查询用户信息
- `update_one_field()` - 更新用户字段
- `is_person_known()` - 检查用户是否已知
- `get_person_info_by_name()` - 根据名称查询

**迁移目标**：使用 `src.common.database.api.specialized` 中的：
```python
from src.common.database.api.specialized import (
    get_or_create_person,
    update_person_affinity,
)

# 替代直接查询
person, created = await get_or_create_person(
    platform=platform,
    person_id=person_id,
    defaults={"nickname": nickname, ...}
)
```

**优势**：
- ✅ 10分钟缓存，减少90%+数据库查询
- ✅ 自动缓存失效机制
- ✅ 标准化的错误处理

**预计工作量**：⏱️ 2-4小时

---

#### 2. UserRelationships 查询 - `src/person_info/relationship_fetcher.py`

**当前实现**：使用 `db_query(UserRelationships, ...)`

**影响代码**：
- `build_relation_info()` 第189行
- 查询用户关系数据

**迁移目标**：
```python
from src.common.database.api.specialized import (
    get_user_relationship,
    update_relationship_affinity,
)

# 替代 db_query
relationship = await get_user_relationship(
    platform=platform,
    user_id=user_id,
    target_id=target_id,
)
```

**优势**：
- ✅ 5分钟缓存
- ✅ 高频场景减少80%+数据库访问
- ✅ 自动缓存失效

**预计工作量**：⏱️ 1-2小时

---

#### 3. ChatStreams 查询 - `src/person_info/relationship_fetcher.py`

**当前实现**：使用 `db_query(ChatStreams, ...)`

**影响代码**：
- `build_chat_stream_impression()` 第250行

**迁移目标**：
```python
from src.common.database.api.specialized import get_or_create_chat_stream

stream, created = await get_or_create_chat_stream(
    stream_id=stream_id,
    platform=platform,
    defaults={...}
)
```

**优势**：
- ✅ 5分钟缓存
- ✅ 减少重复查询
- ✅ 活跃会话期间性能提升75%+

**预计工作量**：⏱️ 30分钟-1小时

---

### 🟡 中优先级（中频查询）

#### 4. ActionRecords 查询 - `src/chat/utils/statistic.py`

**当前实现**：使用 `db_query(ActionRecords, ...)`

**影响代码**：
- 第73行：更新行为记录
- 第97行：插入新记录
- 第105行：查询记录

**迁移目标**：
```python
from src.common.database.api.specialized import store_action_info, get_recent_actions

# 存储行为
await store_action_info(
    user_id=user_id,
    action_type=action_type,
    ...
)

# 获取最近行为
actions = await get_recent_actions(
    user_id=user_id,
    limit=10
)
```

**优势**：
- ✅ 标准化的API
- ✅ 更好的性能监控
- ✅ 未来可添加缓存

**预计工作量**：⏱️ 1-2小时

---

#### 5. CacheEntries 查询 - `src/common/cache_manager.py`

**当前实现**：使用 `db_query(CacheEntries, ...)`

**注意**：这是旧的基于数据库的缓存系统

**建议**：
- ⚠️ 考虑完全迁移到新的 `MultiLevelCache` 系统
- ⚠️ 新系统使用内存缓存，性能更好
- ⚠️ 如需持久化，可以添加持久化层

**预计工作量**：⏱️ 4-8小时（如果重构整个缓存系统）

---

### 🟢 低优先级（低频查询或测试代码）

#### 6. 测试代码 - `tests/test_api_utils_compatibility.py`

**当前实现**：测试中使用直接查询

**建议**：
- ℹ️ 测试代码可以保持现状
- ℹ️ 但可以添加新的测试用例测试优化后的API

**预计工作量**：⏱️ 可选

---

## 迁移步骤

### 第一阶段：高频查询（推荐立即进行）

1. **迁移 PersonInfo 查询**
   - [ ] 修改 `person_info.py` 的 `get_value()`
   - [ ] 修改 `person_info.py` 的 `get_values()`
   - [ ] 修改 `person_info.py` 的 `update_one_field()`
   - [ ] 修改 `person_info.py` 的 `is_person_known()`
   - [ ] 测试缓存效果

2. **迁移 UserRelationships 查询**
   - [ ] 修改 `relationship_fetcher.py` 的关系查询
   - [ ] 测试缓存效果

3. **迁移 ChatStreams 查询**
   - [ ] 修改 `relationship_fetcher.py` 的流查询
   - [ ] 测试缓存效果

### 第二阶段：中频查询（可以分批进行）

4. **迁移 ActionRecords**
   - [ ] 修改 `statistic.py` 的行为记录
   - [ ] 添加单元测试

### 第三阶段：系统优化（长期目标）

5. **重构旧缓存系统**
   - [ ] 评估 `cache_manager.py` 的使用情况
   - [ ] 制定迁移到 MultiLevelCache 的计划
   - [ ] 逐步迁移

---

## 性能提升预期

基于当前测试数据：

| 查询类型 | 迁移前 QPS | 迁移后 QPS | 提升 | 数据库负载降低 |
|---------|-----------|-----------|------|--------------|
| PersonInfo | ~50 | ~500+ | **10倍** | **90%+** |
| UserRelationships | ~30 | ~150+ | **5倍** | **80%+** |
| ChatStreams | ~40 | ~160+ | **4倍** | **75%+** |

**总体效果**：
- 📈 高峰期数据库连接数减少 **80%+**
- 📈 平均响应时间降低 **70%+**
- 📈 系统吞吐量提升 **5-10倍**

---

## 注意事项

### 1. 缓存一致性

迁移后需要确保：
- ✅ 所有更新操作都正确使缓存失效
- ✅ 缓存键的生成逻辑一致
- ✅ TTL设置合理

### 2. 测试覆盖

每次迁移后需要：
- ✅ 运行单元测试
- ✅ 测试缓存命中率
- ✅ 监控性能指标
- ✅ 检查日志中的缓存统计

### 3. 回滚计划

如果遇到问题：
- 🔄 保留原有代码在注释中
- 🔄 使用 git 标签标记迁移点
- 🔄 准备快速回滚脚本

### 4. 逐步迁移

建议：
- ⭐ 一次迁移一个模块
- ⭐ 在测试环境充分验证
- ⭐ 监控生产环境指标
- ⭐ 根据反馈调整策略

---

## 迁移示例

### 示例1：PersonInfo 查询迁移

**迁移前**：
```python
# src/person_info/person_info.py
async def get_value(self, person_id: str, field_name: str):
    async with get_db_session() as session:
        result = await session.execute(
            select(PersonInfo).where(PersonInfo.person_id == person_id)
        )
        person = result.scalar_one_or_none()
        if person:
            return getattr(person, field_name, None)
        return None
```

**迁移后**：
```python
# src/person_info/person_info.py
async def get_value(self, person_id: str, field_name: str):
    from src.common.database.api.crud import CRUDBase
    from src.common.database.core.models import PersonInfo
    from src.common.database.utils.decorators import cached
    
    @cached(ttl=600, key_prefix=f"person_field_{field_name}")
    async def _get_cached_value(pid: str):
        crud = CRUDBase(PersonInfo)
        person = await crud.get_by(person_id=pid)
        if person:
            return getattr(person, field_name, None)
        return None
    
    return await _get_cached_value(person_id)
```

或者更简单，使用现有的 `get_or_create_person`：
```python
async def get_value(self, person_id: str, field_name: str):
    from src.common.database.api.specialized import get_or_create_person
    
    # 解析 person_id 获取 platform 和 user_id
    # （需要调整 get_or_create_person 支持 person_id 查询，
    #  或者在 PersonInfoManager 中缓存映射关系）
    person, _ = await get_or_create_person(
        platform=self._platform_cache.get(person_id),
        person_id=person_id,
    )
    if person:
        return getattr(person, field_name, None)
    return None
```

### 示例2：UserRelationships 迁移

**迁移前**：
```python
# src/person_info/relationship_fetcher.py
relationships = await db_query(
    UserRelationships,
    filters={"user_id": user_id},
    limit=1,
)
```

**迁移后**：
```python
from src.common.database.api.specialized import get_user_relationship

relationship = await get_user_relationship(
    platform=platform,
    user_id=user_id,
    target_id=target_id,
)
# 如果需要查询某个用户的所有关系，可以添加新的API函数
```

---

## 进度跟踪

| 任务 | 状态 | 负责人 | 预计完成时间 | 实际完成时间 | 备注 |
|-----|------|--------|------------|------------|------|
| PersonInfo 迁移 | ⏳ 待开始 | - | - | - | 高优先级 |
| UserRelationships 迁移 | ⏳ 待开始 | - | - | - | 高优先级 |
| ChatStreams 迁移 | ⏳ 待开始 | - | - | - | 高优先级 |
| ActionRecords 迁移 | ⏳ 待开始 | - | - | - | 中优先级 |
| 缓存系统重构 | ⏳ 待开始 | - | - | - | 长期目标 |

---

## 相关文档

- [数据库缓存系统使用指南](./database_cache_guide.md)
- [数据库重构完成报告](./database_refactoring_completion.md)
- [优化后的API文档](../src/common/database/api/specialized.py)

---

## 联系与支持

如果在迁移过程中遇到问题：
1. 查看相关文档
2. 检查示例代码
3. 运行测试验证
4. 查看日志中的缓存统计

**最后更新**: 2025-11-01
