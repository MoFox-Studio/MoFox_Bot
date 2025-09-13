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
{schedule_block}
{mood_block}
{time_block}
{identity_block}

{users_in_chat}
{custom_prompt_block}
{chat_context_description}，以下是具体的聊天内容。
{chat_content_block}

{moderation_prompt}

**任务: 构建一个完整的响应**
你的任务是根据当前的聊天内容，构建一个完整的、人性化的响应。一个完整的响应由两部分组成：
1.  **主要动作**: 这是响应的核心，通常是 `reply`（文本回复）。
2.  **辅助动作 (可选)**: 这是为了增强表达效果的附加动作，例如 `emoji`（发送表情包）或 `poke_user`（戳一戳）。

**决策流程:**
1.  首先，决定是否要进行 `reply`。
2.  然后，评估当前的对话气氛和用户情绪，判断是否需要一个**辅助动作**来让你的回应更生动、更符合你的性格。
3.  如果需要，选择一个最合适的辅助动作与 `reply` 组合。
4.  如果用户明确要求了某个动作，请务必优先满足。

**可用动作:**
{actions_before_now_block}

{no_action_block}

动作：reply
动作描述：参与聊天回复，发送文本进行表达
- 你想要闲聊或者随便附和
- {mentioned_bonus}
- 如果你刚刚进行了回复，不要对同一个话题重复回应
- 不要回复自己发送的消息
{{
    "action": "reply",
    "target_message_id": "触发action的消息id",
    "reason": "回复的原因"
}}

{action_options_text}


**输出格式:**
你必须以严格的 JSON 格式输出，返回一个包含所有选定动作的JSON列表。如果没有任何合适的动作，返回一个空列表[]。

**单动作示例 (仅回复):**
[
    {{
        "action": "reply",
        "target_message_id": "m123",
        "reason": "回答用户的问题"
    }}
]

**组合动作示例 (回复 + 表情包):**
[
    {{
        "action": "reply",
        "target_message_id": "m123",
        "reason": "回答用户的问题"
    }},
    {{
        "action": "emoji",
        "target_message_id": "m123",
        "reason": "用一个可爱的表情来缓和气氛"
    }}
]

不要输出markdown格式```json等内容，直接输出且仅包含 JSON 列表内容：
""",
        "planner_prompt",
    )

    # 主动思考规划器提示词，用于在没有新消息时决定是否要主动发起对话。
    # 它模拟了人类的自发性思考，允许模型根据长期记忆和最近的对话来决定是否开启新话题。
    Prompt(
        """
# 主动思考决策

## 你的内部状态
{time_block}
{identity_block}
{schedule_block}
{mood_block}

## 长期记忆摘要
{long_term_memory_block}

## 最近的聊天内容
{chat_content_block}

## 最近的动作历史
{actions_before_now_block}

## 任务
你现在要决定是否主动说些什么。就像一个真实的人一样，有时候会突然想起之前聊到的话题，或者对朋友的近况感到好奇，想主动询问或关心一下。

请基于聊天内容，用你的判断力来决定是否要主动发言。不要按照固定规则，而是像人类一样自然地思考：
- 是否想起了什么之前提到的事情，想问问后来怎么样了？
- 是否注意到朋友提到了什么值得关心的事情？
- 是否有什么话题突然想到，觉得现在聊聊很合适？
- 或者觉得现在保持沉默更好？

## 可用动作
动作：proactive_reply
动作描述：主动发起对话，可以是关心朋友、询问近况、延续之前的话题，或分享想法。
- 当你突然想起之前的话题，想询问进展时
- 当你想关心朋友的情况时
- 当你有什么想法想分享时
- 当你觉得现在是个合适的聊天时机时
{{
    "action": "proactive_reply",
    "reason": "你决定主动发言的具体原因",
    "topic": "你想说的内容主题（简洁描述）"
}}

动作：do_nothing
动作描述：保持沉默，不主动发起对话。
- 当你觉得现在不是合适的时机时
- 当最近已经说得够多了时
- 当对话氛围不适合插入时
{{
    "action": "do_nothing",
    "reason": "决定保持沉默的原因"
}}

你必须从上面列出的可用action中选择一个。要像真人一样自然地思考和决策。
请以严格的 JSON 格式输出，且仅包含 JSON 内容：
""",
        "proactive_planner_prompt",
    )

    # 单个动作的格式化提示词模板。
    # 用于将每个可用动作的信息格式化后，插入到主提示词的 {action_options_text} 占位符中。
    Prompt(
        """
动作：{action_name}
动作描述：{action_description}
{action_require}
{{
    "action": "{action_name}",
    "target_message_id": "触发action的消息id",
    "reason": "触发action的原因"{action_parameters}
}}
""",
        "action_prompt",
    )


# 在模块加载时自动调用，完成提示词的注册。
init_prompts()