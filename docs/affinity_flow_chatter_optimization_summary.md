# Affinity Flow Chatter 插件优化总结

## 更新日期
2025年11月3日

## 优化概述

本次对 Affinity Flow Chatter 插件进行了全面的重构和优化，主要包括目录结构优化、性能改进、bug修复和新功能添加。

## � 任务-1: 细化提及分数机制（强提及 vs 弱提及）

### 变更内容
将原有的统一提及分数细化为**强提及**和**弱提及**两种类型，使用不同的分值。

### 原设计问题
**旧逻辑**：
- ❌ 所有提及方式使用同一个分值（`mention_bot_interest_score`）
- ❌ 被@、私聊、文本提到名字都是相同的重要性
- ❌ 无法区分用户的真实意图

### 新设计

#### 强提及（Strong Mention）
**定义**：用户**明确**想与bot交互
- ✅ 被 @ 提及
- ✅ 被回复
- ✅ 私聊消息

**分值**：`strong_mention_interest_score = 2.5`（默认）

#### 弱提及（Weak Mention）
**定义**：在讨论中**顺带**提到bot
- ✅ 消息中包含bot名字
- ✅ 消息中包含bot别名

**分值**：`weak_mention_interest_score = 1.5`（默认）

### 检测逻辑

```python
def is_mentioned_bot_in_message(message) -> tuple[bool, float]:
    """
    Returns:
        tuple[bool, float]: (是否提及, 提及类型)
        提及类型: 0=未提及, 1=弱提及, 2=强提及
    """
    # 1. 检查私聊 → 强提及
    if is_private_chat:
        return True, 2.0
    
    # 2. 检查 @ → 强提及
    if is_at:
        return True, 2.0
    
    # 3. 检查回复 → 强提及
    if is_replied:
        return True, 2.0
    
    # 4. 检查文本匹配 → 弱提及
    if text_contains_bot_name_or_alias:
        return True, 1.0
    
    return False, 0.0
```

### 配置参数

**config/bot_config.toml**:
```toml
[affinity_flow]
# 提及bot相关参数
strong_mention_interest_score = 2.5  # 强提及（@/回复/私聊）
weak_mention_interest_score = 1.5    # 弱提及（文本匹配）
```

### 实际效果对比

**场景1：被@**
```
用户: "@小狐 你好呀"
旧逻辑: 提及分 = 2.5
新逻辑: 提及分 = 2.5 (强提及) ✅ 保持不变
```

**场景2：回复bot**
```
用户: [回复 小狐：...] "是的"
旧逻辑: 提及分 = 2.5
新逻辑: 提及分 = 2.5 (强提及) ✅ 保持不变
```

**场景3：私聊**
```
用户: "在吗"
旧逻辑: 提及分 = 2.5
新逻辑: 提及分 = 2.5 (强提及) ✅ 保持不变
```

**场景4：文本提及**
```
用户: "小狐今天没来吗"
旧逻辑: 提及分 = 2.5 (可能过高)
新逻辑: 提及分 = 1.5 (弱提及) ✅ 更合理
```

**场景5：讨论bot**
```
用户A: "小狐这个bot挺有意思的"
旧逻辑: 提及分 = 2.5 (bot可能会插话)
新逻辑: 提及分 = 1.5 (弱提及，降低打断概率) ✅ 更自然
```

### 优势

- ✅ **意图识别**：区分"想对话"和"在讨论"
- ✅ **减少误判**：降低在他人讨论中插话的概率
- ✅ **灵活调节**：可以独立调整强弱提及的权重
- ✅ **向后兼容**：保持原有强提及的行为不变

### 影响文件

- `config/bot_config.toml`：添加 `strong/weak_mention_interest_score` 配置
- `template/bot_config_template.toml`：同步模板配置
- `src/config/official_configs.py`：添加配置字段定义
- `src/chat/utils/utils.py`：修改 `is_mentioned_bot_in_message()` 函数
- `src/plugins/built_in/affinity_flow_chatter/core/affinity_interest_calculator.py`：使用新的强弱提及逻辑
- `docs/affinity_flow_guide.md`：更新文档说明

---

## �🆔 任务0: 修改 Personality ID 生成逻辑

### 变更内容
将 `bot_person_id` 从固定值改为基于人设文本的 hash 生成，实现人设变化时自动触发兴趣标签重新生成。

### 原设计问题
**旧逻辑**：
```python
self.bot_person_id = person_info_manager.get_person_id("system", "bot_id")
# 结果：md5("system_bot_id") = 固定值
```
- ❌ personality_id 固定不变
- ❌ 人设修改后不会重新生成兴趣标签
- ❌ 需要手动清空数据库才能触发重新生成

### 新设计
**新逻辑**：
```python
personality_hash, _ = self._get_config_hash(bot_nickname, personality_core, personality_side, identity)
self.bot_person_id = personality_hash
# 结果：md5(人设配置的JSON) = 动态值
```

### Hash 生成规则
```python
personality_config = {
    "nickname": bot_nickname,
    "personality_core": personality_core,
    "personality_side": personality_side,
    "compress_personality": global_config.personality.compress_personality,
}
personality_hash = md5(json_dumps(personality_config, sorted=True))
```

### 工作原理
1. **初始化时**：根据当前人设配置计算 hash 作为 personality_id
2. **配置变化检测**：
   - 计算当前人设的 hash
   - 与上次保存的 hash 对比
   - 如果不同，触发重新生成
3. **兴趣标签生成**：
   - `bot_interest_manager` 根据 personality_id 查询数据库
   - 如果 personality_id 不存在（人设变化了），自动生成新的兴趣标签
   - 保存时使用新的 personality_id

### 优势
- ✅ **自动检测**：人设改变后无需手动操作
- ✅ **数据隔离**：不同人设的兴趣标签分开存储
- ✅ **版本管理**：可以保留历史人设的兴趣标签（如果需要）
- ✅ **逻辑清晰**：personality_id 直接反映人设内容

### 示例
```
人设 A:
  nickname: "小狐"
  personality_core: "活泼开朗"
  personality_side: "喜欢编程"
  → personality_id: a1b2c3d4e5f6...

人设 B (修改后):
  nickname: "小狐"
  personality_core: "冷静理性"  ← 改变
  personality_side: "喜欢编程"
  → personality_id: f6e5d4c3b2a1...  ← 自动生成新ID

结果：
- 数据库查询时找不到 f6e5d4c3b2a1 的兴趣标签
- 自动触发重新生成
- 新兴趣标签保存在 f6e5d4c3b2a1 下
```

### 影响范围
- `src/individuality/individuality.py`：personality_id 生成逻辑
- `src/chat/interest_system/bot_interest_manager.py`：兴趣标签加载/保存（已支持）
- 数据库：`bot_personality_interests` 表通过 personality_id 字段关联

---

## 📁 任务1: 优化插件目录结构

### 变更内容
将原本扁平的文件结构重组为分层目录，提高代码可维护性：

```
affinity_flow_chatter/
├── core/                          # 核心模块
│   ├── __init__.py
│   ├── affinity_chatter.py       # 主聊天处理器
│   └── affinity_interest_calculator.py  # 兴趣度计算器
│
├── planner/                       # 规划器模块
│   ├── __init__.py
│   ├── planner.py                # 动作规划器
│   ├── planner_prompts.py        # 提示词模板
│   ├── plan_generator.py         # 计划生成器
│   ├── plan_filter.py            # 计划过滤器
│   └── plan_executor.py          # 计划执行器
│
├── proactive/                     # 主动思考模块
│   ├── __init__.py
│   ├── proactive_thinking_scheduler.py   # 主动思考调度器
│   ├── proactive_thinking_executor.py    # 主动思考执行器
│   └── proactive_thinking_event.py       # 主动思考事件
│
├── tools/                         # 工具模块
│   ├── __init__.py
│   ├── chat_stream_impression_tool.py    # 聊天印象工具
│   └── user_profile_tool.py              # 用户档案工具
│
├── plugin.py                      # 插件注册
├── __init__.py                    # 插件元数据
└── README.md                      # 文档
```

### 优势
- ✅ **逻辑清晰**：相关功能集中在同一目录
- ✅ **易于维护**：模块职责明确，便于定位和修改
- ✅ **可扩展性**：新功能可以轻松添加到对应目录
- ✅ **团队协作**：多人开发时减少文件冲突

---

## 💾 任务2: 修改 Embedding 存储策略

### 问题分析
**原设计**：兴趣标签的 embedding 向量（2560维度浮点数组）直接存储在数据库中
- ❌ 数据库存储过长，可能导致写入失败
- ❌ 每次加载需要反序列化大量数据
- ❌ 数据库体积膨胀

### 解决方案
**新设计**：Embedding 改为启动时动态生成并缓存在内存中

#### 实现细节

**1. 数据库存储**（不再包含 embedding）：
```python
# 保存时
tag_dict = {
    "tag_name": tag.tag_name,
    "weight": tag.weight,
    "expanded": tag.expanded,  # 扩展描述
    "created_at": tag.created_at.isoformat(),
    "updated_at": tag.updated_at.isoformat(),
    "is_active": tag.is_active,
    # embedding 不再存储
}
```

**2. 启动时动态生成**：
```python
async def _generate_embeddings_for_tags(self, interests: BotPersonalityInterests):
    """为所有兴趣标签生成embedding（仅缓存在内存中）"""
    for tag in interests.interest_tags:
        if tag.tag_name in self.embedding_cache:
            # 使用内存缓存
            tag.embedding = self.embedding_cache[tag.tag_name]
        else:
            # 动态生成新的embedding
            embedding = await self._get_embedding(tag.tag_name)
            tag.embedding = embedding  # 设置到内存对象
            self.embedding_cache[tag.tag_name] = embedding  # 缓存
```

**3. 加载时处理**：
```python
tag = BotInterestTag(
    tag_name=tag_data.get("tag_name", ""),
    weight=tag_data.get("weight", 0.5),
    expanded=tag_data.get("expanded"),
    embedding=None,  # 不从数据库加载，改为动态生成
    # ...
)
```

### 优势
- ✅ **数据库轻量化**：数据库只存储标签名和权重等元数据
- ✅ **避免写入失败**：不再因为数据过长导致数据库操作失败
- ✅ **灵活性**：可以随时切换 embedding 模型而无需迁移数据
- ✅ **性能**：内存缓存访问速度快

### 权衡
- ⚠️ 启动时需要生成 embedding（首次启动稍慢，约10-20秒）
- ✅ 后续运行时使用内存缓存，性能与原来相当

---

## 🔧 任务3: 修复连续不回复阈值调整问题

### 问题描述
原实现中，连续不回复调整只提升了分数，但阈值保持不变：
```python
# ❌ 错误的实现
adjusted_score = self._apply_no_reply_boost(total_score)  # 只提升分数
should_reply = adjusted_score >= self.reply_threshold  # 阈值不变
```

**问题**：动作阈值（`non_reply_action_interest_threshold`）没有被调整，导致即使回复阈值满足，动作阈值可能仍然不满足。

### 解决方案
改为**同时降低回复阈值和动作阈值**：

```python
def _apply_no_reply_threshold_adjustment(self) -> tuple[float, float]:
    """应用阈值调整（包括连续不回复和回复后降低机制）"""
    base_reply_threshold = self.reply_threshold
    base_action_threshold = global_config.affinity_flow.non_reply_action_interest_threshold
    
    total_reduction = 0.0
    
    # 连续不回复的阈值降低
    if self.no_reply_count > 0:
        no_reply_reduction = self.no_reply_count * self.probability_boost_per_no_reply
        total_reduction += no_reply_reduction
    
    # 应用到两个阈值
    adjusted_reply_threshold = max(0.0, base_reply_threshold - total_reduction)
    adjusted_action_threshold = max(0.0, base_action_threshold - total_reduction)
    
    return adjusted_reply_threshold, adjusted_action_threshold
```

**使用**：
```python
# ✅ 正确的实现
adjusted_reply_threshold, adjusted_action_threshold = self._apply_no_reply_threshold_adjustment()
should_reply = adjusted_score >= adjusted_reply_threshold
should_take_action = adjusted_score >= adjusted_action_threshold
```

### 优势
- ✅ **逻辑一致**：回复阈值和动作阈值同步调整
- ✅ **避免矛盾**：不会出现"满足回复但不满足动作"的情况
- ✅ **更合理**：连续不回复时，bot更容易采取任何行动

---

## ⏱️ 任务4: 添加兴趣度计算超时机制

### 问题描述
兴趣匹配计算调用 embedding API，可能因为网络问题或模型响应慢导致：
- ❌ 长时间等待（>5秒）
- ❌ 整体超时导致强制使用默认分值
- ❌ **丢失了提及分和关系分**（因为整个计算被中断）

### 解决方案
为兴趣匹配计算添加**1.5秒超时保护**，超时时返回默认分值：

```python
async def _calculate_interest_match_score(self, content: str, keywords: list[str] | None = None) -> float:
    """计算兴趣匹配度（带超时保护）"""
    try:
        # 使用 asyncio.wait_for 添加1.5秒超时
        match_result = await asyncio.wait_for(
            bot_interest_manager.calculate_interest_match(content, keywords or []),
            timeout=1.5
        )
        
        if match_result:
            # 正常计算分数
            final_score = match_result.overall_score * 1.15 * match_result.confidence + match_count_bonus
            return final_score
        else:
            return 0.0
    
    except asyncio.TimeoutError:
        # 超时时返回默认分值 0.5
        logger.warning("⏱️ 兴趣匹配计算超时(>1.5秒)，返回默认分值0.5以保留其他分数")
        return 0.5  # 避免丢失提及分和关系分
    
    except Exception as e:
        logger.warning(f"智能兴趣匹配失败: {e}")
        return 0.0
```

### 工作流程
```
正常情况（<1.5秒）:
  兴趣匹配分: 0.8 + 关系分: 0.3 + 提及分: 2.5 = 3.6 ✅

超时情况（>1.5秒）:
  兴趣匹配分: 0.5（默认）+ 关系分: 0.3 + 提及分: 2.5 = 3.3 ✅
  （保留了关系分和提及分）

强制中断（无超时保护）:
  整体计算失败 = 0.0（默认） ❌
  （丢失了所有分数）
```

### 优势
- ✅ **防止阻塞**：不会因为一个API调用卡住整个流程
- ✅ **保留分数**：即使兴趣匹配超时，提及分和关系分依然有效
- ✅ **用户体验**：响应更快，不会长时间无反应
- ✅ **降级优雅**：超时时仍能给出合理的默认值

---

## 🔄 任务5: 实现回复后阈值降低机制

### 需求背景
**目标**：让bot在回复后更容易进行连续对话，提升对话的连贯性和自然性。

**场景示例**：
```
用户: "你好呀"
Bot: "你好！今天过得怎么样？" ← 此时激活连续对话模式

用户: "还不错"
Bot: "那就好～有什么有趣的事情吗？" ← 阈值降低，更容易回复

用户: "没什么"
Bot: "嗯嗯，那要不要聊聊别的？" ← 仍然更容易回复

用户: "..."
（如果一直不回复，降低效果会逐渐衰减）
```

### 配置项
在 `bot_config.toml` 中添加：

```toml
# 回复后连续对话机制参数
enable_post_reply_boost = true  # 是否启用回复后阈值降低机制
post_reply_threshold_reduction = 0.15  # 回复后初始阈值降低值
post_reply_boost_max_count = 3  # 回复后阈值降低的最大持续次数
post_reply_boost_decay_rate = 0.5  # 每次回复后阈值降低衰减率（0-1）
```

### 实现细节

**1. 初始化计数器**：
```python
def __init__(self):
    # 回复后阈值降低机制
    self.enable_post_reply_boost = affinity_config.enable_post_reply_boost
    self.post_reply_boost_remaining = 0  # 剩余的回复后降低次数
    self.post_reply_threshold_reduction = affinity_config.post_reply_threshold_reduction
    self.post_reply_boost_max_count = affinity_config.post_reply_boost_max_count
    self.post_reply_boost_decay_rate = affinity_config.post_reply_boost_decay_rate
```

**2. 阈值调整**：
```python
def _apply_no_reply_threshold_adjustment(self) -> tuple[float, float]:
    """应用阈值调整"""
    total_reduction = 0.0
    
    # 1. 连续不回复的降低
    if self.no_reply_count > 0:
        no_reply_reduction = self.no_reply_count * self.probability_boost_per_no_reply
        total_reduction += no_reply_reduction
    
    # 2. 回复后的降低（带衰减）
    if self.enable_post_reply_boost and self.post_reply_boost_remaining > 0:
        # 计算衰减因子
        decay_factor = self.post_reply_boost_decay_rate ** (
            self.post_reply_boost_max_count - self.post_reply_boost_remaining
        )
        post_reply_reduction = self.post_reply_threshold_reduction * decay_factor
        total_reduction += post_reply_reduction
    
    # 应用总降低量
    adjusted_reply_threshold = max(0.0, base_reply_threshold - total_reduction)
    adjusted_action_threshold = max(0.0, base_action_threshold - total_reduction)
    
    return adjusted_reply_threshold, adjusted_action_threshold
```

**3. 状态更新**：
```python
def on_reply_sent(self):
    """当机器人发送回复后调用"""
    if self.enable_post_reply_boost:
        # 重置回复后降低计数器
        self.post_reply_boost_remaining = self.post_reply_boost_max_count
        # 同时重置不回复计数
        self.no_reply_count = 0

def on_message_processed(self, replied: bool):
    """消息处理完成后调用"""
    # 更新不回复计数
    self.update_no_reply_count(replied)
    
    # 如果已回复，激活回复后降低机制
    if replied:
        self.on_reply_sent()
    else:
        # 如果没有回复，减少回复后降低剩余次数
        if self.post_reply_boost_remaining > 0:
            self.post_reply_boost_remaining -= 1
```

### 衰减机制说明

**衰减公式**：
```
decay_factor = decay_rate ^ (max_count - remaining_count)
actual_reduction = base_reduction * decay_factor
```

**示例**（`base_reduction=0.15`, `decay_rate=0.5`, `max_count=3`）：
```
第1次回复后: decay_factor = 0.5^0 = 1.00, reduction = 0.15 * 1.00 = 0.15
第2次回复后: decay_factor = 0.5^1 = 0.50, reduction = 0.15 * 0.50 = 0.075
第3次回复后: decay_factor = 0.5^2 = 0.25, reduction = 0.15 * 0.25 = 0.0375
```

### 实际效果

**配置示例**：
- 回复阈值: 0.7
- 初始降低值: 0.15
- 最大次数: 3
- 衰减率: 0.5

**对话流程**：
```
初始状态:
  回复阈值: 0.7
  
Bot发送回复 → 激活连续对话模式:
  剩余次数: 3
  
第1条消息:
  阈值降低: 0.15
  实际阈值: 0.7 - 0.15 = 0.55 ✅ 更容易回复
  
第2条消息:
  阈值降低: 0.075 (衰减)
  实际阈值: 0.7 - 0.075 = 0.625
  
第3条消息:
  阈值降低: 0.0375 (继续衰减)
  实际阈值: 0.7 - 0.0375 = 0.6625
  
第4条消息:
  降低结束，恢复正常阈值: 0.7
```

### 优势
- ✅ **连贯对话**：bot回复后更容易继续对话
- ✅ **自然衰减**：避免无限连续回复，逐渐恢复正常
- ✅ **可配置**：可以根据需求调整降低值、次数和衰减率
- ✅ **灵活控制**：可以随时启用/禁用此功能

---

## 📊 整体影响

### 性能优化
- ✅ **内存优化**：不再在数据库中存储大量 embedding 数据
- ✅ **响应速度**：超时保护避免长时间等待
- ✅ **启动速度**：首次启动需要生成 embedding（10-20秒），后续运行使用缓存

### 功能增强
- ✅ **阈值调整**：修复了回复和动作阈值不一致的问题
- ✅ **连续对话**：新增回复后阈值降低机制，提升对话连贯性
- ✅ **容错能力**：超时保护确保即使API失败也能保留其他分数

### 代码质量
- ✅ **目录结构**：清晰的模块划分，易于维护
- ✅ **可扩展性**：新功能可以轻松添加到对应目录
- ✅ **可配置性**：关键参数可通过配置文件调整

---

## 🔧 使用说明

### 配置调整

在 `config/bot_config.toml` 中调整回复后连续对话参数：

```toml
[affinity_flow]
# 回复后连续对话机制
enable_post_reply_boost = true  # 启用/禁用
post_reply_threshold_reduction = 0.15  # 初始降低值（建议0.1-0.2）
post_reply_boost_max_count = 3  # 持续次数（建议2-5）
post_reply_boost_decay_rate = 0.5  # 衰减率（建议0.3-0.7）
```

### 调用方式

在 planner 或其他需要的地方调用：

```python
# 计算兴趣值
result = await interest_calculator.execute(message)

# 消息处理完成后更新状态
interest_calculator.on_message_processed(replied=result.should_reply)
```

---

## 🐛 已知问题

暂无

---

## 📝 后续优化建议

1. **监控日志**：观察实际使用中的阈值调整效果
2. **A/B测试**：对比启用/禁用回复后降低机制的对话质量
3. **参数调优**：根据实际使用情况调整默认配置值
4. **性能监控**：监控 embedding 生成的时间和缓存命中率

---

## 👥 贡献者

- GitHub Copilot - 代码实现和文档编写

---

## 📅 更新历史

- 2025-11-03: 完成所有5个任务的实现
  - ✅ 优化插件目录结构
  - ✅ 修改 embedding 存储策略
  - ✅ 修复连续不回复阈值调整
  - ✅ 添加超时保护机制
  - ✅ 实现回复后阈值降低
