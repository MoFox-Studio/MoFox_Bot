from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from src.plugin_system.base.component_types import ChatType

from . import BaseDataModel

if TYPE_CHECKING:
    from src.plugin_system.base.component_types import ActionInfo, ChatMode

    from .database_data_model import DatabaseMessages


@dataclass
class TargetPersonInfo(BaseDataModel):
    platform: str = field(default_factory=str)
    user_id: str = field(default_factory=str)
    user_nickname: str = field(default_factory=str)
    person_id: str | None = None
    person_name: str | None = None


@dataclass
class ActionPlannerInfo(BaseDataModel):
    action_type: str = field(default_factory=str)
    reasoning: str | None = None
    action_data: dict | None = None
    action_message: Optional["DatabaseMessages"] = None
    available_actions: dict[str, "ActionInfo"] | None = None

@dataclass
class InterestScore(BaseDataModel):
    """兴趣度评分结果"""

    message_id: str
    total_score: float
    interest_match_score: float
    relationship_score: float
    mentioned_score: float
    details: dict[str, str]


@dataclass
class Plan(BaseDataModel):
    """
    统一规划数据模型
    """

    chat_id: str
    mode: "ChatMode"

    chat_type: "ChatType"
    # Generator 填充
    available_actions: dict[str, "ActionInfo"] = field(default_factory=dict)
    chat_history: list["DatabaseMessages"] = field(default_factory=list)
    target_info: TargetPersonInfo | None = None

    # Filter 填充
    llm_prompt: str | None = None
    decided_actions: list[ActionPlannerInfo] | None = None
