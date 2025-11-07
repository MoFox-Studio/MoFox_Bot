# 🎯 记忆图可视化工具 - 快速参考

## 🚀 快速启动

### 推荐方式 (交互式菜单)
```powershell
.\visualizer.ps1
```

然后选择:
- **选项 1**: 独立版 (快速，推荐) ⭐
- **选项 2**: 完整版 (实时数据)
- **选项 3**: 生成示例数据

---

## 📋 各版本对比

| 特性 | 独立版 ⭐ | 完整版 |
|------|---------|--------|
| **启动速度** | 🚀 快速 (2秒) | ⏱️ 较慢 (5-10秒) |
| **数据源** | 📂 文件 | 💾 内存 (实时) |
| **文件切换** | ✅ 支持 | ❌ 不支持 |
| **资源占用** | 💚 低 | 💛 中等 |
| **端口** | 5001 | 5000 |
| **适用场景** | 查看历史数据、调试 | 实时监控、开发 |

---

## 🔧 手动启动命令

### 独立版 (推荐)
```powershell
# Windows
.\start_visualizer.ps1

# 或直接运行
.\.venv\Scripts\python.exe tools/memory_visualizer/visualizer_simple.py
```
访问: http://127.0.0.1:5001

### 完整版
```powershell
.\.venv\Scripts\python.exe tools/memory_visualizer/visualizer_server.py
```
访问: http://127.0.0.1:5000

### 生成示例数据
```powershell
.\.venv\Scripts\python.exe generate_sample_data.py
```

---

## 📊 功能一览

### 🎨 可视化功能
- ✅ 交互式图形 (拖动、缩放、点击)
- ✅ 节点类型颜色分类
- ✅ 实时搜索和过滤
- ✅ 统计信息展示
- ✅ 节点详情查看

### 📂 数据管理
- ✅ 自动搜索数据文件
- ✅ 多文件切换 (独立版)
- ✅ 数据导出 (JSON格式)
- ✅ 文件信息显示

---

## 🎯 使用场景

### 1️⃣ 首次使用
```powershell
# 1. 生成示例数据
.\visualizer.ps1
# 选择: 3

# 2. 启动可视化
.\visualizer.ps1
# 选择: 1

# 3. 打开浏览器
# 访问: http://127.0.0.1:5001
```

### 2️⃣ 查看实际数据
```powershell
# 先运行Bot生成记忆
# 然后启动可视化
.\visualizer.ps1
# 选择: 1 (独立版) 或 2 (完整版)
```

### 3️⃣ 调试记忆系统
```powershell
# 使用完整版，实时查看变化
.\visualizer.ps1
# 选择: 2
```

---

## 🐛 故障排除

### ❌ 问题: 未找到数据文件
**解决**: 
```powershell
.\visualizer.ps1
# 选择 3 生成示例数据
```

### ❌ 问题: 端口被占用
**解决**: 
- 独立版: 修改 `visualizer_simple.py` 中的 `port=5001`
- 完整版: 修改 `visualizer_server.py` 中的 `port=5000`

### ❌ 问题: 数据加载失败
**可能原因**: 
- 数据文件格式不正确
- 文件损坏

**解决**: 
1. 检查 `data/memory_graph/` 目录
2. 重新生成示例数据
3. 查看终端错误信息

---

## 📚 相关文档

- **完整指南**: `VISUALIZER_GUIDE.md`
- **快速入门**: `tools/memory_visualizer/QUICKSTART.md`
- **详细文档**: `tools/memory_visualizer/README.md`
- **更新日志**: `tools/memory_visualizer/CHANGELOG.md`

---

## 💡 提示

1. **首次使用**: 先生成示例数据 (选项 3)
2. **查看历史**: 使用独立版，可以切换不同数据文件
3. **实时监控**: 使用完整版，与Bot同时运行
4. **性能优化**: 大型图使用过滤器和搜索
5. **快捷键**: 
   - `Ctrl + 滚轮`: 缩放
   - 拖动空白: 移动画布
   - 点击节点: 查看详情

---

## 🎉 开始探索！

```powershell
.\visualizer.ps1
```

享受你的记忆图之旅！🚀🦊
