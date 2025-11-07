# 记忆图系统使用指南

## 概述

记忆图系统是MoFox Bot的新一代记忆管理系统,基于图结构存储和管理记忆,提供更智能的记忆检索、整合和遗忘机制。

## 核心特性

### 1. 图结构存储
- 使用**节点-边**模型表示记忆
- 支持复杂的记忆关系网络
- 高效的图遍历和邻接查询

### 2. 智能记忆检索
- **向量相似度搜索**: 基于语义理解检索相关记忆
- **查询优化**: 自动扩展查询关键词
- **重要性排序**: 优先返回重要记忆
- **图扩展**: 可选择性扩展到相邻记忆

### 3. 自动记忆整合
- **相似度检测**: 自动识别相似记忆
- **智能合并**: 合并重复记忆,提升激活度
- **时间窗口**: 可配置整合时间范围
- **定期执行**: 每小时自动整合(可配置)

### 4. 记忆遗忘机制
- **激活度衰减**: 未使用的记忆逐渐降低激活度
- **自动清理**: 低激活度记忆自动遗忘
- **重要性保护**: 高重要性记忆不会被遗忘

## 配置说明

### 基本配置 (`bot_config.toml`)

```toml
[memory_graph]
# 启用开关
enable = true

# 数据存储目录
data_dir = "data/memory_graph"

# 向量数据库配置
vector_collection_name = "memory_nodes"
vector_db_path = ""  # 为空则使用data_dir

# 检索配置
search_top_k = 5  # 返回最相关的5条记忆
search_min_importance = 0.0  # 最低重要性阈值
search_similarity_threshold = 0.0  # 相似度阈值
search_optimize_query = true  # 启用查询优化

# 记忆整合配置
consolidation_enabled = true  # 启用自动整合
consolidation_interval_hours = 1.0  # 每小时执行一次
consolidation_similarity_threshold = 0.85  # 相似度>=0.85认为重复
consolidation_time_window_hours = 24  # 整合过去24小时的记忆

# 记忆遗忘配置
forgetting_enabled = true  # 启用自动遗忘
forgetting_activation_threshold = 0.1  # 激活度<0.1的记忆会被遗忘
forgetting_min_importance = 0.3  # 重要性>=0.3的记忆不会被遗忘

# 激活度配置
activation_decay_rate = 0.95  # 每天衰减5%
activation_propagation_strength = 0.1  # 传播强度10%
activation_propagation_depth = 2  # 传播深度2层

# 性能配置
max_nodes_per_memory = 50  # 每个记忆最多50个节点
max_related_memories = 10  # 最多返回10个相关记忆
```

### 配置项说明

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `enable` | bool | true | 启用记忆图系统 |
| `data_dir` | string | "data/memory_graph" | 数据存储目录 |
| `search_top_k` | int | 5 | 检索返回数量 |
| `search_optimize_query` | bool | true | 启用查询优化 |
| `consolidation_enabled` | bool | true | 启用自动整合 |
| `consolidation_interval_hours` | float | 1.0 | 整合间隔(小时) |
| `consolidation_similarity_threshold` | float | 0.85 | 相似度阈值 |
| `forgetting_enabled` | bool | true | 启用自动遗忘 |
| `forgetting_activation_threshold` | float | 0.1 | 遗忘阈值 |
| `activation_decay_rate` | float | 0.95 | 激活度衰减率 |

## LLM工具使用

### 1. 创建记忆 (`create_memory`)

**描述**: 创建一个新的记忆,包含主体、主题和相关信息。

**参数**:
- `subject` (必填): 记忆主体,如"用户"、"AI助手"
- `memory_type` (必填): 记忆类型,如"事件"、"知识"、"偏好"
- `topic` (必填): 记忆主题,简短描述
- `object` (可选): 记忆对象
- `attributes` (可选): 附加属性,JSON格式
- `importance` (可选): 重要性0.0-1.0,默认0.5

**示例**:
```json
{
  "subject": "用户",
  "memory_type": "偏好",
  "topic": "喜欢晴天",
  "importance": 0.7
}
```

**返回**:
```json
{
  "name": "create_memory",
  "content": "成功创建记忆（ID: mem_xxx）",
  "memory_id": "mem_xxx"
}
```

### 2. 搜索记忆 (`search_memories`)

**描述**: 根据查询搜索相关记忆。

**参数**:
- `query` (必填): 搜索查询文本
- `top_k` (可选): 返回数量,默认5
- `expand_depth` (可选): 图扩展深度,默认1

**示例**:
```json
{
  "query": "天气偏好",
  "top_k": 3
}
```

### 3. 关联记忆 (`link_memories`)

**描述**: 在两个记忆之间建立关联(暂不对LLM开放)。

## 代码使用示例

### 初始化记忆管理器

```python
from src.memory_graph.manager_singleton import initialize_memory_manager, get_memory_manager

# 初始化(在bot启动时调用一次)
await initialize_memory_manager()

# 获取管理器实例
manager = get_memory_manager()
```

### 创建记忆

```python
memory = await manager.create_memory(
    subject="用户",
    memory_type="事件",
    topic="询问天气",
    object_="上海",
    attributes={"时间": "早上"},
    importance=0.7
)
print(f"创建记忆: {memory.id}")
```

### 搜索记忆

```python
memories = await manager.search_memories(
    query="天气",
    top_k=5,
    optimize_query=True  # 启用查询优化
)

for mem in memories:
    print(f"- {mem.get_subject_node().content}: {mem.importance}")
```

### 激活记忆

```python
# 访问记忆时会自动激活
await manager.activate_memory(memory.id, strength=0.5)
```

### 手动执行维护

```python
# 整合相似记忆
result = await manager.consolidate_memories(
    similarity_threshold=0.85,
    time_window_hours=24
)
print(f"合并了 {result['merged_count']} 条记忆")

# 遗忘低激活度记忆
forgotten = await manager.auto_forget_memories(
    activation_threshold=0.1,
    min_importance=0.3
)
print(f"遗忘了 {forgotten} 条记忆")
```

### 获取统计信息

```python
stats = manager.get_statistics()
print(f"总记忆数: {stats['total_memories']}")
print(f"激活记忆数: {stats['active_memories']}")
print(f"平均激活度: {stats['avg_activation']:.3f}")
```

## 最佳实践

### 1. 记忆重要性评分

- **0.8-1.0**: 非常重要(用户核心偏好、关键事件)
- **0.6-0.8**: 重要(常见偏好、重要对话)
- **0.4-0.6**: 一般(普通事件)
- **0.2-0.4**: 次要(临时信息)
- **0.0-0.2**: 不重要(无关紧要的细节)

### 2. 记忆类型选择

- **事件**: 发生的具体事情(提问、回答、活动)
- **知识**: 事实性信息(定义、解释)
- **偏好**: 用户喜好(喜欢/不喜欢)
- **关系**: 实体之间的关系
- **技能**: 能力或技巧

### 3. 性能优化

- 定期清理: 每周手动执行一次深度整合
- 调整阈值: 根据实际情况调整相似度和遗忘阈值
- 限制数量: 控制单个记忆的节点数量(<50)
- 批量操作: 使用批量API减少调用次数

### 4. 维护建议

- **每天**: 自动整合和遗忘(系统自动执行)
- **每周**: 检查统计信息,调整配置
- **每月**: 备份记忆数据(`data/memory_graph/`)

## 数据持久化

### 存储结构

```
data/memory_graph/
├── memory_graph.json     # 图结构数据
└── chroma_db/            # 向量数据库
    └── memory_nodes/     # 节点向量集合
```

### 备份建议

```bash
# 备份整个记忆图目录
cp -r data/memory_graph/ backup/memory_graph_$(date +%Y%m%d)/

# 或使用git
cd data/memory_graph/
git add .
git commit -m "Backup: $(date)"
```

## 故障排除

### 问题1: 记忆检索返回空

**可能原因**:
- 向量数据库未初始化
- 查询关键词过于模糊
- 相似度阈值设置过高

**解决方案**:
```python
# 降低相似度阈值
memories = await manager.search_memories(
    query="具体关键词",
    top_k=10,
    min_similarity=0.0  # 降低阈值
)
```

### 问题2: 记忆整合过于激进

**可能原因**:
- 相似度阈值设置过低

**解决方案**:
```toml
# 提高整合阈值
consolidation_similarity_threshold = 0.90  # 从0.85提高到0.90
```

### 问题3: 内存占用过高

**可能原因**:
- 记忆数量过多
- 向量维度过高

**解决方案**:
```toml
# 启用更激进的遗忘策略
forgetting_activation_threshold = 0.2  # 从0.1提高到0.2
forgetting_min_importance = 0.4  # 从0.3提高到0.4
```

## 迁移指南

### 从旧记忆系统迁移

旧记忆系统(`[memory]`配置)已废弃,建议迁移到新系统:

1. **备份旧数据**: 备份`data/memory/`目录
2. **更新配置**: 删除`[memory]`配置,启用`[memory_graph]`
3. **重启系统**: 新系统会自动初始化
4. **验证功能**: 测试记忆创建和检索

**注意**: 旧记忆数据不会自动迁移,需要手动导入(如需要)。

## API参考

完整API文档请参考:
- `src/memory_graph/manager.py` - MemoryManager核心API
- `src/memory_graph/plugin_tools/memory_plugin_tools.py` - LLM工具
- `src/memory_graph/models.py` - 数据模型

## 更新日志

### v7.6.0 (2025-11-05)
- ✅ 完整的记忆图系统实现
- ✅ LLM工具集成
- ✅ 自动整合和遗忘机制
- ✅ 配置系统支持
- ✅ 完整的集成测试(5/5通过)

---

**相关文档**:
- [系统架构](architecture/memory_graph_architecture.md)
- [API文档](api/memory_graph_api.md)
- [开发指南](development/memory_graph_dev.md)
