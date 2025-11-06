# 记忆去重工具使用指南

## 📋 功能说明

`deduplicate_memories.py` 是一个用于清理重复记忆的工具。它会：

1. 扫描所有标记为"相似"关系的记忆对
2. 根据重要性、激活度和创建时间决定保留哪个
3. 删除重复的记忆，保留最有价值的那个
4. 提供详细的去重报告

## 🚀 快速开始

### 步骤1: 预览模式（推荐）

**首次使用前，建议先运行预览模式，查看会删除哪些记忆：**

```bash
python scripts/deduplicate_memories.py --dry-run
```

输出示例：
```
============================================================
记忆去重工具
============================================================
数据目录: data/memory_graph
相似度阈值: 0.85
模式: 预览模式（不实际删除）
============================================================

✅ 记忆管理器初始化成功，共 156 条记忆
找到 23 对相似记忆（阈值>=0.85）

[预览] 去重相似记忆对 (相似度=0.904):
  保留: mem_20251106_202832_887727
    - 主题: 今天天气很好
    - 重要性: 0.60
    - 激活度: 0.55
    - 创建时间: 2024-11-06 20:28:32
  删除: mem_20251106_202828_883440
    - 主题: 今天天气晴朗
    - 重要性: 0.50
    - 激活度: 0.50
    - 创建时间: 2024-11-06 20:28:28
  [预览模式] 不执行实际删除

============================================================
去重报告
============================================================
总记忆数: 156
相似记忆对: 23
发现重复: 23
预览通过: 23
错误数: 0
耗时: 2.35秒

⚠️ 这是预览模式，未实际删除任何记忆
💡 要执行实际删除，请运行: python scripts/deduplicate_memories.py
============================================================
```

### 步骤2: 执行去重

**确认预览结果无误后，执行实际去重：**

```bash
python scripts/deduplicate_memories.py
```

输出示例：
```
============================================================
记忆去重工具
============================================================
数据目录: data/memory_graph
相似度阈值: 0.85
模式: 执行模式（会实际删除）
============================================================

✅ 记忆管理器初始化成功，共 156 条记忆
找到 23 对相似记忆（阈值>=0.85）

[执行] 去重相似记忆对 (相似度=0.904):
  保留: mem_20251106_202832_887727
    ...
  删除: mem_20251106_202828_883440
    ...
  ✅ 删除成功

正在保存数据...
✅ 数据已保存

============================================================
去重报告
============================================================
总记忆数: 156
相似记忆对: 23
成功删除: 23
错误数: 0
耗时: 5.67秒

✅ 去重完成！
📊 最终记忆数: 133 (减少 23 条)
============================================================
```

## 🎛️ 命令行参数

### `--dry-run`（推荐先使用）

预览模式，不实际删除任何记忆。

```bash
python scripts/deduplicate_memories.py --dry-run
```

### `--threshold <相似度>`

指定相似度阈值，只处理相似度大于等于此值的记忆对。

```bash
# 只处理高度相似（>=0.95）的记忆
python scripts/deduplicate_memories.py --threshold 0.95

# 处理中等相似（>=0.8）的记忆
python scripts/deduplicate_memories.py --threshold 0.8
```

**阈值建议**：
- `0.95-1.0`: 极高相似度，几乎完全相同（最安全）
- `0.9-0.95`: 高度相似，内容基本一致（推荐）
- `0.85-0.9`: 中等相似，可能有细微差别（谨慎使用）
- `<0.85`: 低相似度，可能误删（不推荐）

### `--data-dir <目录>`

指定记忆数据目录。

```bash
# 对测试数据去重
python scripts/deduplicate_memories.py --data-dir data/test_memory

# 对备份数据去重
python scripts/deduplicate_memories.py --data-dir data/memory_backup
```

## 📖 使用场景

### 场景1: 定期维护

**建议频率**: 每周或每月运行一次

```bash
# 1. 先预览
python scripts/deduplicate_memories.py --dry-run --threshold 0.92

# 2. 确认后执行
python scripts/deduplicate_memories.py --threshold 0.92
```

### 场景2: 清理大量重复

**适用于**: 导入外部数据后，或发现大量重复记忆

```bash
# 使用较低阈值，清理更多重复
python scripts/deduplicate_memories.py --threshold 0.85
```

### 场景3: 保守清理

**适用于**: 担心误删，只想删除极度相似的记忆

```bash
# 使用高阈值，只删除几乎完全相同的记忆
python scripts/deduplicate_memories.py --threshold 0.98
```

### 场景4: 测试环境

**适用于**: 在测试数据上验证效果

```bash
# 对测试数据执行去重
python scripts/deduplicate_memories.py --data-dir data/test_memory --dry-run
```

## 🔍 去重策略

### 保留原则（按优先级）

脚本会按以下优先级决定保留哪个记忆：

1. **重要性更高** (`importance` 值更大)
2. **激活度更高** (`activation` 值更大)
3. **创建时间更早** (更早创建的记忆)

### 增强保留记忆

保留的记忆会获得以下增强：

- **重要性** +0.05（最高1.0）
- **激活度** +0.05（最高1.0）
- **访问次数** 累加被删除记忆的访问次数

### 示例

```
记忆A: 重要性0.8, 激活度0.6, 创建于 2024-11-01
记忆B: 重要性0.7, 激活度0.9, 创建于 2024-11-05

结果: 保留记忆A（重要性更高）
增强: 重要性 0.8 → 0.85, 激活度 0.6 → 0.65
```

## ⚠️ 注意事项

### 1. 备份数据

**在执行实际去重前，建议备份数据：**

```bash
# Windows
xcopy data\memory_graph data\memory_graph_backup /E /I /Y

# Linux/Mac
cp -r data/memory_graph data/memory_graph_backup
```

### 2. 先预览再执行

**务必先运行 `--dry-run` 预览：**

```bash
# 错误示范 ❌
python scripts/deduplicate_memories.py  # 直接执行

# 正确示范 ✅
python scripts/deduplicate_memories.py --dry-run  # 先预览
python scripts/deduplicate_memories.py  # 再执行
```

### 3. 阈值选择

**过低的阈值可能导致误删：**

```bash
# 风险较高 ⚠️
python scripts/deduplicate_memories.py --threshold 0.7

# 推荐范围 ✅
python scripts/deduplicate_memories.py --threshold 0.92
```

### 4. 不可恢复

**删除的记忆无法恢复！** 如果不确定，请：

1. 先备份数据
2. 使用 `--dry-run` 预览
3. 使用较高的阈值（如 0.95）

### 5. 中断恢复

如果执行过程中中断（Ctrl+C），已删除的记忆无法恢复。建议：

- 在低负载时段运行
- 确保足够的执行时间
- 使用 `--threshold` 限制处理数量

## 🐛 故障排查

### 问题1: 找不到相似记忆对

```
找到 0 对相似记忆（阈值>=0.85）
```

**原因**：
- 没有标记为"相似"的边
- 阈值设置过高

**解决**：
1. 降低阈值：`--threshold 0.7`
2. 检查记忆系统是否正确创建了相似关系
3. 先运行自动关联任务

### 问题2: 初始化失败

```
❌ 记忆管理器初始化失败
```

**原因**：
- 数据目录不存在
- 配置文件错误
- 数据文件损坏

**解决**：
1. 检查数据目录是否存在
2. 验证配置文件：`config/bot_config.toml`
3. 查看详细日志定位问题

### 问题3: 删除失败

```
❌ 删除失败: ...
```

**原因**：
- 权限不足
- 数据库锁定
- 文件损坏

**解决**：
1. 检查文件权限
2. 确保没有其他进程占用数据
3. 恢复备份后重试

## 📊 性能参考

| 记忆数量 | 相似对数 | 执行时间（预览） | 执行时间（实际） |
|---------|---------|----------------|----------------|
| 100 | 10 | ~1秒 | ~2秒 |
| 500 | 50 | ~3秒 | ~6秒 |
| 1000 | 100 | ~5秒 | ~12秒 |
| 5000 | 500 | ~15秒 | ~45秒 |

**注**: 实际时间取决于服务器性能和数据复杂度

## 🔗 相关工具

- **记忆整理**: `src/memory_graph/manager.py::consolidate_memories()`
- **自动关联**: `src/memory_graph/manager.py::auto_link_memories()`
- **配置验证**: `scripts/verify_config_update.py`

## 💡 最佳实践

### 1. 定期维护流程

```bash
# 每周执行
cd /path/to/bot

# 1. 备份
cp -r data/memory_graph data/memory_graph_backup_$(date +%Y%m%d)

# 2. 预览
python scripts/deduplicate_memories.py --dry-run --threshold 0.92

# 3. 执行
python scripts/deduplicate_memories.py --threshold 0.92

# 4. 验证
python scripts/verify_config_update.py
```

### 2. 保守去重策略

```bash
# 只删除极度相似的记忆
python scripts/deduplicate_memories.py --dry-run --threshold 0.98
python scripts/deduplicate_memories.py --threshold 0.98
```

### 3. 批量清理策略

```bash
# 先清理高相似度的
python scripts/deduplicate_memories.py --threshold 0.95

# 再清理中相似度的（可选）
python scripts/deduplicate_memories.py --dry-run --threshold 0.9
python scripts/deduplicate_memories.py --threshold 0.9
```

## 📝 总结

- ✅ **务必先备份数据**
- ✅ **务必先运行 `--dry-run`**
- ✅ **建议使用阈值 >= 0.92**
- ✅ **定期运行，保持记忆库清洁**
- ❌ **避免过低阈值（< 0.85）**
- ❌ **避免跳过预览直接执行**

---

**创建日期**: 2024-11-06  
**版本**: v1.0  
**维护者**: MoFox-Bot Team
