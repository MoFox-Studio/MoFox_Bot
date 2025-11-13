# MoFox_Bot AI Coding Agent Instructions

MoFox_Bot 是基于 MaiCore 的增强型 QQ 聊天机器人，集成了 LLM、插件系统、记忆图谱、情感系统等高级特性。本指南帮助 AI 代理快速理解项目架构并高效开发。

## 🏗️ 核心架构

### 应用启动流程
- **入口点**: `bot.py` → `src/main.py` 的 `MainSystem` 类
- **启动顺序**: EULA 检查 → 数据库初始化 → 插件加载 → 组件初始化 → WebUI 启动（可选）
- **关键管理器**: 所有管理器通过单例模式获取（如 `get_xxx_manager()`）

### 六层数据库架构
项目在 2025年11月重构了完整的数据库层，采用 **SQLAlchemy 2.0**：

1. **Core Layer** (`src/common/database/core.py`): `DatabaseEngine` 单例、WAL 模式 SQLite、连接池管理
2. **API Layer** (`src/common/database/api/`): `CRUDBase` 通用 CRUD、`QueryBuilder` 链式查询、`specialized.py` 业务特化 API
3. **Optimization Layer** (`src/common/database/optimization/`): 3级缓存 (L1内存/L2 SQLite/L3预加载)、`IntelligentPreloader`、`AdaptiveBatchScheduler`
4. **Config Layer** (`src/common/database/config/`): 数据库/缓存/预加载器配置
5. **Utils Layer** (`src/common/database/utils/`): 装饰器（重试、超时、缓存）、性能监控
6. **Compatibility Layer** (`src/common/database/compatibility/`): 向后兼容旧 API（`db_query`、`db_save` 等）

**关键原则**:
- ✅ 新代码使用 `CRUDBase` 或 `QueryBuilder`
- ✅ 批量操作使用 `AdaptiveBatchScheduler`
- ⚠️ 避免直接使用 `Session`，使用提供的 API 层
- ⚠️ 数据模型在 `src/common/database/sqlalchemy_models.py` 统一定义

### 插件系统架构
**核心概念**: 组件化设计，插件包含多个可注册组件

**组件类型** (`src/plugin_system/base/component_types.py`):
- `ACTION`: 主动/被动行为（回复、发送表情、禁言等）
- `COMMAND`: 命令处理（传统 `/` 前缀命令）
- `PLUS_COMMAND`: 增强命令（支持参数解析、权限检查）
- `TOOL`: LLM 工具调用（函数调用集成）
- `EVENT_HANDLER`: 事件订阅处理器
- `INTEREST_CALCULATOR`: 兴趣值计算器
- `PROMPT`: 自定义提示词注入

**插件开发流程**:
1. 在 `plugins/` 下创建目录，编写 `_manifest.json`
2. 创建 `plugin.py`，继承 `BasePlugin` 或 `PlusPlugin`
3. 使用 `@register_plugin` 装饰器注册
4. 实现 `get_plugin_components()` 返回组件列表
5. 组件通过 `ComponentRegistry` 自动注册

**示例结构**:
```python
from src.plugin_system import BasePlugin, register_plugin, BaseAction

@register_plugin
class MyPlugin(BasePlugin):
    plugin_name = "my_plugin"
    enable_plugin = True
    
    def get_plugin_components(self):
        return [(ActionInfo(...), MyAction)]
```

**关键 API** (`src/plugin_system/apis/`):
- `chat_api`: 聊天功能（获取消息、发送消息）
- `database_api`: 数据库操作（推荐使用新 API）
- `llm_api`: LLM 交互（模型调用、工具注册）
- `permission_api`: 权限管理（检查权限、节点操作）
- `component_manage_api`: 组件查询与管理

### 统一调度器（Unified Scheduler）
**位置**: `src/schedule/unified_scheduler.py`

**触发类型**:
- `TIME`: 延迟触发（`delay_seconds`）或指定时间（`trigger_at`）
- `EVENT`: 事件触发（基于 `event_manager`）
- `CUSTOM`: 自定义条件函数

**使用模式**:
```python
from src.schedule.unified_scheduler import unified_scheduler, TriggerType

await unified_scheduler.create_schedule(
    callback=my_async_function,
    trigger_type=TriggerType.TIME,
    trigger_config={"delay_seconds": 30},
    is_recurring=True,
    task_name="periodic_task"
)
```

⚠️ **自动启动**: 调度器在 `MainSystem.initialize()` 中自动启动，无需手动初始化

### 记忆系统架构
**双轨记忆**:
- **Memory Graph** (`src/memory_graph/`): 基于图的持久记忆（人物、事件、关系）
- **Chat Memory** (`src/chat/memory_system/`): 会话上下文记忆

**兴趣值系统** (`src/chat/interest_system/`):
- 通过插件自动注册 `InterestCalculator` 组件
- 支持主题聚类、时间衰减、动态权重
- 影响 AFC (Affinity Flow Chatter) 对话策略

**关系系统** (`src/person_info/`):
- 亲密度值影响回复风格和语气
- 与兴趣值系统协同工作

## 🛠️ 开发工作流

### 环境管理
**首选**: `uv` 包管理器（配置清华镜像）
```powershell
uv venv
uv pip install -r requirements.txt
```

**环境配置**:
1. 复制 `template/template.env` → `.env`
2. 设置 `EULA_CONFIRMED=true`
3. 编辑 `config/bot_config.toml` 和 `config/model_config.toml`

### 代码质量
**Linter**: Ruff（配置在 `pyproject.toml`）
```powershell
ruff check .      # 检查
ruff format .     # 格式化
```

**规范**:
- 行长度: 120 字符
- 引号: 双引号
- 类型提示: 推荐使用（尤其是公共 API）
- 异步优先: 所有 I/O 操作使用 `async/await`

### 日志系统
**位置**: `src/common/logger.py`

**使用模式**:
```python
from src.common.logger import get_logger

logger = get_logger("module_name")
logger.info("信息")
logger.error("错误", exc_info=True)  # 包含堆栈跟踪
```

**日志级别**: 通过 `bot_config.toml` 的 `[logging]` 配置

### 运行与调试
**启动命令**:
```powershell
python bot.py              # 标准启动
python __main__.py         # 备用入口
```

**WebUI 开发**:
- WebUI 位于同级目录 `webui/` 或 `../webui`
- 自动通过 `npm run dev` 启动（可在 `.env` 设置 `WEBUI_DIR`）
- 超时 60 秒检测是否成功

**调试技巧**:
- 检查 `logs/app_*.jsonl` 结构化日志
- 使用 `get_errors()` 工具查看编译错误
- 数据库问题：查看 `data/MaiBot.db`（SQLite）或 MySQL 连接

## 📋 关键约定与模式

### 配置管理
**全局配置**: `src/config/config.py` 的 `global_config` 单例
- 通过 TOML 文件驱动（`config/bot_config.toml`）
- 支持环境变量覆盖（`.env`）
- 数据库类型切换：`database.database_type = "sqlite" | "mysql"`

### 事件系统
**Event Manager** (`src/plugin_system/core/event_manager.py`):
```python
from src.plugin_system.core.event_manager import event_manager
from src.plugin_system.base.component_types import EventType

await event_manager.trigger_event(
    EventType.ON_MESSAGE_RECEIVED,
    message_data=data,
    permission_group="USER"
)
```

**常用事件**:
- `ON_START` / `ON_STOP`: 系统生命周期
- `ON_MESSAGE_RECEIVED`: 消息接收
- `ON_PLUGIN_LOADED` / `ON_PLUGIN_UNLOADED`: 插件生命周期

### 消息处理
**核心类**: `ChatBot` (`src/chat/message_receive/bot.py`)
- 消息通过 `_message_process_wrapper` 异步并行处理
- 使用 `MessageStorageBatcher` 批量存储（`src/chat/message_receive/storage.py`）
- 消息分块重组: `MessageReassembler` (`src/utils/message_chunker.py`)

### 批量操作最佳实践
**场景**: 需要保存大量数据库记录
```python
from src.common.database.optimization.batch_scheduler import get_batch_scheduler

scheduler = get_batch_scheduler()
await scheduler.schedule_batch_insert(model_class, data_list)
```

### 权限系统
**检查权限**:
```python
from src.plugin_system.apis.permission_api import permission_api

has_permission = await permission_api.check_permission(
    user_id="123456",
    platform="qq",
    permission_node="plugin.my_plugin.admin"
)
```

**Master 用户**: 在 `bot_config.toml` 的 `[permission.master_users]` 配置

## 🔍 常见问题与陷阱

### 数据库相关
❌ **错误**: 直接创建 `Session` 对象
✅ **正确**: 使用 `CRUDBase` 或 `QueryBuilder` API

❌ **错误**: 循环中逐条插入
✅ **正确**: 使用 `AdaptiveBatchScheduler` 批量插入

### 插件开发
❌ **错误**: 在 `__init__` 中执行异步操作
✅ **正确**: 在 `on_plugin_loaded()` 中执行异步初始化

❌ **错误**: 硬编码配置值
✅ **正确**: 使用 `self.plugin_config` 读取配置

### 性能优化
⚠️ **避免**: 在主事件循环中阻塞 I/O
✅ **使用**: `asyncio.to_thread()` 或 `loop.run_in_executor()`

⚠️ **避免**: 频繁的小查询
✅ **使用**: 预加载、缓存或批量查询

## 📚 关键文档参考

- **插件开发**: `docs/plugins/quick-start.md`
- **数据库架构**: `docs/database_refactoring_completion.md`
- **统一调度器**: `docs/unified_scheduler_guide.md`
- **记忆图谱**: `docs/memory_graph_guide.md`
- **部署指南**: `docs/deployment_guide.md`
- **配置说明**: 在线文档 https://mofox-studio.github.io/MoFox-Bot-Docs/

## 🎯 快速定位关键文件

| 功能域 | 入口文件 |
|--------|----------|
| 主系统 | `src/main.py` |
| 插件管理器 | `src/plugin_system/core/plugin_manager.py` |
| 数据库 API | `src/common/database/api/crud.py` |
| 消息处理 | `src/chat/message_receive/bot.py` |
| LLM 集成 | `src/llm_models/model_client/` |
| 配置系统 | `src/config/config.py` |
| 日志系统 | `src/common/logger.py` |

---

**项目特色**: 本项目集成了 MCP (Model Context Protocol) 支持、Affinity Flow Chatter 智能对话、视频分析、日程管理等独特功能。探索 `src/plugins/built_in/` 查看内置插件示例。
