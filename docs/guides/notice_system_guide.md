# Notice 系统使用指南

## 概述

Notice 系统用于管理和展示系统通知消息，支持两种作用域：
- **公共 Notice（Public）**: 对所有聊天流可见
- **流级 Notice（Stream）**: 仅对特定聊天流可见

## Notice 配置

### 1. 消息标记为 Notice

在消息的 `additional_config` 中设置以下字段：

```python
additional_config = {
    "is_notice": True,  # 标记为notice消息
    "notice_type": "group_ban",  # notice类型（可选）
    "is_public_notice": False,  # 是否为公共notice
}
```

### 2. Notice 作用域

Notice 的作用域完全由 `is_public_notice` 字段决定：

#### 流级 Notice（默认）
```python
additional_config = {
    "is_notice": True,
    "is_public_notice": False,  # 或者不设置该字段
}
```
- 仅在消息所属的聊天流中可见
- 适用于：群禁言、群解禁、戳一戳等群内事件

#### 公共 Notice
```python
additional_config = {
    "is_notice": True,
    "is_public_notice": True,  # 明确设置为公共
}
```
- 在所有聊天流中可见
- 适用于：系统公告、平台维护通知等全局事件

### 3. Notice 类型

通过 `notice_type` 字段可以对 notice 进行分类：

```python
# 常见的 notice 类型
notice_types = {
    "group_ban": "群禁言",
    "group_lift_ban": "群解禁",
    "group_whole_ban": "全员禁言",
    "group_whole_lift_ban": "全员解禁",
    "poke": "戳一戳",
    "system_announcement": "系统公告",
    "platform_maintenance": "平台维护",
}
```

### 4. Notice 生存时间（TTL）

Notice 消息会在一定时间后自动过期，默认为 1 小时（3600 秒）。

不同类型的 notice 可以有不同的 TTL：
- 临时事件（戳一戳）: 5 分钟
- 群管理事件（禁言/解禁）: 1 小时
- 重要公告: 24 小时

## 使用示例

### 示例 1: 群禁言通知（流级）

```python
from src.common.data_models.database_data_model import DatabaseMessages

message = DatabaseMessages(
    chat_id="group_123456",
    sender_id="10001",
    raw_message="用户 张三 被管理员禁言 10 分钟",
    additional_config={
        "is_notice": True,
        "is_public_notice": False,  # 仅该群可见
        "notice_type": "group_ban",
        "target_id": "user_12345",
    }
)
```

### 示例 2: 系统维护公告（公共）

```python
message = DatabaseMessages(
    chat_id="system",
    sender_id="system",
    raw_message="系统将于今晚 23:00 进行维护，预计 1 小时",
    additional_config={
        "is_notice": True,
        "is_public_notice": True,  # 所有聊天流可见
        "notice_type": "platform_maintenance",
    }
)
```

### 示例 3: 在插件中发送 Notice

```python
from src.api import send_private_message, send_group_message

# 发送群内 notice
await send_group_message(
    group_id=123456,
    message="管理员已开启全员禁言",
    additional_config={
        "is_notice": True,
        "is_public_notice": False,
        "notice_type": "group_whole_ban",
    }
)

# 发送公共 notice
await send_group_message(
    group_id=123456,  # 任意有效的群号
    message="🔔 Bot 将在 5 分钟后重启进行更新",
    additional_config={
        "is_notice": True,
        "is_public_notice": True,
        "notice_type": "system_announcement",
    }
)
```

## Notice 在 Prompt 中的展示

当启用 `notice_in_prompt` 配置时，notice 消息会被自动添加到 AI 的提示词中：

```
## 📢 最近的系统通知

[群禁言] 用户 张三 被管理员禁言 10 分钟 (5分钟前)
[戳一戳] 李四 戳了戳 你 (刚刚)
[系统公告] Bot 将在 5 分钟后重启进行更新 (2分钟前)
```

## 配置选项

在 `bot_config.toml` 中配置 notice 系统：

```toml
[notice]
# 是否在 prompt 中显示 notice
notice_in_prompt = true

# prompt 中显示的 notice 数量限制
notice_prompt_limit = 5
```

## 注意事项

1. **作用域控制**: 
   - `is_public_notice` 字段是唯一决定 notice 作用域的因素
   - 不要依赖 `notice_type` 来控制作用域

2. **性能考虑**:
   - Notice 消息会自动过期清理
   - 每种类型最多存储 100 条 notice
   - 每 5 分钟自动清理过期消息

3. **兼容性**:
   - 如果不设置 `is_public_notice`，默认为流级 notice
   - 旧代码中基于 `notice_type` 的判断已被移除

## 迁移指南

如果你的代码中依赖了以下 notice 类型自动成为公共 notice 的行为：
- `group_whole_ban`
- `group_whole_lift_ban`
- `system_announcement`
- `platform_maintenance`

请在消息的 `additional_config` 中显式设置：

```python
# 修改前（依赖硬编码）
additional_config = {
    "is_notice": True,
    "notice_type": "system_announcement",
    # 会自动成为公共 notice
}

# 修改后（显式指定）
additional_config = {
    "is_notice": True,
    "notice_type": "system_announcement",
    "is_public_notice": True,  # 显式设置
}
```

## API 参考

### GlobalNoticeManager

```python
from src.chat.message_manager.global_notice_manager import global_notice_manager

# 添加 notice
success = global_notice_manager.add_notice(
    message=db_message,
    scope=NoticeScope.PUBLIC,  # 或 NoticeScope.STREAM
    target_stream_id="group_123456",  # STREAM 模式必需
    ttl=3600  # 生存时间（秒）
)

# 获取可访问的 notice
notices = global_notice_manager.get_accessible_notices(
    stream_id="group_123456",
    limit=10
)

# 获取格式化的 notice 文本
text = global_notice_manager.get_notice_text(
    stream_id="group_123456",
    limit=5
)
```

## 常见问题

### Q: Notice 不显示在 prompt 中？
A: 检查配置：
1. `bot_config.toml` 中 `notice.notice_in_prompt = true`
2. 确认消息的 `is_notice = True`
3. 确认 notice 未过期

### Q: 如何让 notice 对所有群可见？
A: 在 `additional_config` 中设置 `is_public_notice = True`

### Q: 如何设置自定义的 notice 类型？
A: 在 `additional_config` 中设置任意字符串作为 `notice_type`

### Q: Notice 什么时候会被清理？
A: 
1. 超过 TTL 时间后自动清理
2. 每种类型超过 100 条时，移除最旧的
3. 手动调用清理 API
