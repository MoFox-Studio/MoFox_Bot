# 消息分发器重构文档

## 重构日期
2025-11-04

## 重构目标
将基于异步任务循环的消息分发机制改为使用统一的 `unified_scheduler`，实现更优雅和可维护的消息处理流程。

## 重构内容

### 1. 修改 unified_scheduler 以支持完全并发执行

**文件**: `src/schedule/unified_scheduler.py`

**主要改动**:
- 修改 `_check_and_trigger_tasks` 方法，使用 `asyncio.create_task` 为每个到期任务创建独立的异步任务
- 新增 `_execute_task_callback` 方法，用于并发执行单个任务
- 使用 `asyncio.gather` 并发等待所有任务完成，确保不同 schedule 之间完全异步执行，不会相互阻塞

**关键改进**:
```python
# 为每个任务创建独立的异步任务，确保并发执行
execution_tasks = []
for task in tasks_to_trigger:
    execution_task = asyncio.create_task(
        self._execute_task_callback(task, current_time),
        name=f"execute_{task.task_name}"
    )
    execution_tasks.append(execution_task)

# 等待所有任务完成（使用 return_exceptions=True 避免单个任务失败影响其他任务）
results = await asyncio.gather(*execution_tasks, return_exceptions=True)
```

### 2. 创建新的 SchedulerDispatcher

**文件**: `src/chat/message_manager/scheduler_dispatcher.py`

**功能**:
基于 `unified_scheduler` 的消息分发器，替代原有的 `stream_loop_task` 循环机制。

**工作流程**:
1. **接收消息时**: 将消息添加到聊天流上下文（缓存）
2. **检查 schedule**: 查看该聊天流是否有活跃的 schedule
3. **打断判定**: 如果有活跃 schedule，检查是否需要打断
   - 如果需要打断，移除旧 schedule 并创建新的
   - 如果不需要打断，保持原有 schedule
4. **创建 schedule**: 如果没有活跃 schedule，创建新的
5. **Schedule 触发**: 当 schedule 到期时，激活 chatter 进行处理
6. **处理完成**: 计算下次间隔并根据需要注册新的 schedule

**关键方法**:
- `on_message_received(stream_id)`: 消息接收时的处理入口
- `_check_interruption(stream_id, context)`: 检查是否应该打断
- `_create_schedule(stream_id, context)`: 创建新的 schedule
- `_cancel_and_recreate_schedule(stream_id, context)`: 取消并重新创建 schedule
- `_on_schedule_triggered(stream_id)`: schedule 触发时的回调
- `_process_stream(stream_id, context)`: 激活 chatter 处理消息

### 3. 修改 MessageManager 集成新分发器

**文件**: `src/chat/message_manager/message_manager.py`

**主要改动**:
1. 导入 `scheduler_dispatcher`
2. 启动时初始化 `scheduler_dispatcher` 而非 `stream_loop_manager`
3. 修改 `add_message` 方法:
   - 将消息添加到上下文后
   - 调用 `scheduler_dispatcher.on_message_received(stream_id)` 处理消息接收事件
4. 废弃 `_check_and_handle_interruption` 方法（打断逻辑已集成到 dispatcher）

**新的消息接收流程**:
```python
async def add_message(self, stream_id: str, message: DatabaseMessages):
    # 1. 检查 notice 消息
    if self._is_notice_message(message):
        await self._handle_notice_message(stream_id, message)
        if not global_config.notice.enable_notice_trigger_chat:
            return
    
    # 2. 将消息添加到上下文
    chat_stream = await chat_manager.get_stream(stream_id)
    await chat_stream.context_manager.add_message(message)
    
    # 3. 通知 scheduler_dispatcher 处理
    await scheduler_dispatcher.on_message_received(stream_id)
```

### 4. 更新模块导出

**文件**: `src/chat/message_manager/__init__.py`

**改动**:
- 导出 `SchedulerDispatcher` 和 `scheduler_dispatcher`

## 架构对比

### 旧架构 (基于 stream_loop_task)
```
消息到达 -> add_message -> 添加到上下文 -> 检查打断 -> 取消 stream_loop_task
                                                             -> 重新创建 stream_loop_task
                                                             
stream_loop_task: while True:
    检查未读消息 -> 处理消息 -> 计算间隔 -> sleep(间隔)
```

**问题**:
- 每个聊天流维护一个独立的异步循环任务
- 即使没有消息也需要持续轮询
- 打断逻辑通过取消和重建任务实现，较为复杂
- 难以统一管理和监控

### 新架构 (基于 unified_scheduler)
```
消息到达 -> add_message -> 添加到上下文 -> dispatcher.on_message_received
                                                -> 检查是否有活跃 schedule
                                                -> 打断判定
                                                -> 创建/更新 schedule
                                                
schedule 到期 -> _on_schedule_triggered -> 处理消息 -> 计算间隔 -> 创建新 schedule (如果需要)
```

**优势**:
- 使用统一的调度器管理所有聊天流
- 按需创建 schedule，没有消息时不会创建
- 打断逻辑清晰：移除旧 schedule + 创建新 schedule
- 易于监控和统计（统一的 scheduler 统计）
- 完全异步并发，多个 schedule 可以同时触发而不相互阻塞

## 兼容性

### 保留的组件
- `stream_loop_manager`: 暂时保留但不启动，以便需要时回滚
- `_check_and_handle_interruption`: 保留方法签名但不执行，避免破坏现有调用

### 移除的组件
- 无（本次重构采用渐进式方式，先添加新功能，待稳定后再移除旧代码）

## 配置项

所有配置项保持不变，新分发器完全兼容现有配置：
- `chat.interruption_enabled`: 是否启用打断
- `chat.allow_reply_interruption`: 是否允许回复时打断
- `chat.interruption_max_limit`: 最大打断次数
- `chat.distribution_interval`: 基础分发间隔
- `chat.force_dispatch_unread_threshold`: 强制分发阈值
- `chat.force_dispatch_min_interval`: 强制分发最小间隔

## 测试建议

1. **基本功能测试**
   - 单个聊天流接收消息并正常处理
   - 多个聊天流同时接收消息并并发处理
   
2. **打断测试**
   - 在 chatter 处理过程中发送新消息，验证打断逻辑
   - 验证打断次数限制
   - 验证打断概率计算
   
3. **间隔计算测试**
   - 验证基于能量的动态间隔计算
   - 验证强制分发阈值触发
   
4. **并发测试**
   - 多个聊天流的 schedule 同时到期，验证并发执行
   - 验证不同 schedule 之间不会相互阻塞
   
5. **长时间稳定性测试**
   - 运行较长时间，观察是否有内存泄漏
   - 观察 schedule 创建和销毁是否正常

## 回滚方案

如果新机制出现问题，可以通过以下步骤回滚：

1. 在 `message_manager.py` 的 `start()` 方法中:
   ```python
   # 注释掉新分发器
   # await scheduler_dispatcher.start()
   # scheduler_dispatcher.set_chatter_manager(self.chatter_manager)
   
   # 启用旧分发器
   await stream_loop_manager.start()
   stream_loop_manager.set_chatter_manager(self.chatter_manager)
   ```

2. 在 `add_message()` 方法中:
   ```python
   # 注释掉新逻辑
   # await scheduler_dispatcher.on_message_received(stream_id)
   
   # 恢复旧逻辑
   await self._check_and_handle_interruption(chat_stream, message)
   ```

3. 在 `_check_and_handle_interruption()` 方法中移除开头的 `return` 语句

## 后续工作

1. 在确认新机制稳定后，完全移除 `stream_loop_manager` 相关代码
2. 清理 `StreamContext` 中的 `stream_loop_task` 字段
3. 移除 `_check_and_handle_interruption` 方法
4. 更新相关文档和注释

## 性能预期

- **资源占用**: 减少（不再为每个流维护独立循环）
- **响应延迟**: 不变（仍基于相同的间隔计算）
- **并发能力**: 提升（完全异步执行，无阻塞）
- **可维护性**: 提升（逻辑更清晰，统一管理）
