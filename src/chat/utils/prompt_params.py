"""
This module contains the PromptParameters class, which is used to define the parameters for a prompt.
"""
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class PromptParameters:
    """统一提示词参数系统"""

    # 基础参数
    chat_id: str = ""
    is_group_chat: bool = False
    sender: str = ""
    target: str = ""
    reply_to: str = ""
    extra_info: str = ""
    prompt_mode: Literal["s4u", "normal", "minimal"] = "s4u"
    bot_name: str = ""
    bot_nickname: str = ""

    # 功能开关
    enable_tool: bool = True
    enable_memory: bool = True
    enable_expression: bool = True
    enable_relation: bool = True
    enable_cross_context: bool = True
    enable_knowledge: bool = True
    enable_attention_optimization: bool = True  # 注意力优化开关

    # 性能控制
    max_context_messages: int = 50

    # 调试选项
    debug_mode: bool = False

    # 聊天历史和上下文
    chat_target_info: dict[str, Any] | None = None
    message_list_before_now_long: list[dict[str, Any]] = field(default_factory=list)
    message_list_before_short: list[dict[str, Any]] = field(default_factory=list)
    chat_talking_prompt_short: str = ""
    target_user_info: dict[str, Any] | None = None

    # 已构建的内容块
    expression_habits_block: str = ""
    relation_info_block: str = ""
    memory_block: str = ""
    tool_info_block: str = ""
    knowledge_prompt: str = ""
    cross_context_block: str = ""

    # 其他内容块
    keywords_reaction_prompt: str = ""
    extra_info_block: str = ""
    auth_role_prompt_block: str = ""
    time_block: str = ""
    identity_block: str = ""
    schedule_block: str = ""
    moderation_prompt_block: str = ""
    safety_guidelines_block: str = ""
    reply_target_block: str = ""
    mood_prompt: str = ""
    action_descriptions: str = ""
    notice_block: str = ""

    # 可用动作信息
    available_actions: dict[str, Any] | None = None

    # 动态生成的聊天场景提示
    chat_scene: str = ""

    def validate(self) -> list[str]:
        """参数验证"""
        errors = []
        if not self.chat_id:
            errors.append("chat_id不能为空")
        if self.prompt_mode not in ["s4u", "normal", "minimal"]:
            errors.append("prompt_mode必须是's4u'、'normal'或'minimal'")
        if self.max_context_messages <= 0:
            errors.append("max_context_messages必须大于0")
        return errors
