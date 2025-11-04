# affinity_flow 配置项详解与调整指南

本指南详细说明了 MoFox-Bot `bot_config.toml` 配置文件中 `[affinity_flow]` 区块的各项参数，帮助你根据实际需求调整兴趣评分系统与回复决策系统的行为。

---

## 一、affinity_flow 作用简介

`affinity_flow` 主要用于控制 AI 对消息的兴趣评分（afc），并据此决定是否回复、如何回复、是否发送表情包等。通过合理调整这些参数，可以让 Bot 的回复行为更贴合你的预期。

---

## 二、配置项说明

### 1. 兴趣评分相关参数

- `reply_action_interest_threshold`  
  回复动作兴趣阈值。只有兴趣分高于此值，Bot 才会主动回复消息。
  - **建议调整**：提高此值，Bot 回复更谨慎；降低则更容易回复。

- `non_reply_action_interest_threshold`  
  非回复动作兴趣阈值（如发送表情包等）。兴趣分高于此值时，Bot 可能采取非回复行为。

- `high_match_interest_threshold`  
  高匹配兴趣阈值。关键词匹配度高于此值时，视为高匹配。

- `medium_match_interest_threshold`  
  中匹配兴趣阈值。

- `low_match_interest_threshold`  
  低匹配兴趣阈值。

- `high_match_keyword_multiplier`  
  高匹配关键词兴趣倍率。高匹配关键词对兴趣分的加成倍数。

- `medium_match_keyword_multiplier`  
  中匹配关键词兴趣倍率。

- `low_match_keyword_multiplier`  
  低匹配关键词兴趣倍率。

  匹配关键词数量的加成值。匹配越多，兴趣分越高。

- `max_match_bonus`  
  匹配数加成的最大值。

### 2. 回复决策相关参数

- `no_reply_threshold_adjustment`  
  不回复兴趣阈值调整值。用于动态调整不回复的兴趣阈值。bot每不回复一次，就会在基础阈值上降低该值。

- `reply_cooldown_reduction`  
  回复后减少的不回复计数。回复后，Bot 会更快恢复到基础阈值的状态。

- `max_no_reply_count`  
  最大不回复计数次数。防止 Bot 的回复阈值被过度降低。

### 3. 综合评分权重

- `keyword_match_weight`  
  兴趣关键词匹配度权重。关键词匹配对总兴趣分的影响比例。

- `mention_bot_weight`  
  提及 Bot 分数权重。被提及时兴趣分提升的权重。

- `relationship_weight`  

### 4. 提及 Bot 相关参数

- `mention_bot_adjustment_threshold`  
  提及 Bot 后的调整阈值。当bot被提及后，回复阈值会改变为这个值。

- `strong_mention_interest_score`  
  强提及的兴趣分。强提及包括：被@、被回复、私聊消息。这类提及表示用户明确想与bot交互。

- `weak_mention_interest_score`  
  弱提及的兴趣分。弱提及包括：消息中包含bot的名字或别名（文本匹配）。这类提及可能只是在讨论中提到bot。

- `base_relationship_score`  
---

1. **Bot 太冷漠/回复太少**
   - 降低 `reply_action_interest_threshold`，或降低高中低关键词匹配的阈值。
   
2. **Bot 太热情/回复太多**
   - 提高 `reply_action_interest_threshold`，或降低关键词相关倍率。

3. **希望 Bot 更关注被 @ 或回复的消息**
   - 提高 `strong_mention_interest_score` 或 `mention_bot_weight`。

4. **希望 Bot 对文本提及也积极回应**
   - 提高 `weak_mention_interest_score`。

5. **希望 Bot 更看重关系好的用户**
   - 提高 `relationship_weight` 或 `base_relationship_score`。

6. **表情包行为过于频繁/稀少**
   - 调整 `non_reply_action_interest_threshold`。

---

## 四、参数调整建议流程

1. 明确你希望 Bot 的行为（如更活跃/更安静/更关注特定用户等）。
2. 根据上表找到相关参数，优先调整权重和阈值。
3. 每次只微调一两个参数，观察实际效果。
4. 如需更细致的行为控制，可结合关键词、关系等多项参数综合调整。

---

## 五、示例配置片段

```toml
[affinity_flow]
reply_action_interest_threshold = 1.1
non_reply_action_interest_threshold = 0.9
high_match_interest_threshold = 0.7
medium_match_interest_threshold = 0.4
low_match_interest_threshold = 0.2
high_match_keyword_multiplier = 5
medium_match_keyword_multiplier = 3.75
low_match_keyword_multiplier = 1.3
match_count_bonus = 0.02
max_match_bonus = 0.25
no_reply_threshold_adjustment = 0.01
reply_cooldown_reduction = 5
max_no_reply_count = 20
keyword_match_weight = 0.4
mention_bot_weight = 0.3
relationship_weight = 0.3
mention_bot_adjustment_threshold = 0.5
strong_mention_interest_score = 2.5  # 强提及（@/回复/私聊）
weak_mention_interest_score = 1.5    # 弱提及（文本匹配）
base_relationship_score = 0.3
```

## 六、afc兴趣度评分决策流程详解

MoFox-Bot 在收到每条消息时，会通过一套“兴趣度评分（afc）”决策流程，综合多种因素计算出对该消息的兴趣分，并据此决定是否回复、如何回复或采取其他动作。以下为典型流程说明：

### 1. 关键词匹配与兴趣加成
- Bot 首先分析消息内容，查找是否包含高、中、低匹配的兴趣关键词。
- 不同匹配度的关键词会乘以对应的倍率（high/medium/low_match_keyword_multiplier），并根据匹配数量叠加加成（match_count_bonus，max_match_bonus）。

### 2. 提及与关系加分
- 如果消息中提及了 Bot，会根据提及类型获得不同的兴趣分：
  * **强提及**（被@、被回复、私聊）: 获得 `strong_mention_interest_score` 分值，表示用户明确想与bot交互
  * **弱提及**（文本中包含bot名字或别名）: 获得 `weak_mention_interest_score` 分值，表示在讨论中提到bot
  * 提及分按权重（`mention_bot_weight`）计入总分
- 与用户的关系分（base_relationship_score 及动态关系分）也会按 relationship_weight 计入总分。

### 3. 综合评分计算
- 最终兴趣分 = 关键词匹配分 × keyword_match_weight + 提及分 × mention_bot_weight + 关系分 × relationship_weight。
- 你可以通过调整各权重，决定不同因素对总兴趣分的影响。

### 4. 阈值判定与回复决策
- 若兴趣分高于 reply_action_interest_threshold，Bot 会主动回复。
- 若兴趣分高于 non_reply_action_interest_threshold，但低于回复阈值，Bot 可能采取如发送表情包等非回复行为。
- 若兴趣分均未达到阈值，则不回复。

### 5. 动态阈值调整机制
- Bot 连续多次不回复时，reply_action_interest_threshold 会根据 no_reply_threshold_adjustment 逐步降低，最多降低 max_no_reply_count 次，防止长时间沉默。
- 回复后，阈值通过 reply_cooldown_reduction 恢复。
- 被@时，阈值可临时调整为 mention_bot_adjustment_threshold。

### 6. 典型决策流程图

1. 收到消息 → 2. 关键词/提及/关系分计算 → 3. 综合兴趣分加权 → 4. 与阈值比较 → 5. 决定回复/表情/忽略

通过理解上述流程，你可以有针对性地调整各项参数，让 Bot 的回复行为更贴合你的需求。