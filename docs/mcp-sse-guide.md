# MCP Server-Sent Events (SSE) 使用指南

## 📖 概述

MCP SSE 客户端是 MaiBot 的一个扩展功能，提供与 MCP (Model Context Protocol) 服务器的实时事件订阅能力。通过 Server-Sent Events (SSE) 技术，MaiBot 可以接收来自 MCP 服务器的实时事件推送，实现低延迟的交互响应。

## ✨ 功能特性

- 🔗 **实时连接**: 与 MCP 服务器建立持久的 SSE 连接
- 🔄 **自动重连**: 支持指数退避策略的断线重连机制
- 🎯 **事件订阅**: 灵活的事件类型订阅和处理系统
- 🔐 **安全认证**: 支持 Bearer Token 和 SSL/TLS 认证
- 📊 **监控统计**: 提供连接状态和事件统计信息
- 🛡️ **错误处理**: 完善的异常处理和日志记录

## 🚀 快速开始

### 第一步：启用 MCP SSE 功能

1. 打开 `config/bot_config.toml` 配置文件
2. 找到 `[mcp_sse]` 配置段，如果没有请添加：

```toml
[mcp_sse]
enable = true  # 启用 MCP SSE 功能
server_url = "http://your-mcp-server.com:8080/events"  # MCP 服务器 SSE 端点
auth_key = "your-auth-token"  # 认证密钥（可选）
```

### 第二步：配置连接参数

```toml
[mcp_sse]
# 基本配置
enable = true
server_url = "http://localhost:8080/events"
auth_key = ""

# 连接配置
connection_timeout = 30  # 连接超时时间（秒）
read_timeout = 60       # 读取超时时间（秒）

# 重连配置
enable_reconnect = true           # 启用自动重连
max_reconnect_attempts = 10       # 最大重连次数（-1 表示无限重连）
initial_reconnect_delay = 1.0     # 初始重连延迟（秒）
max_reconnect_delay = 60.0        # 最大重连延迟（秒）
reconnect_backoff_factor = 2.0    # 重连延迟指数退避因子

# 事件处理配置
event_buffer_size = 1000          # 事件缓冲区大小
enable_event_logging = true       # 启用事件日志记录

# 订阅配置
subscribed_events = []            # 订阅的事件类型（空列表表示订阅所有事件）
# 示例：只订阅特定事件
# subscribed_events = ["chat.message", "user.login", "system.status"]

# 高级配置
user_agent = "MaiBot-MCP-SSE-Client/1.0"  # 用户代理字符串

# SSL 配置
verify_ssl = true        # 是否验证 SSL 证书
ssl_cert_path = ""       # SSL 客户端证书路径（可选）
ssl_key_path = ""        # SSL 客户端密钥路径（可选）
```

### 第三步：启动 MaiBot

正常启动 MaiBot，系统会自动初始化 MCP SSE 客户端：

```bash
python bot.py
```

启动日志中会显示 MCP SSE 相关信息：

```
[INFO] 初始化 MCP SSE 管理器
[INFO] 连接到 MCP 服务器: http://localhost:8080/events
[INFO] 成功连接到 MCP 服务器
[INFO] MCP SSE 系统初始化成功
```

## 📝 配置详解

### 基本配置

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `enable` | bool | false | 是否启用 MCP SSE 功能 |
| `server_url` | string | "" | MCP 服务器 SSE 端点 URL |
| `auth_key` | string | "" | 认证密钥，用于 Bearer Token 认证 |

### 连接配置

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `connection_timeout` | int | 30 | 连接超时时间（秒） |
| `read_timeout` | int | 60 | 读取超时时间（秒） |

### 重连配置

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `enable_reconnect` | bool | true | 是否启用自动重连 |
| `max_reconnect_attempts` | int | 10 | 最大重连次数，-1 表示无限重连 |
| `initial_reconnect_delay` | float | 1.0 | 初始重连延迟时间（秒） |
| `max_reconnect_delay` | float | 60.0 | 最大重连延迟时间（秒） |
| `reconnect_backoff_factor` | float | 2.0 | 重连延迟指数退避因子 |

### 事件处理配置

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `event_buffer_size` | int | 1000 | 事件缓冲区大小 |
| `enable_event_logging` | bool | true | 是否启用事件日志记录 |
| `subscribed_events` | list | [] | 订阅的事件类型列表 |

## 🎯 事件类型

MCP SSE 支持多种事件类型，常见的包括：

### 系统事件
- `system.status` - 系统状态变化
- `system.heartbeat` - 系统心跳
- `system.shutdown` - 系统关闭

### 用户事件
- `user.login` - 用户登录
- `user.logout` - 用户登出
- `user.action` - 用户行为

### 聊天事件
- `chat.message` - 聊天消息
- `chat.typing` - 正在输入
- `chat.reaction` - 消息反应

### 通知事件
- `notification.info` - 信息通知
- `notification.warning` - 警告通知
- `notification.error` - 错误通知

## 🔧 高级用法

### 自定义事件处理器

如果您需要对特定事件进行自定义处理，可以通过插件系统或直接修改代码来注册事件处理器：

```python
from src.mcp import get_mcp_sse_manager

# 获取 MCP SSE 管理器
manager = get_mcp_sse_manager()

if manager:
    # 注册聊天消息事件处理器
    def handle_chat_message(event):
        print(f"收到聊天消息: {event.data}")
        # 在这里添加您的自定义逻辑
    
    manager.register_event_handler("chat.message", handle_chat_message)
    
    # 注册全局事件处理器
    def handle_all_events(event):
        print(f"收到事件: {event.event_type} - {event.data}")
    
    manager.register_global_event_handler(handle_all_events)
```

### 获取连接状态

```python
from src.mcp import get_mcp_sse_manager

manager = get_mcp_sse_manager()
if manager:
    # 检查连接状态
    if manager.is_connected():
        print("MCP SSE 客户端已连接")
    
    # 获取详细统计信息
    stats = manager.get_stats()
    print(f"连接状态: {stats['connected']}")
    print(f"运行状态: {stats['running']}")
    print(f"总接收事件数: {stats['total_events_received']}")
    print(f"重连次数: {stats['reconnect_attempts']}")
```

### 查看最近事件

```python
from src.mcp import get_mcp_sse_manager

manager = get_mcp_sse_manager()
if manager:
    # 获取最近 10 个事件
    recent_events = manager.get_recent_events(10)
    
    for event in recent_events:
        print(f"{event.timestamp}: {event.event_type}")
        print(f"数据: {event.data}")
        print("---")
```

## 🧪 测试功能

项目包含了完整的测试工具，您可以使用它们来验证 MCP SSE 功能：

### 基本功能测试

```bash
# 进入项目目录
cd /path/to/MaiBot

# 运行基本功能测试
python -m src.mcp.test_client
```

### 重连功能测试

```bash
# 测试断线重连功能
python -m src.mcp.test_client reconnect
```

测试脚本会输出详细的测试结果，包括：
- 连接状态
- 接收到的事件数量
- 事件类型统计
- 连接时长和性能指标

## 🔍 故障排除

### 常见问题

#### 1. 连接失败

**问题**: 无法连接到 MCP 服务器

**解决方案**:
- 检查 `server_url` 配置是否正确
- 确认 MCP 服务器是否正在运行
- 检查网络连接和防火墙设置
- 验证端口是否可访问

#### 2. 认证失败

**问题**: 收到 401 认证错误

**解决方案**:
- 检查 `auth_key` 配置是否正确
- 确认认证密钥是否有效
- 联系 MCP 服务器管理员获取正确的认证信息

#### 3. SSL 证书错误

**问题**: SSL 证书验证失败

**解决方案**:
- 检查服务器 SSL 证书是否有效
- 临时设置 `verify_ssl = false` 进行测试
- 配置正确的客户端证书路径

#### 4. 频繁重连

**问题**: 客户端不断重连

**解决方案**:
- 检查网络连接稳定性
- 调整重连参数（增加延迟时间）
- 检查服务器端是否有连接限制

#### 5. 事件丢失

**问题**: 部分事件没有收到

**解决方案**:
- 增加 `event_buffer_size` 配置
- 检查事件处理器是否有异常
- 确认 `subscribed_events` 配置是否正确

### 日志调试

MCP SSE 相关日志使用以下标签：

- `mcp.sse_client` - SSE 客户端日志
- `mcp.event_handler` - 事件处理器日志  
- `mcp.manager` - 管理器日志

您可以通过调整日志级别来获取更详细的调试信息：

```toml
[log]
console_log_level = "DEBUG"  # 设置为 DEBUG 级别查看详细日志
```

## 📚 API 参考

### MCPSSEManager

主要的管理器类，提供以下方法：

- `is_running()` - 检查是否正在运行
- `is_connected()` - 检查是否已连接
- `get_stats()` - 获取统计信息
- `get_recent_events(count)` - 获取最近的事件
- `register_event_handler(event_type, handler)` - 注册事件处理器
- `register_global_event_handler(handler)` - 注册全局事件处理器

### MCPEvent

事件对象，包含以下属性：

- `event_type` - 事件类型
- `data` - 事件数据（字典格式）
- `timestamp` - 事件时间戳
- `event_id` - 事件 ID（可选）
- `retry` - 重试间隔（可选）

## 🤝 贡献

如果您在使用过程中遇到问题或有改进建议，欢迎：

1. 提交 Issue 报告问题
2. 提交 Pull Request 贡献代码
3. 完善文档和示例

## 📄 许可证

本功能遵循 MaiBot 项目的许可证协议。