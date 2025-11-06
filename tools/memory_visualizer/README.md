# 🦊 记忆图可视化工具

一个交互式的 Web 可视化工具，用于查看和分析 MoFox Bot 的记忆图结构。

## 📁 目录结构

```
tools/memory_visualizer/
├── visualizer.ps1              # 统一启动脚本（主入口）⭐
├── visualizer_simple.py        # 独立版服务器（推荐）
├── visualizer_server.py        # 完整版服务器
├── generate_sample_data.py     # 测试数据生成器
├── test_visualizer.py          # 测试脚本
├── requirements.txt            # Python 依赖
├── templates/                  # HTML 模板
│   └── visualizer.html        # 可视化界面
├── docs/                       # 文档目录
│   ├── VISUALIZER_README.md
│   ├── VISUALIZER_GUIDE.md
│   └── VISUALIZER_INSTALL_COMPLETE.md
├── README.md                   # 本文件
├── QUICKSTART.md              # 快速开始指南
└── CHANGELOG.md               # 更新日志
```

## 🚀 快速开始

### 方式 1：交互式菜单（推荐）

```powershell
# 在项目根目录运行
.\visualizer.ps1

# 或在工具目录运行
cd tools\memory_visualizer
.\visualizer.ps1
```

### 方式 2：命令行参数

```powershell
# 启动独立版（推荐，快速）
.\visualizer.ps1 -Simple

# 启动完整版（需要 MemoryManager）
.\visualizer.ps1 -Full

# 生成测试数据
.\visualizer.ps1 -Generate

# 运行测试
.\visualizer.ps1 -Test

# 查看帮助
.\visualizer.ps1 -Help
```

## 📊 两个版本的区别

### 独立版（Simple）- 推荐
- ✅ **快速启动**：直接读取数据文件，无需初始化 MemoryManager
- ✅ **轻量级**：只依赖 Flask 和 vis.js
- ✅ **稳定**：不依赖主系统运行状态
- 📌 **端口**：5001
- 📁 **数据源**：`data/memory_graph/*.json`

### 完整版（Full）
- 🔄 **实时数据**：使用 MemoryManager 获取最新数据
- 🔌 **集成**：与主系统深度集成
- ⚡ **功能完整**：支持所有高级功能
- 📌 **端口**：5000
- 📁 **数据源**：MemoryManager

## ✨ 主要功能

1. **交互式图形可视化**
   - 🎨 5 种节点类型（主体、主题、客体、属性、值）
   - 🔗 完整路径高亮显示
   - 🔍 点击节点查看连接关系
   - 📐 自动布局和缩放

2. **高级筛选**
   - ☑️ 按节点类型筛选
   - 🔎 关键词搜索
   - 📊 统计信息实时更新

3. **智能高亮**
   - 💡 点击节点高亮所有连接路径（递归探索）
   - 👻 无关节点变为半透明
   - 🎯 自动聚焦到相关子图

4. **物理引擎优化**
   - 🚀 智能布局算法
   - ⏱️ 自动停止防止持续运行
   - 🔄 筛选后自动重新布局

5. **数据管理**
   - 📂 多文件选择器
   - 💾 导出图形数据
   - 🔄 实时刷新

## 🔧 依赖安装

脚本会自动检查并安装依赖，也可以手动安装：

```powershell
# 激活虚拟环境
.\.venv\Scripts\Activate.ps1

# 安装依赖
pip install -r tools/memory_visualizer/requirements.txt
```

**所需依赖：**
- Flask >= 2.3.0
- flask-cors >= 4.0.0

## 📖 使用说明

### 1. 查看记忆图
1. 启动服务器（推荐独立版）
2. 在浏览器打开 http://127.0.0.1:5001
3. 等待数据加载完成

### 2. 探索连接关系
1. **点击节点**：查看与该节点相关的所有连接路径
2. **点击空白处**：恢复所有节点显示
3. **使用筛选器**：按类型过滤节点

### 3. 搜索记忆
1. 在搜索框输入关键词
2. 点击搜索按钮
3. 相关节点会自动高亮

### 4. 查看统计
- 左侧面板显示实时统计信息
- 节点数、边数、记忆数
- 图密度等指标

## 🎨 节点颜色说明

- 🔴 **主体（SUBJECT）**：红色 (#FF6B6B)
- 🔵 **主题（TOPIC）**：青色 (#4ECDC4)
- 🟦 **客体（OBJECT）**：蓝色 (#45B7D1)
- 🟠 **属性（ATTRIBUTE）**：橙色 (#FFA07A)
- 🟢 **值（VALUE）**：绿色 (#98D8C8)

## 🐛 常见问题

### 问题 1：没有数据显示
**解决方案：**
1. 检查 `data/memory_graph/` 目录是否存在数据文件
2. 运行 `.\visualizer.ps1 -Generate` 生成测试数据
3. 确保 Bot 已经运行过并生成了记忆数据

### 问题 2：物理引擎一直运行
**解决方案：**
- 新版本已修复此问题
- 物理引擎会在稳定后自动停止（最多 5 秒）

### 问题 3：筛选后节点排版错乱
**解决方案：**
- 新版本已修复此问题
- 筛选后会自动重新布局

### 问题 4：无法查看完整连接路径
**解决方案：**
- 新版本使用 BFS 算法递归探索所有连接
- 点击节点即可查看完整路径

## 📝 开发说明

### 添加新功能
1. 编辑 `visualizer_simple.py` 或 `visualizer_server.py`
2. 修改 `templates/visualizer.html` 更新界面
3. 更新 `requirements.txt` 添加新依赖
4. 运行测试：`.\visualizer.ps1 -Test`

### 调试
```powershell
# 启动 Flask 调试模式
$env:FLASK_DEBUG = "1"
python tools/memory_visualizer/visualizer_simple.py
```

## 📚 相关文档

- [快速开始指南](QUICKSTART.md)
- [更新日志](CHANGELOG.md)
- [详细使用指南](docs/VISUALIZER_GUIDE.md)

## 🆘 获取帮助

遇到问题？
1. 查看 [常见问题](#常见问题)
2. 运行 `.\visualizer.ps1 -Help` 查看帮助
3. 查看项目文档目录

## 📄 许可证

与 MoFox Bot 主项目相同
