# 对象级内存分析指南

## 🎯 概述

对象级内存分析可以帮助你：
- 查看哪些 Python 对象类型占用最多内存
- 追踪对象数量和大小的变化
- 识别内存泄漏的具体对象
- 监控垃圾回收效率

## 🚀 快速开始

### 1. 安装依赖

```powershell
pip install pympler
```

### 2. 启用对象级分析

```powershell
# 基本用法 - 启用对象分析
python scripts/run_bot_with_tracking.py --objects

# 自定义监控间隔（10 秒）
python scripts/run_bot_with_tracking.py --objects --interval 10

# 显示更多对象类型（前 20 个）
python scripts/run_bot_with_tracking.py --objects --object-limit 20

# 完整示例（简写参数）
python scripts/run_bot_with_tracking.py -o -i 10 -l 20
```

## 📊 输出示例

### 进程级信息

```
================================================================================
检查点 #1 - 12:34:56
Bot 进程 (PID: 12345)
  RSS: 45.23 MB
  VMS: 125.45 MB
  占比: 0.35%
  子进程: 1 个
  子进程内存: 32.10 MB
  总内存: 77.33 MB

变化:
  RSS: +2.15 MB
```

### 对象级分析信息

```
📦 对象级内存分析 (检查点 #1)
--------------------------------------------------------------------------------
类型                                       数量        总大小
--------------------------------------------------------------------------------
dict                                     12,345      15.23 MB
str                                      45,678       8.92 MB
list                                      8,901       5.67 MB
tuple                                    23,456       4.32 MB
type                                      1,234       3.21 MB
code                                      2,345       2.10 MB
set                                       1,567       1.85 MB
function                                  3,456       1.23 MB
method                                    4,567     890.45 KB
weakref                                   2,345     678.12 KB

🗑️  垃圾回收统计:
  - 代 0 回收: 125 次
  - 代 1 回收: 12 次
  - 代 2 回收: 2 次
  - 未回收对象: 0
  - 追踪对象数: 89,456

📊 总对象数: 123,456
--------------------------------------------------------------------------------
```

## 🔍 如何解读输出

### 1. 对象类型统计

每一行显示：
- **类型名称**: Python 对象类型（dict、str、list 等）
- **数量**: 该类型的对象实例数量
- **总大小**: 该类型所有对象占用的总内存

**关键指标**：
- `dict` 多是正常的（Python 大量使用字典）
- `str` 多也是正常的（字符串无处不在）
- 如果看到某个自定义类型数量异常增长 → 可能存在泄漏
- 如果某个类型占用内存异常大 → 需要优化

### 2. 垃圾回收统计

**代 0/1/2 回收次数**：
- 代 0：最频繁，新创建的对象
- 代 1：中等频率，存活一段时间的对象
- 代 2：最少，长期存活的对象

**未回收对象**：
- 应该是 0 或很小的数字
- 如果持续增长 → 可能存在循环引用导致的内存泄漏

**追踪对象数**：
- Python 垃圾回收器追踪的对象总数
- 持续增长可能表示内存泄漏

### 3. 总对象数

当前进程中所有 Python 对象的数量。

## 🎯 常见使用场景

### 场景 1: 查找内存泄漏

```powershell
# 长时间运行，频繁检查
python scripts/run_bot_with_tracking.py -o -i 5
```

**观察**：
- 哪些对象类型数量持续增长？
- RSS 内存增长和对象数量增长是否一致？
- 垃圾回收是否正常工作？

### 场景 2: 优化内存占用

```powershell
# 较长间隔，查看稳定状态
python scripts/run_bot_with_tracking.py -o -i 30 -l 25
```

**分析**：
- 前 25 个对象类型中，哪些是你的代码创建的？
- 是否有不必要的大对象缓存？
- 能否使用更轻量的数据结构？

### 场景 3: 调试特定功能

```powershell
# 短间隔，快速反馈
python scripts/run_bot_with_tracking.py -o -i 3
```

**用途**：
- 触发某个功能后立即观察内存变化
- 检查对象是否正确释放
- 验证优化效果

## 📝 保存的历史文件

监控结束后，历史数据会自动保存到：
```
data/memory_diagnostics/bot_memory_monitor_YYYYMMDD_HHMMSS_pidXXXXX.txt
```

文件内容包括：
- 每个检查点的进程内存信息
- 每个检查点的对象统计（前 10 个类型）
- 总体统计信息（起始/结束/峰值/平均）

## 🔧 高级技巧

### 1. 结合代码修改

在你的代码中添加检查点：

```python
import gc
from pympler import muppy, summary

def debug_memory():
    """在关键位置调用此函数"""
    gc.collect()
    all_objects = muppy.get_objects()
    sum_data = summary.summarize(all_objects)
    summary.print_(sum_data, limit=10)
```

### 2. 比较不同时间点

```powershell
# 运行 1 分钟
python scripts/run_bot_with_tracking.py -o -i 10
# Ctrl+C 停止，查看文件

# 等待 5 分钟后再运行
python scripts/run_bot_with_tracking.py -o -i 10
# 比较两次的对象统计
```

### 3. 专注特定对象类型

修改 `run_bot_with_tracking.py` 中的 `get_object_stats()` 函数，添加过滤：

```python
def get_object_stats(limit: int = 10) -> Dict:
    # ...现有代码...
    
    # 只显示特定类型
    filtered_summary = [
        row for row in sum_data 
        if 'YourClassName' in row[0]
    ]
    
    return {
        "summary": filtered_summary[:limit],
        # ...
    }
```

## ⚠️ 注意事项

### 性能影响

对象级分析会影响性能：
- **pympler 分析**: ~10-20% 性能影响
- **gc.collect()**: 每次检查点触发垃圾回收，可能导致短暂卡顿

**建议**：
- 开发/调试时使用对象分析
- 生产环境使用普通监控（不加 `--objects`）

### 内存开销

对象分析本身也会占用内存：
- `muppy.get_objects()` 会创建对象列表
- 统计数据会保存在历史中

**建议**：
- 不要设置过小的 `--interval`（建议 >= 5 秒）
- 长时间运行时考虑关闭对象分析

### 准确性

- 对象统计是**快照**，不是实时的
- `gc.collect()` 后才统计，确保垃圾已回收
- 子进程的对象无法统计（只统计主进程）

## 📚 相关工具

| 工具 | 用途 | 对象级分析 |
|------|------|----------|
| `run_bot_with_tracking.py` | 一键启动+监控 | ✅ 支持 |
| `memory_monitor.py` | 手动监控 | ✅ 支持 |
| `windows_memory_profiler.py` | 详细分析 | ✅ 支持 |
| `run_bot_with_pympler.py` | 专门的对象追踪 | ✅ 专注此功能 |

## 🎓 学习资源

- [Pympler 文档](https://pympler.readthedocs.io/)
- [Python GC 模块](https://docs.python.org/3/library/gc.html)
- [内存泄漏调试技巧](https://docs.python.org/3/library/tracemalloc.html)

---

**快速开始**: 
```powershell
pip install pympler
python scripts/run_bot_with_tracking.py --objects
```
🎉
