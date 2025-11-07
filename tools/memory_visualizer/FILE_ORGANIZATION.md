# 📁 可视化工具文件整理完成

## ✅ 整理结果

### 新的目录结构

```
tools/memory_visualizer/
├── visualizer.ps1              ⭐ 统一启动脚本（主入口）
├── visualizer_simple.py        # 独立版服务器
├── visualizer_server.py        # 完整版服务器
├── generate_sample_data.py     # 测试数据生成器
├── test_visualizer.py          # 测试脚本
├── run_visualizer.py           # Python 运行脚本（独立版）
├── run_visualizer_simple.py    # Python 运行脚本（简化版）
├── start_visualizer.bat        # Windows 批处理启动脚本
├── start_visualizer.ps1        # PowerShell 启动脚本
├── start_visualizer.sh         # Linux/Mac 启动脚本
├── requirements.txt            # Python 依赖
├── templates/                  # HTML 模板
│   └── visualizer.html        # 可视化界面
├── docs/                       # 文档目录
│   ├── VISUALIZER_README.md
│   ├── VISUALIZER_GUIDE.md
│   └── VISUALIZER_INSTALL_COMPLETE.md
├── README.md                   # 主说明文档
├── QUICKSTART.md              # 快速开始指南
└── CHANGELOG.md               # 更新日志
```

### 根目录保留文件

```
项目根目录/
├── visualizer.ps1              # 快捷启动脚本（指向 tools/memory_visualizer/visualizer.ps1）
└── tools/memory_visualizer/    # 所有可视化工具文件
```

## 🚀 使用方法

### 推荐方式：使用统一启动脚本

```powershell
# 在项目根目录
.\visualizer.ps1

# 或在工具目录
cd tools\memory_visualizer
.\visualizer.ps1
```

### 命令行参数

```powershell
# 直接启动独立版（推荐）
.\visualizer.ps1 -Simple

# 启动完整版
.\visualizer.ps1 -Full

# 生成测试数据
.\visualizer.ps1 -Generate

# 运行测试
.\visualizer.ps1 -Test
```

## 📋 整理内容

### 已移动的文件

从项目根目录移动到 `tools/memory_visualizer/`：

1. **脚本文件**
   - `generate_sample_data.py`
   - `run_visualizer.py`
   - `run_visualizer_simple.py`
   - `test_visualizer.py`
   - `start_visualizer.bat`
   - `start_visualizer.ps1`
   - `start_visualizer.sh`
   - `visualizer.ps1`

2. **文档文件** → `docs/` 子目录
   - `VISUALIZER_GUIDE.md`
   - `VISUALIZER_INSTALL_COMPLETE.md`
   - `VISUALIZER_README.md`

### 已创建的新文件

1. **统一启动脚本**
   - `tools/memory_visualizer/visualizer.ps1` - 功能齐全的统一入口

2. **快捷脚本**
   - `visualizer.ps1`（根目录）- 快捷方式，指向实际脚本

3. **更新的文档**
   - `tools/memory_visualizer/README.md` - 更新为反映新结构

## 🎯 优势

### 整理前的问题
- ❌ 文件散落在根目录
- ❌ 多个启动脚本功能重复
- ❌ 文档分散不便管理
- ❌ 不清楚哪个是主入口

### 整理后的改进
- ✅ 所有文件集中在 `tools/memory_visualizer/`
- ✅ 单一统一的启动脚本 `visualizer.ps1`
- ✅ 文档集中在 `docs/` 子目录
- ✅ 清晰的主入口和快捷方式
- ✅ 更好的可维护性

## 📝 功能对比

### 旧的方式（整理前）
```powershell
# 需要记住多个脚本名称
.\start_visualizer.ps1
.\run_visualizer.py
.\run_visualizer_simple.py
.\generate_sample_data.py
```

### 新的方式（整理后）
```powershell
# 只需要一个统一的脚本
.\visualizer.ps1              # 交互式菜单
.\visualizer.ps1 -Simple      # 启动独立版
.\visualizer.ps1 -Generate    # 生成数据
.\visualizer.ps1 -Test        # 运行测试
```

## 🔧 维护说明

### 添加新功能
1. 在 `tools/memory_visualizer/` 目录下添加新文件
2. 如需启动选项，在 `visualizer.ps1` 中添加新参数
3. 更新 `README.md` 文档

### 更新文档
1. 主文档：`tools/memory_visualizer/README.md`
2. 详细文档：`tools/memory_visualizer/docs/`

## ✅ 测试结果

- ✅ 统一启动脚本正常工作
- ✅ 独立版服务器成功启动（端口 5001）
- ✅ 数据加载成功（725 节点，769 边）
- ✅ Web 界面正常访问
- ✅ 所有文件已整理到位

## 📚 相关文档

- [README](tools/memory_visualizer/README.md) - 主要说明文档
- [QUICKSTART](tools/memory_visualizer/QUICKSTART.md) - 快速开始指南  
- [CHANGELOG](tools/memory_visualizer/CHANGELOG.md) - 更新日志
- [详细指南](tools/memory_visualizer/docs/VISUALIZER_GUIDE.md) - 完整使用指南

---

整理完成时间：2025-11-06
