# 统一调度器 (Unified Scheduler)

## 概述

统一调度器是一个为 MoFox Bot 设计的通用任务调度系统，主要服务于插件系统。它提供了一个简单而强大的接口来创建和管理各种类型的定时任务。

### 核心特性

- **每秒检查机制**: 调度器在后台每秒检查一次所有任务，确保及时触发
- **三种触发类型**: 支持时间触发、事件触发和自定义条件触发
- **循环与一次性**: 支持循环任务和一次性任务
- **任务管理**: 提供完整的API来创建、删除、暂停、恢复和强制触发任务
- **线程安全**: 使用异步锁保证并发安全
- **自动清理**: 一次性任务执行后自动移除
- **Event Manager 集成**: 与 event_manager 直接集成，事件触发更高效

### 自动启动

统一调度器已集成到主系统中，会在 Bot 启动时自动启动，在 Bot 关闭时自动清理，**无需手动启动或停止**。

## 快速开始

### 基本使用

由于调度器已自动启动，你可以直接使用它：

```python
from src.schedule.unified_scheduler import unified_scheduler, TriggerType

async def my_callback():
    print("任务执行了！")

# 创建一个5秒后执行的任务
schedule_id = await unified_scheduler.create_schedule(
    callback=my_callback,
    trigger_type=TriggerType.TIME,
    trigger_config={"delay_seconds": 5},
    task_name="我的第一个任务"
)
```

> **提示**: 如果需要手动控制，可以使用 `initialize_scheduler()` 和 `shutdown_scheduler()` 函数。

## 触发类型详解

### 1. 时间触发 (TIME)

时间触发支持两种模式：

#### 延迟触发
```python
# 5秒后执行一次
await unified_scheduler.create_schedule(
    callback=my_callback,
    trigger_type=TriggerType.TIME,
    trigger_config={"delay_seconds": 5},
    task_name="延迟任务"
)

# 每30秒执行一次（循环）
await unified_scheduler.create_schedule(
    callback=my_callback,
    trigger_type=TriggerType.TIME,
    trigger_config={"delay_seconds": 30},
    is_recurring=True,
    task_name="周期任务"
)
```

#### 指定时间点触发
```python
from datetime import datetime, timedelta

# 在指定时间执行一次
target_time = datetime.now() + timedelta(hours=1)
await unified_scheduler.create_schedule(
    callback=my_callback,
    trigger_type=TriggerType.TIME,
    trigger_config={"trigger_at": target_time},
    task_name="定时任务"
)

# 每天固定时间执行（循环）
await unified_scheduler.create_schedule(
    callback=my_callback,
    trigger_type=TriggerType.TIME,
    trigger_config={
        "trigger_at": target_time,
        "interval_seconds": 86400  # 24小时
    },
    is_recurring=True,
    task_name="每日任务"
)
```

### 2. 事件触发 (EVENT)

事件触发允许任务订阅特定事件，当事件发生时自动执行。**事件系统与 event_manager 直接集成**，通过高效的回调机制实现零延迟的事件通知。

#### 工作原理

1. 创建 EVENT 类型任务时，调度器会追踪该事件
2. 当通过 `event_manager.trigger_event()` 触发事件时，event_manager 会**直接调用**调度器的回调
3. 调度器查找所有订阅该事件的任务并立即执行
4. 无需 Handler 中间层，效率更高

#### 创建事件监听任务

```python
from src.schedule.unified_scheduler import unified_scheduler, TriggerType

async def on_user_login(user_id: int, username: str):
    print(f"用户登录: {username} (ID: {user_id})")

# 订阅 user_login 事件
schedule_id = await unified_scheduler.create_schedule(
    callback=on_user_login,
    trigger_type=TriggerType.EVENT,
    trigger_config={"event_name": "user_login"},
    is_recurring=True,  # 循环任务可以多次触发
    task_name="登录监听器"
)
```

#### 触发事件

**重要**: 事件触发**必须**通过 `event_manager` 进行：

```python
from src.plugin_system.core.event_manager import event_manager

# 触发事件，所有订阅该事件的调度任务都会被执行
await event_manager.trigger_event(
    "user_login",
    permission_group="SYSTEM",  # 或插件名称
    user_id=123,
    username="张三"
)
```

**工作流程**:
1. 调用 `event_manager.trigger_event("user_login", ...)`
2. Event manager 检测到 scheduler 已注册回调
3. Event manager **直接调用** scheduler 的 `_handle_event_trigger()` 方法
4. Scheduler 查找所有订阅 "user_login" 的任务
5. 立即执行这些任务的回调函数，并传入事件参数

**参数说明**:
- 第一个参数: 事件名称（必需）
- `permission_group`: 用于权限验证（可选，系统事件使用 "SYSTEM"）
- 其他参数: 会作为 `**kwargs` 传递给所有订阅该事件的回调函数

#### 事件自动管理

- **自动追踪**: 创建 EVENT 类型任务时，调度器会自动追踪该事件
- **直接通知**: Event manager 触发事件时会直接通知调度器，无中间层
- **自动清理**: 移除最后一个订阅某事件的任务时，自动停止追踪该事件
- **零延迟**: 使用直接回调机制，事件触发到任务执行几乎无延迟

### 3. 自定义触发 (CUSTOM)

自定义触发允许你提供一个判断函数，调度器会每秒执行这个函数，当返回 `True` 时触发任务。

```python
# 定义条件函数
def check_condition():
    # 这里可以是任何自定义逻辑
    return some_variable > threshold

async def on_condition_met():
    print("条件满足了！")

# 创建自定义条件任务
await unified_scheduler.create_schedule(
    callback=on_condition_met,
    trigger_type=TriggerType.CUSTOM,
    trigger_config={"condition_func": check_condition},
    task_name="自定义条件任务"
)
```

⚠️ **注意**: 条件函数会每秒执行一次，避免在其中执行耗时操作。

## 任务管理 API

### 移除任务
```python
success = await unified_scheduler.remove_schedule(schedule_id)
```

### 暂停任务
```python
# 暂停任务（保留但不触发）
success = await unified_scheduler.pause_schedule(schedule_id)
```

### 恢复任务
```python
# 恢复已暂停的任务
success = await unified_scheduler.resume_schedule(schedule_id)
```

### 强制触发任务
```python
# 立即执行任务（不等待触发条件）
success = await unified_scheduler.trigger_schedule(schedule_id)
```

### 获取任务信息
```python
# 获取单个任务的详细信息
task_info = await unified_scheduler.get_task_info(schedule_id)
print(task_info)
# {
#     "schedule_id": "...",
#     "task_name": "...",
#     "trigger_type": "time",
#     "is_recurring": False,
#     "is_active": True,
#     "created_at": "2025-10-27T10:00:00",
#     "last_triggered_at": None,
#     "trigger_count": 0,
#     "trigger_config": {...}
# }
```

### 列出所有任务
```python
# 列出所有任务
all_tasks = await unified_scheduler.list_tasks()

# 列出特定类型的任务
time_tasks = await unified_scheduler.list_tasks(trigger_type=TriggerType.TIME)
```

### 获取统计信息
```python
stats = unified_scheduler.get_statistics()
print(stats)
# {
#     "is_running": True,
#     "total_tasks": 10,
#     "active_tasks": 8,
#     "paused_tasks": 2,
#     "recurring_tasks": 5,
#     "one_time_tasks": 5,
#     "tasks_by_type": {
#         "time": 6,
#         "event": 3,
#         "custom": 1
#     },
#     "registered_events": ["user_login", "message_received"]
# }
```

## 回调函数

### 同步和异步回调
调度器支持同步和异步回调函数：

```python
# 异步回调
async def async_callback():
    await some_async_operation()

# 同步回调
def sync_callback():
    print("同步执行")

# 两种都可以使用
await unified_scheduler.create_schedule(
    callback=async_callback,  # 或 sync_callback
    trigger_type=TriggerType.TIME,
    trigger_config={"delay_seconds": 5}
)
```

### 带参数的回调
```python
async def callback_with_params(user_id: int, message: str):
    print(f"用户 {user_id}: {message}")

# 使用 callback_args 和 callback_kwargs 传递参数
await unified_scheduler.create_schedule(
    callback=callback_with_params,
    trigger_type=TriggerType.TIME,
    trigger_config={"delay_seconds": 5},
    callback_args=(123,),
    callback_kwargs={"message": "你好"}
)
```

## 在插件中使用

插件中使用调度器的典型模式：

```python
from src.plugin_system.plugin_base import PluginBase
from src.schedule.unified_scheduler import TriggerType, unified_scheduler

class MyPlugin(PluginBase):
    def __init__(self):
        super().__init__(...)
        self.schedule_ids = []  # 保存所有任务ID
    
    async def on_enable(self):
        """插件启动时创建任务"""
        # 创建定时任务
        id1 = await unified_scheduler.create_schedule(
            callback=self._my_task,
            trigger_type=TriggerType.TIME,
            trigger_config={"delay_seconds": 60},
            is_recurring=True,
            task_name=f"{self.meta.name}_periodic_task"
        )
        self.schedule_ids.append(id1)
        
        # 创建事件监听
        id2 = await unified_scheduler.create_schedule(
            callback=self._on_event,
            trigger_type=TriggerType.EVENT,
            trigger_config={"event_name": "my_event"},
            is_recurring=True,
            task_name=f"{self.meta.name}_event_listener"
        )
        self.schedule_ids.append(id2)
    
    async def on_disable(self):
        """插件停止时清理任务"""
        for schedule_id in self.schedule_ids:
            await unified_scheduler.remove_schedule(schedule_id)
        self.schedule_ids.clear()
    
    async def _my_task(self):
        """定时任务回调函数"""
        self.logger.info("执行定时任务")
    
    async def _on_event(self, **event_params):
        """事件回调函数"""
        self.logger.info(f"收到事件: {event_params}")
```

### 最佳实践

1. **命名规范**: 使用插件名称作为任务名称前缀，便于识别和调试
   ```python
   task_name=f"{self.meta.name}_task_description"
   ```

2. **保存ID**: 在插件中保存所有创建的 `schedule_id`，方便管理
   ```python
   self.schedule_ids = []
   self.schedule_ids.append(schedule_id)
   ```

3. **及时清理**: 在 `on_disable()` 中移除所有任务，避免内存泄漏
   ```python
   async def on_disable(self):
       for sid in self.schedule_ids:
           await unified_scheduler.remove_schedule(sid)
       self.schedule_ids.clear()
   ```

4. **异常处理**: 在回调函数中做好异常处理，避免影响调度器
   ```python
   async def my_callback(self):
       try:
           # 任务逻辑
           pass
       except Exception as e:
           self.logger.error(f"任务执行失败: {e}", exc_info=True)
   ```

5. **性能考虑**: 
   - CUSTOM 类型的条件函数会每秒执行，避免耗时操作
   - 优先使用 EVENT 类型替代频繁的条件检查
   - 事件触发使用直接回调，效率最高

6. **事件命名**: 使用清晰的事件命名，避免冲突
   ```python
   event_name = f"{self.meta.name}_custom_event"
   ```

## 使用场景示例

### 定时提醒
```python
async def send_reminder():
    await send_message("该喝水了！")

# 每小时提醒一次
await unified_scheduler.create_schedule(
    callback=send_reminder,
    trigger_type=TriggerType.TIME,
    trigger_config={"delay_seconds": 3600},
    is_recurring=True,
    task_name="喝水提醒"
)
```

### 监听消息事件
```python
from src.plugin_system.core.event_manager import event_manager
from src.schedule.unified_scheduler import unified_scheduler, TriggerType

async def on_new_message(content: str, sender: str):
    # 处理新消息
    print(f"收到来自 {sender} 的消息: {content}")

# 订阅消息事件
await unified_scheduler.create_schedule(
    callback=on_new_message,
    trigger_type=TriggerType.EVENT,
    trigger_config={"event_name": "new_message"},
    is_recurring=True,
    task_name="消息处理器"
)

# 在其他地方触发事件（通过 event_manager）
await event_manager.trigger_event(
    "new_message",
    permission_group="SYSTEM",
    content="你好！",
    sender="用户A"
)
```

> **注意**: 事件触发必须通过 `event_manager.trigger_event()`，这样才能触发调度器中的事件任务。

### 条件监控
```python
import os

def check_file_exists():
    return os.path.exists("/tmp/signal.txt")

async def on_file_created():
    print("检测到信号文件！")
    os.remove("/tmp/signal.txt")

# 监控文件创建
await unified_scheduler.create_schedule(
    callback=on_file_created,
    trigger_type=TriggerType.CUSTOM,
    trigger_config={"condition_func": check_file_exists},
    task_name="文件监控"
)
```

### 每日总结
```python
from datetime import datetime, time, timedelta

async def daily_summary():
    # 生成每日总结
    summary = generate_summary()
    await send_message(summary)

# 每天晚上10点执行
now = datetime.now()
target = datetime.combine(now.date(), time(22, 0))
if target <= now:
    target += timedelta(days=1)

await unified_scheduler.create_schedule(
    callback=daily_summary,
    trigger_type=TriggerType.TIME,
    trigger_config={
        "trigger_at": target,
        "interval_seconds": 86400  # 24小时
    },
    is_recurring=True,
    task_name="每日总结"
)
```

## 示例代码

完整的示例代码可以在以下文件中找到：

- `examples/unified_scheduler_example.py` - 基础使用示例
- `examples/plugin_scheduler_integration.py` - 插件集成示例
- `examples/test_scheduler_direct_integration.py` - Event Manager 直接集成测试

运行示例：
```bash
# 基础示例
python examples/unified_scheduler_example.py

# 直接集成测试
python examples/test_scheduler_direct_integration.py
```

## 注意事项

1. **自动启动**: 调度器在 Bot 启动时自动启动，无需手动调用 `start()`
2. **自动清理**: Bot 关闭时会自动清理调度器，但插件仍需清理自己的任务
3. **任务清理**: 插件或模块不再使用时，**必须**移除创建的任务，避免内存泄漏
4. **异常处理**: 回调函数中的异常会被捕获并记录，但不会中断调度器运行
5. **性能影响**: 
   - 大量 CUSTOM 类型任务会影响性能，优先考虑使用 EVENT 类型
   - EVENT 类型使用直接回调机制，几乎无性能开销
6. **时区问题**: 所有时间使用系统本地时间
7. **事件触发**: 必须通过 `event_manager.trigger_event()` 触发事件，直接调用 `unified_scheduler` 的方法不会触发事件任务

## API 参考

### UnifiedScheduler

#### 方法

- `start()` - 启动调度器（通常由系统自动调用）
- `stop()` - 停止调度器（通常由系统自动调用）
- `create_schedule(...)` - 创建调度任务
- `remove_schedule(schedule_id)` - 移除任务
- `trigger_schedule(schedule_id)` - 强制触发任务
- `pause_schedule(schedule_id)` - 暂停任务
- `resume_schedule(schedule_id)` - 恢复任务
- `get_task_info(schedule_id)` - 获取任务信息
- `list_tasks(trigger_type=None)` - 列出任务
- `get_statistics()` - 获取统计信息

#### 便捷函数

- `initialize_scheduler()` - 初始化并启动调度器（系统启动时调用）
- `shutdown_scheduler()` - 关闭调度器并清理资源（系统关闭时调用）

### TriggerType

触发类型枚举：

- `TriggerType.TIME` - 时间触发
- `TriggerType.EVENT` - 事件触发
- `TriggerType.CUSTOM` - 自定义条件触发

## 故障排查

### 任务没有执行

1. 检查调度器是否已启动：`unified_scheduler.get_statistics()["is_running"]`
2. 检查任务是否处于暂停状态：查看 `task_info["is_active"]`
3. 检查触发条件是否正确配置
4. 对于 EVENT 类型，确认事件是通过 `event_manager.trigger_event()` 触发的
5. 查看日志中是否有异常信息

### 事件任务不触发

1. 确认使用 `event_manager.trigger_event()` 而不是其他方式
2. 检查事件名称是否匹配（大小写敏感）
3. 检查任务的 `is_recurring` 设置（一次性任务执行后会自动移除）
4. 使用 `get_statistics()` 检查 `registered_events` 列表

### 性能问题

1. 检查 CUSTOM 类型任务的数量和复杂度
2. 减少条件函数的执行时间
3. 考虑使用 EVENT 类型替代频繁的条件检查（EVENT 类型使用直接回调，几乎无性能开销）

### 内存泄漏

1. 确保插件卸载时移除了所有任务
2. 检查是否有任务引用了不再需要的资源
3. 使用 `list_tasks()` 检查是否有遗留任务
4. 检查 `registered_events` 是否随任务清理而减少

## 更新日志

### v1.1.0 (2025-10-28)
- 🚀 **重大改进**: 移除 SchedulerEventHandler 中间层
- ⚡ **性能优化**: Event Manager 直接回调机制，零延迟事件通知
- 🔧 **架构简化**: 减少约 180 行代码，逻辑更清晰
- 🎯 **自动集成**: 已集成到主系统，自动启动和关闭
- 📝 **API 优化**: 简化事件订阅流程

### v1.0.0 (2025-10-27)
- ✨ 初始版本发布
- ✅ 支持三种触发类型（TIME、EVENT、CUSTOM）
- ✅ 支持循环和一次性任务
- ✅ 提供完整的任务管理API
- ✅ 线程安全的异步实现

## 许可证

本模块是 MoFox Bot 项目的一部分，遵循项目的许可证。
