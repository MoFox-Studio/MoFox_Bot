"""
回复动作模块

定义了两种回复动作：
- reply: 针对单条消息的深度回复（使用 s4u 模板）
- respond: 对未读消息的统一回应（使用 normal 模板）
"""

from typing import ClassVar

from src.common.logger import get_logger
from src.plugin_system import ActionActivationType, BaseAction, ChatMode

logger = get_logger("reply_actions")


class ReplyAction(BaseAction):
    """Reply动作 - 针对单条消息的深度回复
    
    特点：
    - 使用 s4u (Speak for You) 模板
    - 专注于理解和回应单条消息的具体内容
    - 适合 Focus 模式下的精准回复
    """

    # 动作基本信息
    action_name = "reply"
    action_description = "针对特定消息进行精准回复。深度理解并回应单条消息的具体内容。需要指定目标消息ID。"

    # 激活设置
    activation_type = ActionActivationType.ALWAYS  # 回复动作总是可用
    mode_enable = ChatMode.ALL  # 在所有模式下都可用
    parallel_action = False  # 回复动作不能与其他动作并行

    # 动作参数定义
    action_parameters: ClassVar = {
        "target_message_id": "要回复的目标消息ID（必需，来自未读消息的 <m...> 标签）",
        "content": "回复的具体内容（可选，由LLM生成）",
        "should_quote_reply": "是否引用原消息（可选，true/false，默认false。群聊中回复较早消息或需要明确指向时使用true）",
    }

    # 动作使用场景
    action_require: ClassVar = [
        "需要针对特定消息进行精准回复时使用",
        "适合单条消息的深度理解和回应",
        "必须提供准确的 target_message_id（来自未读历史的 <m...> 标签）",
        "私聊场景必须使用此动作（不支持 respond）",
        "群聊中需要明确回应某个特定用户或问题时使用",
        "关注单条消息的具体内容和上下文细节",
    ]

    # 关联类型
    associated_types: ClassVar[list[str]] = ["text"]

    async def execute(self) -> tuple[bool, str]:
        """执行reply动作
        
        注意：实际的回复生成由 action_manager 统一处理
        这里只是标记使用 reply 动作（s4u 模板）
        """
        logger.info(f"{self.log_prefix} 使用 reply 动作（s4u 模板）")
        return True, ""


class RespondAction(BaseAction):
    """Respond动作 - 对未读消息的统一回应
    
    特点：
    - 关注整体对话动态和未读消息的统一回应
    - 适合对于群聊消息下的宏观回应
    - 避免与单一用户深度对话而忽略其他用户的消息
    """

    # 动作基本信息
    action_name = "respond"
    action_description = "统一回应所有未读消息。理解整体对话动态和话题走向，生成连贯的回复。无需指定目标消息。"

    # 激活设置
    activation_type = ActionActivationType.ALWAYS  # 回应动作总是可用
    mode_enable = ChatMode.ALL  # 在所有模式下都可用
    parallel_action = False  # 回应动作不能与其他动作并行

    # 动作参数定义
    action_parameters: ClassVar = {
        "content": "回复的具体内容（可选，由LLM生成）",
    }

    # 动作使用场景
    action_require: ClassVar = [
        "需要统一回应多条未读消息时使用（Normal 模式专用）",
        "适合理解整体对话动态而非单条消息",
        "不需要指定 target_message_id，会自动处理所有未读消息",
        "关注对话流程、话题走向和整体氛围",
        "适合群聊中的自然对话流，无需精确指向特定消息",
        "可以同时回应多个话题或参与者",
    ]

    # 关联类型
    associated_types: ClassVar[list[str]] = ["text"]

    async def execute(self) -> tuple[bool, str]:
        """执行respond动作
        
        注意：实际的回复生成由 action_manager 统一处理
        这里只是标记使用 respond 动作（normal 模板）
        """
        logger.info(f"{self.log_prefix} 使用 respond 动作（normal 模板）")
        return True, ""
