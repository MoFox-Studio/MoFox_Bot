# 记忆图可视化工具 - 快速入门指南

## 🎯 方案选择

我为你创建了**两个版本**的可视化工具:

### 1️⃣ 独立版 (推荐 ⭐)
- **文件**: `tools/memory_visualizer/visualizer_simple.py`
- **优点**: 
  - 直接读取存储文件,无需初始化完整系统
  - 启动快速
  - 占用资源少
- **适用**: 快速查看已有记忆数据

### 2️⃣ 完整版
- **文件**: `tools/memory_visualizer/visualizer_server.py`
- **优点**:
  - 实时数据
  - 支持更多功能
- **缺点**: 
  - 需要完整初始化记忆管理器
  - 启动较慢

## 🚀 快速开始

### 步骤 1: 安装依赖

**Windows (PowerShell):**
```powershell
# 依赖会自动检查和安装
.\start_visualizer.ps1
```

**Windows (CMD):**
```cmd
start_visualizer.bat
```

**Linux/Mac:**
```bash
chmod +x start_visualizer.sh
./start_visualizer.sh
```

**手动安装依赖:**
```bash
# 使用虚拟环境
.\.venv\Scripts\python.exe -m pip install flask flask-cors

# 或全局安装
pip install flask flask-cors
```

### 步骤 2: 确保有数据

如果还没有记忆数据,可以:

**选项A**: 运行Bot生成实际数据
```bash
python bot.py
# 与Bot交互一会儿,让它积累一些记忆
```

**选项B**: 生成测试数据 (如果测试脚本可用)
```bash
python test_visualizer.py
# 选择选项 1: 生成测试数据
```

### 步骤 3: 启动可视化服务器

**方式一: 使用启动脚本 (推荐 ⭐)**

Windows PowerShell:
```powershell
.\start_visualizer.ps1
```

Windows CMD:
```cmd
start_visualizer.bat
```

Linux/Mac:
```bash
./start_visualizer.sh
```

**方式二: 手动启动**

使用虚拟环境:
```bash
# Windows
.\.venv\Scripts\python.exe tools/memory_visualizer/visualizer_simple.py

# Linux/Mac
.venv/bin/python tools/memory_visualizer/visualizer_simple.py
```

或使用系统Python:
```bash
python tools/memory_visualizer/visualizer_simple.py
```

服务器将在 http://127.0.0.1:5001 启动

### 步骤 4: 打开浏览器

访问对应的地址,开始探索记忆图! 🎉

## 🎨 界面功能

### 左侧栏

1. **🔍 搜索框**
   - 输入关键词搜索相关记忆
   - 结果会在图中高亮显示

2. **📊 统计信息**
   - 节点总数
   - 边总数
   - 记忆总数
   - 图密度

3. **🎨 节点类型图例**
   - 🔴 主体 (SUBJECT) - 记忆的主语
   - 🔵 主题 (TOPIC) - 动作或状态
   - 🟢 客体 (OBJECT) - 宾语
   - 🟠 属性 (ATTRIBUTE) - 延伸属性
   - 🟣 值 (VALUE) - 属性的具体值

4. **🔧 过滤器**
   - 勾选/取消勾选来显示/隐藏特定类型的节点
   - 实时更新图形

5. **ℹ️ 节点信息**
   - 点击任意节点查看详细信息
   - 显示节点类型、内容、创建时间等

### 右侧主区域

1. **控制按钮**
   - 🔄 刷新图形: 重新加载最新数据
   - 📐 适应窗口: 自动调整图形大小
   - 💾 导出数据: 下载JSON格式的图数据

2. **交互式图形**
   - **拖动节点**: 点击并拖动单个节点
   - **拖动画布**: 按住空白处拖动整个图形
   - **缩放**: 使用鼠标滚轮放大/缩小
   - **点击节点**: 查看详细信息
   - **物理模拟**: 节点会自动排列,避免重叠

## 🎮 操作技巧

### 查看特定类型的节点
1. 在左侧过滤器中取消勾选不需要的类型
2. 图形会自动更新,只显示选中的类型

### 查找特定记忆
1. 在搜索框输入关键词(如: "小明", "吃饭")
2. 点击"搜索"按钮
3. 相关节点会被选中并自动聚焦

### 整理混乱的图形
1. 点击"适应窗口"按钮
2. 或者刷新页面重新初始化布局

### 导出数据进行分析
1. 点击"导出数据"按钮
2. JSON文件会自动下载
3. 可以用于进一步的数据分析或备份

## 🎯 示例场景

### 场景1: 了解记忆图整体结构
1. 启动可视化工具
2. 观察不同颜色的节点分布
3. 查看统计信息了解数量
4. 使用过滤器逐个类型查看

### 场景2: 追踪特定主题的记忆
1. 在搜索框输入主题关键词(如: "学习")
2. 点击搜索
3. 查看高亮的相关节点
4. 点击节点查看详情

### 场景3: 调试记忆系统
1. 创建一条新记忆
2. 刷新可视化页面
3. 查看新节点和边是否正确创建
4. 验证节点类型和关系

## 🐛 常见问题

### Q: 页面显示空白或没有数据?
**A**: 
1. 检查是否有记忆数据: 查看 `data/memory_graph/` 目录
2. 确保记忆系统已启用: 检查 `config/bot_config.toml` 中 `[memory] enable = true`
3. 尝试生成一些测试数据

### Q: 节点太多,看不清楚?
**A**:
1. 使用过滤器只显示某些类型
2. 使用搜索功能定位特定节点
3. 调整浏览器窗口大小,点击"适应窗口"

### Q: 如何更新数据?
**A**:
- **独立版**: 点击"刷新图形"或访问 `/api/reload`
- **完整版**: 点击"刷新图形"会自动加载最新数据

### Q: 端口被占用怎么办?
**A**: 修改启动脚本中的端口号:
```python
run_server(host='127.0.0.1', port=5002, debug=True)  # 改为其他端口
```

## 🎨 自定义配置

### 修改节点颜色

编辑 `templates/visualizer.html`,找到:

```javascript
const nodeColors = {
    'SUBJECT': '#FF6B6B',    // 改为你喜欢的颜色
    'TOPIC': '#4ECDC4',
    // ...
};
```

### 修改物理引擎参数

在同一文件中找到 `physics` 配置:

```javascript
physics: {
    barnesHut: {
        gravitationalConstant: -8000,  // 调整引力
        springLength: 150,             // 调整弹簧长度
        // ...
    }
}
```

### 修改数据加载限制

编辑对应的服务器文件,修改 `get_all_memories()` 的limit参数。

## 📝 文件结构

```
tools/memory_visualizer/
├── README.md                    # 详细文档
├── requirements.txt             # 依赖列表
├── visualizer_server.py         # 完整版服务器
├── visualizer_simple.py         # 独立版服务器 ⭐
└── templates/
    └── visualizer.html          # Web界面模板

run_visualizer.py                # 快速启动脚本
test_visualizer.py              # 测试和演示脚本
```

## 🚀 下一步

现在你可以:

1. ✅ 启动可视化工具查看现有数据
2. ✅ 与Bot交互生成更多记忆
3. ✅ 使用可视化工具验证记忆结构
4. ✅ 根据需要自定义样式和配置

祝你使用愉快! 🎉

---

如有问题,请查看 `tools/memory_visualizer/README.md` 获取更多帮助。
