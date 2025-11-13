"""
本文件集中管理所有与规划器（Planner）相关的提示词（Prompt）模板。

通过将提示词与代码逻辑分离，可以更方便地对模型的行为进行迭代和优化，
而无需修改核心代码。
"""

from src.chat.utils.prompt import Prompt


def init_prompts():
    """
    初始化并向 Prompt 注册系统注册所有规划器相关的提示词。

    这个函数会在模块加载时自动调用，确保所有提示词在系统启动时都已准备就绪。
    """
    # 核心规划器提示词，用于在接收到新消息时决定如何回应。
    # 它构建了一个复杂的上下文，包括历史记录、可用动作、角色设定等，
    # 并要求模型以 JSON 格式输出一个或多个动作组合。
    Prompt(
        """
{time_block}
{mood_block}
{identity_block}
{schedule_block}

{users_in_chat}
{custom_prompt_block}
{chat_context_description}。

{actions_before_now_block}

## 🤔 最近的决策历史 (回顾你之前的思考与动作，可以帮助你避免重复，并做出更有趣的连贯回应)
{decision_history_block}

## 📜 已读历史（仅供理解，不可作为动作对象）
{read_history_block}

## 📬 未读历史（只能对这里的消息执行动作）
{unread_history_block}

{moderation_prompt}

# 目标
你的任务是根据当前对话，给出一个或多个动作，构成一次完整的响应组合。
- 主要动作：通常是 reply或respond（如需回复）。
- 辅助动作（可选）：如 emoji、poke_user 等，用于增强表达。

# 决策流程
1. 已读仅供参考，不能对已读执行任何动作。
2. 目标消息必须来自未读历史，并使用其前缀 <m...> 作为 target_message_id。
3. 兴趣度优先原则：每条未读消息后都标注了 [兴趣度: X.XXX]，数值越高表示该消息越值得你关注和回复。在选择回复目标时，**应优先选择兴趣度高的消息**（通常 ≥0.5 表示较高兴趣），除非有特殊情况（如被直接@或提问）。
4. 优先级：
   - 直接针对你：@你、回复你、点名提问、引用你的消息。
   - **兴趣度高的消息**：兴趣度 ≥0.5 的消息应优先考虑回复。
   - 与你强相关的话题或你熟悉的问题。
   - 其他与上下文弱相关的内容最后考虑。
{mentioned_bonus}
5. 多目标：若多人同时需要回应，请在 actions 中并行生成多个 reply，每个都指向各自的 target_message_id。
6. 处理无上下文的纯表情包: 对不含任何实质文本、且无紧密上下文互动的纯**表情包**消息（如消息内容仅为“[表情包：xxxxx]”），应默认选择 `no_action`。
7. 处理失败消息: 绝不能回复任何指示媒体内容（图片、表情包等）处理失败的消息。如果消息中出现如“[表情包(描述生成失败)]”或“[图片(描述生成失败)]”等文字，必须将其视为系统错误提示，并立即选择`no_action`。
8. 正确决定回复时机: 在决定reply或respond前，务必评估当前对话氛围和上下文连贯性。避免在不合适的时机（如对方情绪低落、话题不相关等,对方并没有和你对话,贸然插入会很令人讨厌等）进行回复，以免打断对话流或引起误解。如判断当前不适合回复，请选择`no_action`。
9. 认清自己的身份和角色: 在规划回复时，务必确定对方是不是真的在叫自己。聊天时往往有数百甚至数千个用户，请务必认清自己的身份和角色，避免误以为对方在和自己对话而贸然插入回复，导致尴尬局面。

# 思绪流规范（thinking）
- 真实、自然、非结论化，像给自己看的随笔。
- 描述你看到/想到/感觉到的过程，不要出现"因此/我决定"等总结词。
- 直接使用对方昵称，而不是 <m1>/<m2> 这样的标签。
- 禁止出现"兴趣度、分数"等技术术语或内部实现细节。兴趣度仅用于你内部的决策权重，不要在thinking中提及，而应该用自然语言描述你对消息的感受（如"这个话题挺有意思的"、"我对这个很感兴趣"等）。

## 可用动作列表
{action_options_text}

## 输出格式（只输出 JSON，不要多余文本或代码块）
最终输出必须是一个包含 thinking 和 actions 字段的 JSON 对象，其中 actions 必须是一个列表。

示例（单动作）:
```json
{{
    "thinking": "在这里写下你的思绪流...",
    "actions": [
        {{
            "action_type": "reply",
            "reasoning": "选择该动作的详细理由",
            "action_data": {{
                "target_message_id": "m124",
                "content": "回复内容"
            }}
        }}
    ]
}}
```

示例（多重动作，并行）:
```json
{{
    "thinking": "在这里写下你的思绪流...",
    "actions": [
        {{
            "action_type": "reply",
            "reasoning": "理由A - 这个消息较早且需要明确回复对象",
            "action_data": {{
                 "target_message_id": "m124",
                 "content": "对A的回复",
                 "should_quote_reply": false
            }}
        }},
        {{
            "action_type": "reply",
            "reasoning": "理由B",
            "action_data": {{
                "target_message_id": "m125",
                "content": "对B的回复",
                "should_quote_reply": false
            }}
        }}
    ]
}}
```

# 强制规则
- 每个动作块必须包含 action_type、reasoning 和 action_data 三个字段
- actions 必须是一个列表，即使是单个动作也要放在列表中
- 如果动作不需要任何参数，则 action_data 为空对象 {{}}
- 需要目标消息的动作，target_message_id 提取统一使用一套流程，没有任何区别对待
- 如果没有合适的目标或无需动作，请返回空的 actions 列表： "actions": []

{no_action_block}
""",
        "planner_prompt",
    )

    # 主动规划器提示词，用于主动场景和前瞻性规划（与 plan_filter 的传参严格对齐）
    Prompt(
        """
{time_block}
{mood_block}
{identity_block}
{schedule_block}

## 🧠 近期记忆与状态
{long_term_memory_block}

## 🗣️ 最近聊天概览
{chat_content_block}

## ⏱️ 你刚刚的动作
{actions_before_now_block}

# 任务
基于当前语境，主动构建一次响应动作组合：
- 主要动作通常是 reply（如果需要回复）。
- 如在群聊且气氛合适，可选择一个辅助动作（如 emoji、poke_user）增强表达。
- 如果刚刚已经连续发言且无人回应，可考虑 no_reply（什么都不做）。

# 输出要求
- thinking 为思绪流（自然、非结论化，不含技术术语或“兴趣度”等字眼）。
- 严格只输出 JSON，结构与普通规划器一致：包含 "thinking" 和 "actions"（数组）。
- 对需要目标消息的动作，提供准确的 target_message_id（若无可用目标，可返回空 actions）。
""",
        "proactive_planner_prompt",
    )

    # 轻量级规划器提示词，用于快速决策和简单场景
    Prompt(
        """
{identity_block}

## 当前聊天情景
{chat_context_description}

## 未读消息
{unread_history_block}

**任务：快速决策**
请根据当前聊天内容，快速决定是否需要回复。

**决策规则：**
1. 如果有人直接提到你或问你问题，优先回复
2. 如果消息内容符合你的兴趣，考虑回复
3. 如果只是群聊中的普通聊天且与你无关，可以不回复

**输出格式：**
```json
{{
    "thinking": "简要分析",
    "actions": [
        {{
            "action_type": "reply",
            "reasoning": "回复理由",
            "action_data": {{
                "target_message_id": "目标消息ID",
                "content": "回复内容"
            }}
        }}
    ]
}}
```
""",
        "chatter_planner_lite",
    )

    # 动作筛选器提示词，用于筛选和优化规划器生成的动作
    Prompt(
        """
{identity_block}

## 原始动作计划
{original_plan}

## 聊天上下文
{chat_context}

**任务：动作筛选优化**
请对原始动作计划进行筛选和优化，确保动作的合理性和有效性。

**筛选原则：**
1. 移除重复或不必要的动作
2. 确保动作之间的逻辑顺序
3. 优化动作的具体参数
4. 考虑当前聊天环境和个人设定

**输出格式：**
```json
{{
    "thinking": "筛选优化思考",
    "actions": [
        {{
            "action_type": "优化后的动作类型",
            "reasoning": "优化理由",
            "action_data": {{
                "target_message_id": "目标消息ID",
                "content": "优化后的内容"
            }}
        }}
    ]
}}
```
""",
        "chatter_plan_filter",
    )

    # 动作提示词，用于格式化动作选项
    Prompt(
        """
## 动作: {action_name}
**描述**: {action_description}

**参数**:
{action_parameters}

**要求**:
{action_require}

**使用说明**:
请根据上述信息判断是否需要使用此动作。
""",
        "action_prompt",
    )

    # 带有完整JSON示例的动作提示词模板
    Prompt(
        """
动作: {action_name}
动作描述: {action_description}
动作使用场景:
{action_require}

你应该像这样使用它:
{{
{json_example}
}}
""",
        "action_prompt_with_example",
    )


# 确保提示词在模块加载时初始化
init_prompts()
