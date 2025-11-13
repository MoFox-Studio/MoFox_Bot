# 记忆图系统 (Memory Graph System)

> 基于图结构的智能记忆管理系统

## 🎯 特性

- **图结构存储**: 使用节点-边模型表示复杂记忆关系
- **语义检索**: 基于向量相似度的智能记忆搜索
- **自动整合**: 定期合并相似记忆,减少冗余
- **智能遗忘**: 基于激活度的自动记忆清理
- **LLM集成**: 提供工具供AI助手调用

## 📦 快速开始

### 1. 启用系统

在 `config/bot_config.toml` 中:

```toml
[memory_graph]
enable = true
data_dir = "data/memory_graph"
```

### 2. 创建记忆

```python
from src.memory_graph.manager_singleton import get_memory_manager

manager = get_memory_manager()
memory = await manager.create_memory(
    subject="用户",
    memory_type="偏好",
    topic="喜欢晴天",
    importance=0.7
)
```

### 3. 搜索记忆

```python
memories = await manager.search_memories(
    query="天气偏好",
    top_k=5
)
```

## 🔧 配置说明

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `enable` | true | 启用开关 |
| `search_top_k` | 5 | 检索数量 |
| `consolidation_interval_hours` | 1.0 | 整合间隔 |
| `forgetting_activation_threshold` | 0.1 | 遗忘阈值 |

完整配置参考: [使用指南](memory_graph_guide.md#配置说明)

## 🧪 测试状态

✅ **所有测试通过** (5/5)

- ✅ 基本记忆操作 (CRUD + 检索)
- ✅ LLM工具集成
- ✅ 记忆生命周期管理
- ✅ 维护任务调度
- ✅ 配置系统

运行测试:
```bash
python tests/test_memory_graph_integration.py
```

## 📊 系统架构

```
记忆图系统
├── MemoryManager (核心管理器)
│   ├── 创建/删除记忆
│   ├── 检索记忆
│   └── 维护任务
├── 存储层
│   ├── VectorStore (向量检索)
│   ├── GraphStore (图结构)
│   └── PersistenceManager (持久化)
└── 工具层
    ├── CreateMemoryTool
    ├── SearchMemoriesTool
    └── LinkMemoriesTool
```

## 🛠️ 开发状态

### ✅ 已完成

- [x] Step 1: 插件系统集成 (fc71aad8)
- [x] Step 2: 提示词记忆检索 (c3ca811e)
- [x] Step 3: 定期记忆整合 (4d44b18a)
- [x] Step 4: 配置系统支持 (a3cc0740, 3ea6d1dc)
- [x] Step 5: 集成测试 (23b011e6)

### 📝 待优化

- [ ] 性能测试和优化
- [ ] 扩展文档和示例
- [ ] 高级查询功能

## 📚 文档

- [使用指南](memory_graph_guide.md) - 完整的使用说明
- [API文档](../src/memory_graph/README.md) - API参考
- [测试报告](../tests/test_memory_graph_integration.py) - 集成测试

## 🤝 贡献

欢迎提交Issue和PR!

## 📄 License

MIT License - 查看 [LICENSE](../LICENSE) 文件

---

**MoFox Bot** - 更智能的记忆管理
