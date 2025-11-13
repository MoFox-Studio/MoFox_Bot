# Bot 内存分析工具使用指南

一个统一的内存诊断工具，提供进程监控、对象分析和数据可视化功能。

## 🚀 快速开始

> **提示**: 建议使用虚拟环境运行脚本（`.\.venv\Scripts\python.exe`）

```powershell
# 查看帮助
.\.venv\Scripts\python.exe scripts/memory_profiler.py --help

# 进程监控模式（最简单）
.\.venv\Scripts\python.exe scripts/memory_profiler.py --monitor

# 对象分析模式（深度分析）
.\.venv\Scripts\python.exe scripts/memory_profiler.py --objects --output memory_data.txt

# 可视化模式（生成图表）
.\.venv\Scripts\python.exe scripts/memory_profiler.py --visualize --input memory_data.txt.jsonl
```

**或者使用简短命令**（如果你的系统 `python` 已指向虚拟环境）:

```powershell
python scripts/memory_profiler.py --monitor
```

## 📦 依赖安装

```powershell
# 基础功能（进程监控）
pip install psutil

# 对象分析功能
pip install pympler

# 可视化功能
pip install matplotlib

# 一次性安装全部
pip install psutil pympler matplotlib
```

## 🔧 三种模式详解

### 1. 进程监控模式 (--monitor)

**用途**: 从外部监控 bot 进程的总内存、子进程情况

**特点**:
- ✅ 自动启动 bot.py（使用虚拟环境）
- ✅ 实时显示进程内存（RSS、VMS）
- ✅ 列出所有子进程及其内存占用
- ✅ 显示 bot 输出日志
- ✅ 自动保存监控历史

**使用示例**:

```powershell
# 基础用法
python scripts/memory_profiler.py --monitor

# 自定义监控间隔（10秒）
python scripts/memory_profiler.py --monitor --interval 10

# 简写
python scripts/memory_profiler.py -m -i 5
```

**输出示例**:

```
================================================================================
检查点 #1 - 14:23:15
Bot 进程 (PID: 12345)
  RSS: 45.82 MB
  VMS: 12.34 MB
  占比: 0.25%
  子进程: 2 个
  子进程内存: 723.64 MB
  总内存: 769.46 MB

  📋 子进程详情:
    [1] PID 12346: python.exe - 520.15 MB
        命令: python.exe -m chromadb.server ...
    [2] PID 12347: python.exe - 203.49 MB
        命令: python.exe -m uvicorn ...
================================================================================
```

**保存位置**: `data/memory_diagnostics/process_monitor_<timestamp>_pid<PID>.txt`

---

### 2. 对象分析模式 (--objects)

**用途**: 在 bot 进程内部统计所有 Python 对象的内存占用

**特点**:
- ✅ 统计所有对象类型（dict、list、str、AsyncOpenAI 等）
- ✅ **按模块统计内存占用（新增）** - 显示哪个模块占用最多内存
- ✅ 包含所有线程的对象
- ✅ 显示对象变化（diff）
- ✅ 线程信息和 GC 统计
- ✅ 保存 JSONL 数据用于可视化

**使用示例**:

```powershell
# 基础用法（推荐指定输出文件）
python scripts/memory_profiler.py --objects --output memory_data.txt

# 自定义参数
python scripts/memory_profiler.py --objects \
    --interval 10 \
    --output memory_data.txt \
    --object-limit 30

# 简写
python scripts/memory_profiler.py -o -i 10 --output data.txt -l 30
```

**输出示例**:

```
================================================================================
🔍 对象级内存分析 #1 - 14:25:30
================================================================================

📦 对象统计 (前 20 个类型):

类型                                                  数量           总大小
--------------------------------------------------------------------------------
<class 'dict'>                                     125,843         45.23 MB
<class 'str'>                                      234,567         23.45 MB
<class 'list'>                                      56,789         12.34 MB
<class 'tuple'>                                     89,012          8.90 MB
<class 'openai.resources.chat.completions'>            12          5.67 MB
...

📚 模块内存占用 (前 20 个模块):

模块名                                               对象数             总内存
--------------------------------------------------------------------------------
builtins                                         169,144        26.20 MB
src                                               12,345         5.67 MB
openai                                             3,456         2.34 MB
chromadb                                           2,345         1.89 MB
...

  总模块数: 85

🧵 线程信息 (8 个):
  [1] ✓ MainThread
  [2] ✓ AsyncOpenAIClient (守护)
  [3] ✓ ChromaDBWorker (守护)
  ...

🗑️  垃圾回收:
  代 0: 1,234 次
  代 1: 56 次
  代 2: 3 次
  追踪对象: 456,789

📊 总对象数: 567,890
================================================================================
```

**每 3 次迭代会显示对象变化**:

```
📈 对象变化分析:
--------------------------------------------------------------------------------
                types |   # objects |   total size
==================== | =========== | ============
            <class 'dict'> |      +1234 |    +1.23 MB
             <class 'str'> |       +567 |   +0.56 MB
...
--------------------------------------------------------------------------------
```

**保存位置**: 
- 文本: `<output>.txt`
- 结构化数据: `<output>.txt.jsonl`

---

### 3. 可视化模式 (--visualize)

**用途**: 将对象分析模式生成的 JSONL 数据绘制成图表

**特点**:
- ✅ 显示对象类型随时间的内存变化
- ✅ 自动选择内存占用最高的 N 个类型
- ✅ 生成高清 PNG 图表

**使用示例**:

```powershell
# 基础用法
python scripts/memory_profiler.py --visualize \
    --input memory_data.txt.jsonl

# 自定义参数
python scripts/memory_profiler.py --visualize \
    --input memory_data.txt.jsonl \
    --top 15 \
    --plot-output my_plot.png

# 简写
python scripts/memory_profiler.py -v -i data.txt.jsonl -t 15
```

**输出**: PNG 图像，展示前 N 个对象类型的内存占用随时间的变化曲线

**保存位置**: 默认 `memory_analysis_plot.png`，可通过 `--plot-output` 指定

---

## 💡 使用场景

| 场景 | 推荐模式 | 命令 |
|------|----------|------|
| 快速查看总内存 | `--monitor` | `python scripts/memory_profiler.py -m` |
| 查看子进程占用 | `--monitor` | `python scripts/memory_profiler.py -m` |
| 分析具体对象占用 | `--objects` | `python scripts/memory_profiler.py -o --output data.txt` |
| 追踪内存泄漏 | `--objects` | `python scripts/memory_profiler.py -o --output data.txt` |
| 可视化分析趋势 | `--visualize` | `python scripts/memory_profiler.py -v -i data.txt.jsonl` |

## 📊 完整工作流程

### 场景 1: 快速诊断内存问题

```powershell
# 1. 运行进程监控（查看总体情况）
python scripts/memory_profiler.py --monitor --interval 5

# 观察输出，如果发现内存异常，进入场景 2
```

### 场景 2: 深度分析对象占用

```powershell
# 1. 启动对象分析（保存数据）
python scripts/memory_profiler.py --objects \
    --interval 10 \
    --output data/memory_diagnostics/analysis_$(Get-Date -Format 'yyyyMMdd_HHmmss').txt

# 2. 运行一段时间（建议至少 5-10 分钟），按 Ctrl+C 停止

# 3. 生成可视化图表
python scripts/memory_profiler.py --visualize \
    --input data/memory_diagnostics/analysis_<timestamp>.txt.jsonl \
    --top 15 \
    --plot-output data/memory_diagnostics/plot_<timestamp>.png

# 4. 查看图表，分析哪些对象类型随时间增长
```

### 场景 3: 持续监控

```powershell
# 在后台运行对象分析（Windows）
Start-Process powershell -ArgumentList "-Command", "python scripts/memory_profiler.py -o -i 30 --output logs/memory_continuous.txt" -WindowStyle Minimized

# 定期查看 JSONL 并生成图表
python scripts/memory_profiler.py -v -i logs/memory_continuous.txt.jsonl -t 20
```

## 🎯 参数参考

### 通用参数

| 参数 | 简写 | 默认值 | 说明 |
|------|------|--------|------|
| `--interval` | `-i` | 10 | 监控间隔（秒） |

### 对象分析模式参数

| 参数 | 简写 | 默认值 | 说明 |
|------|------|--------|------|
| `--output` | - | 无 | 输出文件路径（强烈推荐） |
| `--object-limit` | `-l` | 20 | 显示的对象类型数量 |

### 可视化模式参数

| 参数 | 简写 | 默认值 | 说明 |
|------|------|--------|------|
| `--input` | - | **必需** | 输入 JSONL 文件路径 |
| `--top` | `-t` | 10 | 展示前 N 个对象类型 |
| `--plot-output` | - | `memory_analysis_plot.png` | 输出图表路径 |

## ⚠️ 注意事项

### 性能影响

| 模式 | 性能影响 | 说明 |
|------|----------|------|
| `--monitor` | < 1% | 几乎无影响，适合生产环境 |
| `--objects` | 5-15% | 有一定影响，建议在测试环境使用 |
| `--visualize` | 0% | 离线分析，无影响 |

### 常见问题

**Q: 对象分析模式报错 "pympler 未安装"？**
```powershell
pip install pympler
```

**Q: 可视化模式报错 "matplotlib 未安装"？**
```powershell
pip install matplotlib
```

**Q: 对象分析模式提示 "bot.py 未找到 main_async() 或 main() 函数"？**

这是正常的。如果你的 bot.py 的主逻辑在 `if __name__ == "__main__":` 中，监控线程仍会在后台运行。你可以：
- 保持 bot 运行，监控会持续统计
- 或者在 bot.py 中添加一个 `main_async()` 或 `main()` 函数

**Q: 进程监控模式看不到子进程？**

确保 bot.py 已经启动了子进程（例如 ChromaDB）。如果刚启动就查看，可能还没有创建子进程。

**Q: JSONL 文件在哪里？**

当你使用 `--output <file>` 时，会生成：
- `<file>`: 人类可读的文本
- `<file>.jsonl`: 结构化数据（用于可视化）

## 📁 输出文件说明

### 进程监控输出

**位置**: `data/memory_diagnostics/process_monitor_<timestamp>_pid<PID>.txt`

**内容**: 每次检查点的进程内存信息

### 对象分析输出

**文本文件**: `<output>`
- 人类可读格式
- 包含每次迭代的对象统计

**JSONL 文件**: `<output>.jsonl`
- 每行一个 JSON 对象
- 包含: timestamp, iteration, total_objects, summary, threads, gc_stats
- 用于可视化分析

### 可视化输出

**PNG 图像**: 默认 `memory_analysis_plot.png`
- 折线图，展示对象类型随时间的内存变化
- 高清 150 DPI

## 🔍 诊断技巧

### 1. 识别内存泄漏

使用对象分析模式运行较长时间，观察：
- 某个对象类型的数量或大小持续增长
- 对象变化 diff 中始终为正数

### 2. 定位大内存对象

**查看对象统计**:
- 如果 `<class 'dict'>` 占用很大，可能是缓存未清理
- 如果看到特定类（如 `AsyncOpenAI`），检查该类的实例数

**查看模块统计**（推荐）:
- 查看 📚 模块内存占用部分
- 如果 `src` 模块占用很大，说明你的代码中有大量对象
- 如果 `openai`、`chromadb` 等第三方模块占用大，可能是这些库的使用问题
- 对比不同时间点，看哪个模块的内存持续增长

### 3. 分析子进程占用

使用进程监控模式：
- 查看子进程详情中的命令行
- 识别哪个子进程占用大量内存（如 ChromaDB）

### 4. 对比不同时间点

使用可视化模式：
- 生成图表后，观察哪些对象类型的曲线持续上升
- 对比不同功能运行时的内存变化

## 🎓 高级用法

### 长期监控脚本

创建 `monitor_continuously.ps1`:

```powershell
# 持续监控脚本
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logPath = "logs/memory_analysis_$timestamp.txt"

Write-Host "开始持续监控，数据保存到: $logPath"
Write-Host "按 Ctrl+C 停止监控"

python scripts/memory_profiler.py --objects --interval 30 --output $logPath
```

### 自动生成日报

创建 `generate_daily_report.ps1`:

```powershell
# 生成内存分析日报
$date = Get-Date -Format "yyyyMMdd"
$jsonlFiles = Get-ChildItem "logs" -Filter "*$date*.jsonl"

foreach ($file in $jsonlFiles) {
    $outputPlot = $file.FullName -replace ".jsonl", "_plot.png"
    python scripts/memory_profiler.py --visualize --input $file.FullName --plot-output $outputPlot --top 20
    Write-Host "生成图表: $outputPlot"
}
```

## 📚 扩展阅读

- **Python 内存管理**: https://docs.python.org/3/c-api/memory.html
- **psutil 文档**: https://psutil.readthedocs.io/
- **Pympler 文档**: https://pympler.readthedocs.io/
- **Matplotlib 文档**: https://matplotlib.org/

## 🆘 获取帮助

```powershell
# 查看完整帮助信息
python scripts/memory_profiler.py --help

# 查看特定模式示例
python scripts/memory_profiler.py --help | Select-String "示例"
```

---

**快速开始提醒**:

```powershell
# 使用虚拟环境（推荐）
.\.venv\Scripts\python.exe scripts/memory_profiler.py --monitor

# 或者使用系统 Python
python scripts/memory_profiler.py --monitor

# 深度分析
.\.venv\Scripts\python.exe scripts/memory_profiler.py --objects --output memory.txt

# 可视化
.\.venv\Scripts\python.exe scripts/memory_profiler.py --visualize --input memory.txt.jsonl
```

### 💡 虚拟环境说明

**Windows**:
```powershell
.\.venv\Scripts\python.exe scripts/memory_profiler.py [选项]
```

**Linux/Mac**:
```bash
./.venv/bin/python scripts/memory_profiler.py [选项]
```

脚本会自动检测并使用项目虚拟环境来启动 bot（进程监控模式），对象分析模式会自动添加项目根目录到 Python 路径。

🎉 现在你已经掌握了完整的内存分析工具！
