# 增强命令系统使用指南

## 概述

增强命令系统是MoFox-Bot插件系统的一个扩展，让命令的定义和使用变得更加简单直观。你不再需要编写复杂的正则表达式，只需要定义命令名、别名和参数处理逻辑即可。

## 核心特性

- **无需正则表达式**：只需定义命令名和别名
- **自动参数解析**：提供`CommandArgs`类处理参数
- **命令别名支持**：一个命令可以有多个别名
- **优先级控制**：支持命令优先级设置
- **聊天类型限制**：可限制命令在群聊或私聊中使用
- **消息拦截**：可选择是否拦截消息进行后续处理

## 快速开始

### 1. 创建基础命令

```python
from src.plugin_system import PlusCommand, CommandArgs, ChatType
from typing import Tuple, Optional

class EchoCommand(PlusCommand):
    """Echo命令示例"""
    
    command_name = "echo"
    command_description = "回显命令"
    command_aliases = ["say", "repeat"]  # 可选：命令别名
    priority = 5  # 可选：优先级，数字越大优先级越高
    chat_type_allow = ChatType.ALL  # 可选：ALL, GROUP, PRIVATE
    intercept_message = True  # 可选：是否拦截消息

    async def execute(self, args: CommandArgs) -> Tuple[bool, Optional[str], bool]:
        """执行命令"""
        if args.is_empty():
            await self.send_text("❓ 请提供要回显的内容\\n用法: /echo <内容>")
            return True, "参数不足", True
        
        content = args.get_raw()
        await self.send_text(f"🔊 {content}")
        
        return True, "Echo命令执行成功", True
```

### 2. 在插件中注册命令

```python
from src.plugin_system import BasePlugin, create_plus_command_adapter, register_plugin

@register_plugin
class MyPlugin(BasePlugin):
    plugin_name = "my_plugin"
    enable_plugin = True
    dependencies = []
    python_dependencies = []
    config_file_name = "config.toml"

    def get_plugin_components(self):
        components = []
        
        # 使用工厂函数创建适配器
        echo_adapter = create_plus_command_adapter(EchoCommand)
        components.append((EchoCommand.get_command_info(), echo_adapter))
        
        return components
```

## CommandArgs 类详解

`CommandArgs`类提供了丰富的参数处理功能：

### 基础方法

```python
# 获取原始参数字符串
raw_text = args.get_raw()

# 获取解析后的参数列表（按空格分割，支持引号）
arg_list = args.get_args()

# 检查是否有参数
if args.is_empty():
    # 没有参数的处理

# 获取参数数量
count = args.count()
```

### 获取特定参数

```python
# 获取第一个参数
first_arg = args.get_first("默认值")

# 获取指定索引的参数
second_arg = args.get_arg(1, "默认值")

# 获取从指定位置开始的剩余参数
remaining = args.get_remaining(1)  # 从第2个参数开始
```

### 标志参数处理

```python
# 检查是否包含标志
if args.has_flag("--verbose"):
    # 处理verbose模式

# 获取标志的值
output_file = args.get_flag_value("--output", "default.txt")
name = args.get_flag_value("--name", "Anonymous")
```

## 高级示例

### 1. 带子命令的复杂命令

```python
class TestCommand(PlusCommand):
    command_name = "test"
    command_description = "测试命令，展示参数解析功能"
    command_aliases = ["t"]

    async def execute(self, args: CommandArgs) -> Tuple[bool, Optional[str], bool]:
        if args.is_empty():
            await self.send_text("用法: /test <子命令> [参数]")
            return True, "显示帮助", True
        
        subcommand = args.get_first().lower()
        
        if subcommand == "args":
            result = f"""
🔍 参数解析结果:
原始字符串: '{args.get_raw()}'
解析后参数: {args.get_args()}
参数数量: {args.count()}
第一个参数: '{args.get_first()}'
剩余参数: '{args.get_remaining()}'
            """
            await self.send_text(result)
            
        elif subcommand == "flags":
            result = f"""
🏴 标志测试结果:
包含 --verbose: {args.has_flag('--verbose')}
包含 -v: {args.has_flag('-v')}
--output 的值: '{args.get_flag_value('--output', '未设置')}'
--name 的值: '{args.get_flag_value('--name', '未设置')}'
            """
            await self.send_text(result)
            
        else:
            await self.send_text(f"❓ 未知的子命令: {subcommand}")
        
        return True, "Test命令执行成功", True
```

### 2. 聊天类型限制示例

```python
class PrivateOnlyCommand(PlusCommand):
    command_name = "private"
    command_description = "仅私聊可用的命令"
    chat_type_allow = ChatType.PRIVATE

    async def execute(self, args: CommandArgs) -> Tuple[bool, Optional[str], bool]:
        await self.send_text("这是一个仅私聊可用的命令")
        return True, "私聊命令执行", True

class GroupOnlyCommand(PlusCommand):
    command_name = "group"
    command_description = "仅群聊可用的命令"
    chat_type_allow = ChatType.GROUP

    async def execute(self, args: CommandArgs) -> Tuple[bool, Optional[str], bool]:
        await self.send_text("这是一个仅群聊可用的命令")
        return True, "群聊命令执行", True
```

### 3. 配置驱动的命令

```python
class ConfigurableCommand(PlusCommand):
    command_name = "config_cmd"
    command_description = "可配置的命令"

    async def execute(self, args: CommandArgs) -> Tuple[bool, Optional[str], bool]:
        # 从插件配置中获取设置
        max_length = self.get_config("commands.max_length", 100)
        enabled_features = self.get_config("commands.features", [])
        
        if args.is_empty():
            await self.send_text("请提供参数")
            return True, "无参数", True
            
        content = args.get_raw()
        if len(content) > max_length:
            await self.send_text(f"内容过长，最大允许 {max_length} 字符")
            return True, "内容过长", True
            
        # 根据配置决定功能
        if "uppercase" in enabled_features:
            content = content.upper()
            
        await self.send_text(f"处理结果: {content}")
        return True, "配置命令执行", True
```

## 支持的命令前缀

系统支持以下命令前缀（在`config/bot_config.toml`中配置）：

- `/` - 斜杠（默认）
- `!` - 感叹号
- `.` - 点号
- `#` - 井号

例如，对于echo命令，以下调用都是有效的：
- `/echo Hello`
- `!echo Hello`
- `.echo Hello`
- `#echo Hello`

## 返回值说明

`execute`方法需要返回一个三元组：

```python
return (执行成功标志, 可选消息, 是否拦截后续处理)
```

- **执行成功标志** (bool): True表示命令执行成功，False表示失败
- **可选消息** (Optional[str]): 用于日志记录的消息
- **是否拦截后续处理** (bool): True表示拦截消息，不进行后续处理

## 最佳实践

1. **命令命名**：使用简短、直观的命令名
2. **别名设置**：为常用命令提供简短别名
3. **参数验证**：总是检查参数的有效性
4. **错误处理**：提供清晰的错误提示和使用说明
5. **配置支持**：重要设置应该可配置
6. **聊天类型**：根据命令功能选择合适的聊天类型限制

## 完整示例

完整的插件示例请参考 `plugins/echo_example/plugin.py` 文件。

## 与传统BaseCommand的区别

| 特性 | PlusCommand | BaseCommand |
|------|-------------|-------------|
| 正则表达式 | 自动生成 | 手动编写 |
| 参数解析 | CommandArgs类 | 手动处理 |
| 别名支持 | 内置支持 | 需要在正则中处理 |
| 代码复杂度 | 简单 | 复杂 |
| 学习曲线 | 平缓 | 陡峭 |

增强命令系统让插件开发变得更加简单和高效，特别适合新手开发者快速上手。
