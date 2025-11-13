# Action 激活机制重构指南

## 📋 概述

本文档介绍 MoFox-Bot Action 组件的新激活机制。新机制通过 `go_activate()` 方法提供更灵活、更强大的激活判断能力。

## 🎯 为什么要重构？

### 旧的激活机制的问题

1. **不够灵活**：只能使用预定义的激活类型（`ALWAYS`、`NEVER`、`RANDOM`、`KEYWORD`、`LLM_JUDGE`）
2. **难以组合**：无法轻松组合多种激活条件
3. **配置复杂**：需要在类属性中配置多个字段
4. **扩展困难**：添加新的激活逻辑需要修改核心代码

### 新的激活机制的优势

1. **完全自定义**：通过重写 `go_activate()` 方法实现任意激活逻辑
2. **灵活组合**：可以轻松组合多种激活条件
3. **简洁明了**：激活逻辑集中在一个方法中
4. **易于扩展**：可以实现任何复杂的激活判断

## 🚀 快速开始

### 基本结构

```python
from src.plugin_system import BaseAction

class MyAction(BaseAction):
    """我的自定义 Action"""
    
    action_name = "my_action"
    action_description = "这是一个示例 Action"
    
    async def go_activate(self, llm_judge_model=None) -> bool:
        """判断此 Action 是否应该被激活
        
        Args:
            chat_content: 聊天内容
            llm_judge_model: LLM 判断模型（可选）
            
        Returns:
            bool: True 表示激活，False 表示不激活
        """
        # 在这里实现你的激活逻辑
        return True
    
    async def execute(self) -> tuple[bool, str]:
        """执行 Action 的具体逻辑"""
        await self.send_text("Hello, World!")
        return True, "发送成功"
```

## 🛠️ 工具函数

BaseAction 提供了三个便捷的工具函数来简化常见的激活判断：

### 1. `_random_activation(probability)` - 随机激活

```python
async def go_activate(self, llm_judge_model=None) -> bool:
    """30% 概率激活"""
    return await self._random_activation(0.3)
```

**参数：**
- `probability`: 激活概率，范围 0.0 到 1.0

### 2. `_keyword_match(keywords, case_sensitive)` - 关键词匹配

```python
async def go_activate(self, llm_judge_model=None) -> bool:
    """当消息包含特定关键词时激活"""
    return await self._keyword_match(
        keywords=["你好", "hello", "hi"],
        case_sensitive=False  # 不区分大小写
    )
```

**参数：**
- `keywords`: 关键词列表
- `case_sensitive`: 是否区分大小写（默认 False）

### 3. `_llm_judge_activation(...)` - LLM 智能判断

```python
async def go_activate(self, llm_judge_model=None) -> bool:
    """使用 LLM 判断是否激活"""
    return await self._llm_judge_activation(
        judge_prompt="当用户询问天气信息时激活",
        llm_judge_model=llm_judge_model
    )
```

**参数：**
- `judge_prompt`: 判断提示词（核心判断逻辑）
- `llm_judge_model`: LLM 模型实例（可选，会自动创建）
- `action_description`: Action 描述（可选，默认使用类属性）
- `action_require`: 使用场景列表（可选，默认使用类属性）

## 📚 示例

### 示例 1：简单的关键词激活

```python
class GreetingAction(BaseAction):
    """问候 Action - 当检测到问候语时激活"""
    
    action_name = "greeting"
    action_description = "回应用户的问候"
    
    async def go_activate(self, llm_judge_model=None) -> bool:
        """检测到问候语时激活"""
        return await self._keyword_match(
            keywords=["你好", "hello", "hi", "嗨"],
            case_sensitive=False
        )
    
    async def execute(self) -> tuple[bool, str]:
        await self.send_text("你好！很高兴见到你！👋")
        return True, "发送了问候"
```

### 示例 2：LLM 智能判断激活

```python
class ComfortAction(BaseAction):
    """安慰 Action - 当用户情绪低落时激活"""
    
    action_name = "comfort"
    action_description = "提供情感支持和安慰"
    action_require = ["用户情绪低落", "需要安慰"]
    
    async def go_activate(self, llm_judge_model=None) -> bool:
        """使用 LLM 判断用户是否需要安慰"""
        return await self._llm_judge_activation(
            judge_prompt="""
判断用户是否表达了以下情绪或需求：
1. 感到难过、沮丧或失落
2. 表达了负面情绪
3. 需要安慰或鼓励

如果满足上述条件，回答"是"，否则回答"否"。
            """,
            llm_judge_model=llm_judge_model
        )
    
    async def execute(self) -> tuple[bool, str]:
        await self.send_text("看起来你心情不太好，希望能让你开心一点！🤗💕")
        return True, "发送了安慰"
```

### 示例 3：随机激活

```python
class RandomEmojiAction(BaseAction):
    """随机表情 Action - 10% 概率激活"""
    
    action_name = "random_emoji"
    action_description = "随机发送表情增加趣味性"
    
    async def go_activate(self, llm_judge_model=None) -> bool:
        """10% 概率激活"""
        return await self._random_activation(0.1)
    
    async def execute(self) -> tuple[bool, str]:
        import random
        emojis = ["😊", "😂", "👍", "🎉", "🤔", "🤖"]
        await self.send_text(random.choice(emojis))
        return True, "发送了表情"
```

### 示例 4：组合多种激活条件

```python
class FlexibleAction(BaseAction):
    """灵活的 Action - 组合多种激活条件"""
    
    action_name = "flexible"
    action_description = "展示灵活的激活逻辑"
    
    async def go_activate(self, llm_judge_model=None) -> bool:
        """组合激活：随机 20% 概率，或者匹配关键词"""
        
        # 策略 1: 随机激活
        if await self._random_activation(0.2):
            return True
        
        # 策略 2: 关键词匹配
        if await self._keyword_match(["表情", "emoji"], case_sensitive=False):
            return True
        
        # 策略 3: 所有条件都不满足
        return False
    
    async def execute(self) -> tuple[bool, str]:
        await self.send_text("这是一个灵活的激活示例！✨")
        return True, "执行成功"
```

### 示例 5：复杂的自定义逻辑

```python
class AdvancedAction(BaseAction):
    """高级 Action - 实现复杂的激活逻辑"""
    
    action_name = "advanced"
    action_description = "高级激活逻辑示例"
    
    async def go_activate(self, llm_judge_model=None) -> bool:
        """实现复杂的激活逻辑"""
        
        # 1. 检查时间：只在工作时间激活
        from datetime import datetime
        now = datetime.now()
        if now.hour < 9 or now.hour > 18:
            return False
        
        # 2. 检查消息长度：消息太短不激活
        if len(chat_content) < 10:
            return False
        
        # 3. 组合关键词和 LLM 判断
        has_keyword = await self._keyword_match(
            ["帮助", "help", "求助"],
            case_sensitive=False
        )
        
        if has_keyword:
            # 如果匹配到关键词，用 LLM 进一步判断
            return await self._llm_judge_activation(
                judge_prompt="用户是否真的需要帮助？",
                llm_judge_model=llm_judge_model
            )
        
        return False
    
    async def execute(self) -> tuple[bool, str]:
        await self.send_text("我来帮助你！")
        return True, "提供了帮助"
```

### 示例 6：始终激活或从不激活

```python
class AlwaysActiveAction(BaseAction):
    """始终激活的 Action"""
    
    action_name = "always_active"
    action_description = "这个 Action 总是激活"
    
    async def go_activate(self, llm_judge_model=None) -> bool:
        """始终返回 True"""
        return True
    
    async def execute(self) -> tuple[bool, str]:
        await self.send_text("我总是可用！")
        return True, "执行成功"


class NeverActiveAction(BaseAction):
    """从不激活的 Action（可用于测试或临时禁用）"""
    
    action_name = "never_active"
    action_description = "这个 Action 从不激活"
    
    async def go_activate(self, llm_judge_model=None) -> bool:
        """始终返回 False"""
        return False
    
    async def execute(self) -> tuple[bool, str]:
        # 这个方法不会被调用
        return False, "未执行"
```

## 🔄 从旧的激活机制迁移

### 旧写法（已废弃但仍然兼容）

```python
class OldStyleAction(BaseAction):
    action_name = "old_style"
    action_description = "旧风格的 Action"
    
    # 旧的激活配置
    activation_type = ActionActivationType.KEYWORD
    activation_keywords = ["你好", "hello"]
    keyword_case_sensitive = False
    
    async def execute(self) -> tuple[bool, str]:
        return True, "执行成功"
```

### 新写法（推荐）

```python
class NewStyleAction(BaseAction):
    action_name = "new_style"
    action_description = "新风格的 Action"
    
    async def go_activate(self, llm_judge_model=None) -> bool:
        """使用新的激活方式"""
        return await self._keyword_match(
            chat_content,
            keywords=["你好", "hello"],
            case_sensitive=False
        )
    
    async def execute(self) -> tuple[bool, str]:
        return True, "执行成功"
```

### 迁移对照表

| 旧的激活类型 | 新的实现方式 |
|-------------|-------------|
| `ActionActivationType.ALWAYS` | `return True` |
| `ActionActivationType.NEVER` | `return False` |
| `ActionActivationType.RANDOM` | `return await self._random_activation(probability)` |
| `ActionActivationType.KEYWORD` | `return await self._keyword_match( keywords)` |
| `ActionActivationType.LLM_JUDGE` | `return await self._llm_judge_activation(judge_prompt, llm_judge_model)` |

## ⚠️ 注意事项

### 1. 向后兼容性

旧的激活类型配置仍然有效！如果你的 Action 没有重写 `go_activate()` 方法，BaseAction 的默认实现会自动使用旧的配置字段。

### 2. 性能考虑

- `_random_activation()` 和 `_keyword_match()` 非常快速
- `_llm_judge_activation()` 需要调用 LLM，会有延迟
- ActionModifier 会并行执行所有 Action 的 `go_activate()` 方法以提高性能

### 3. 日志记录

工具函数会自动记录调试日志，便于追踪激活决策过程：

```
[DEBUG] 随机激活判断: 概率=0.3, 结果=激活
[DEBUG] 匹配到关键词: ['你好', 'hello']
[DEBUG] LLM 判断结果: 响应='是', 结果=激活
```

### 4. 错误处理

- 如果 `go_activate()` 抛出异常，Action 会被标记为不激活
- `_llm_judge_activation()` 在出错时默认返回 False（不激活）

## 🎨 最佳实践

### 1. 保持 `go_activate()` 方法简洁

```python
# ✅ 好的做法：简洁明了
async def go_activate(self, llm_judge_model=None) -> bool:
    return await self._keyword_match(["帮助", "help"])

# ❌ 不好的做法：过于复杂
async def go_activate(self, llm_judge_model=None) -> bool:
    # 大量复杂的逻辑...
    # 应该拆分成辅助方法
```

### 2. 合理使用 LLM 判断

```python
# ✅ 好的做法：需要语义理解时使用 LLM
async def go_activate(self, llm_judge_model=None) -> bool:
    # 判断用户情绪需要 LLM
    return await self._llm_judge_activation(
        "用户是否情绪低落？",
        llm_judge_model
    )

# ❌ 不好的做法：简单匹配也用 LLM（浪费资源）
async def go_activate(self, llm_judge_model=None) -> bool:
    # 简单的关键词匹配不需要 LLM
    return await self._llm_judge_activation(
        "消息是否包含'你好'？",
        llm_judge_model
    )
```

### 3. 组合条件时使用清晰的逻辑结构

```python
# ✅ 好的做法：清晰的条件组合
async def go_activate(self, llm_judge_model=None) -> bool:
    # 策略 1: 快速路径 - 关键词匹配
    if await self._keyword_match(["紧急", "urgent"]):
        return True
    
    # 策略 2: 随机激活
    if await self._random_activation(0.1):
        return True
    
    # 策略 3: LLM 判断（最耗时，放最后）
    return await self._llm_judge_activation(
        "是否需要特别关注？",
        llm_judge_model
    )
```

## 📖 完整示例项目

查看 `plugins/hello_world_plugin/plugin.py` 获取更多实际示例。

## 🤝 贡献

如果你有更好的激活逻辑实现，欢迎分享！

