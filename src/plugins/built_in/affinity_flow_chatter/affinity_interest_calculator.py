"""AffinityFlow 风格兴趣值计算组件

基于原有的 AffinityFlow 兴趣度评分系统，提供标准化的兴趣值计算功能
"""

import time
from typing import TYPE_CHECKING

import orjson

from src.chat.interest_system import bot_interest_manager
from src.common.logger import get_logger
from src.config.config import global_config
from src.plugin_system.base.base_interest_calculator import BaseInterestCalculator, InterestCalculationResult

if TYPE_CHECKING:
    from src.common.data_models.database_data_model import DatabaseMessages

logger = get_logger("affinity_interest_calculator")


class AffinityInterestCalculator(BaseInterestCalculator):
    """AffinityFlow 风格兴趣值计算组件"""

    # 直接定义类属性
    component_name = "affinity_interest_calculator"
    component_version = "1.0.0"
    component_description = "基于AffinityFlow逻辑的兴趣值计算组件，使用智能兴趣匹配和用户关系评分"

    def __init__(self):
        super().__init__()

        # 智能兴趣匹配配置（已在类属性中定义）

        # 从配置加载评分权重
        affinity_config = global_config.affinity_flow
        self.score_weights = {
            "interest_match": affinity_config.keyword_match_weight,  # 兴趣匹配度权重
            "relationship": affinity_config.relationship_weight,  # 关系分权重
            "mentioned": affinity_config.mention_bot_weight,  # 是否提及bot权重
        }

        # 评分阈值
        self.reply_threshold = affinity_config.reply_action_interest_threshold  # 回复动作兴趣阈值
        self.mention_threshold = affinity_config.mention_bot_adjustment_threshold  # 提及bot后的调整阈值

        # 兴趣匹配系统配置
        self.use_smart_matching = True

        # 连续不回复概率提升
        self.no_reply_count = 0
        self.max_no_reply_count = affinity_config.max_no_reply_count
        self.probability_boost_per_no_reply = (
            affinity_config.no_reply_threshold_adjustment / affinity_config.max_no_reply_count
        )  # 每次不回复增加的概率

        # 用户关系数据缓存
        self.user_relationships: dict[str, float] = {}  # user_id -> relationship_score

        logger.info("[Affinity兴趣计算器] 初始化完成:")
        logger.info(f"  - 权重配置: {self.score_weights}")
        logger.info(f"  - 回复阈值: {self.reply_threshold}")
        logger.info(f"  - 智能匹配: {self.use_smart_matching}")

        # 检查 bot_interest_manager 状态
        try:
            logger.info(f"  - bot_interest_manager 初始化状态: {bot_interest_manager.is_initialized}")
            if not bot_interest_manager.is_initialized:
                logger.warning("  - bot_interest_manager 未初始化，这将导致兴趣匹配返回默认值0.3")
        except Exception as e:
            logger.error(f"  - 检查 bot_interest_manager 时出错: {e}")

    async def execute(self, message: "DatabaseMessages") -> InterestCalculationResult:
        """执行AffinityFlow风格的兴趣值计算"""
        try:
            start_time = time.time()
            message_id = getattr(message, "message_id", "")
            content = getattr(message, "processed_plain_text", "")
            user_info = getattr(message, "user_info", None)
            if user_info and hasattr(user_info, "user_id"):
                user_id = user_info.user_id
            else:
                user_id = ""

            logger.debug(f"[Affinity兴趣计算] 开始处理消息 {message_id}")
            logger.debug(f"[Affinity兴趣计算] 消息内容: {content[:50]}...")
            logger.debug(f"[Affinity兴趣计算] 用户ID: {user_id}")

            # 1. 计算兴趣匹配分
            keywords = self._extract_keywords_from_database(message)
            interest_match_score = await self._calculate_interest_match_score(content, keywords)
            logger.debug(f"[Affinity兴趣计算] 兴趣匹配分: {interest_match_score}")

            # 2. 计算关系分
            relationship_score = await self._calculate_relationship_score(user_id)
            logger.debug(f"[Affinity兴趣计算] 关系分: {relationship_score}")

            # 3. 计算提及分
            mentioned_score = self._calculate_mentioned_score(message, global_config.bot.nickname)
            logger.debug(f"[Affinity兴趣计算] 提及分: {mentioned_score}")

            # 4. 综合评分
            # 确保所有分数都是有效的 float 值
            interest_match_score = float(interest_match_score) if interest_match_score is not None else 0.0
            relationship_score = float(relationship_score) if relationship_score is not None else 0.0
            mentioned_score = float(mentioned_score) if mentioned_score is not None else 0.0

            total_score = (
                interest_match_score * self.score_weights["interest_match"]
                + relationship_score * self.score_weights["relationship"]
                + mentioned_score * self.score_weights["mentioned"]
            )

            logger.debug(
                f"[Affinity兴趣计算] 综合得分计算: {interest_match_score:.3f}*{self.score_weights['interest_match']} + "
                f"{relationship_score:.3f}*{self.score_weights['relationship']} + "
                f"{mentioned_score:.3f}*{self.score_weights['mentioned']} = {total_score:.3f}"
            )

            # 5. 考虑连续不回复的概率提升
            adjusted_score = self._apply_no_reply_boost(total_score)
            logger.debug(f"[Affinity兴趣计算] 应用不回复提升后: {total_score:.3f} → {adjusted_score:.3f}")

            # 6. 决定是否回复和执行动作
            reply_threshold = self.reply_threshold
            action_threshold = global_config.affinity_flow.non_reply_action_interest_threshold

            should_reply = adjusted_score >= reply_threshold
            should_take_action = adjusted_score >= action_threshold

            logger.debug(
                f"[Affinity兴趣计算] 阈值判断: {adjusted_score:.3f} >= 回复阈值:{reply_threshold:.3f}? = {should_reply}"
            )
            logger.debug(
                f"[Affinity兴趣计算] 阈值判断: {adjusted_score:.3f} >= 动作阈值:{action_threshold:.3f}? = {should_take_action}"
            )

            calculation_time = time.time() - start_time

            logger.debug(
                f"Affinity兴趣值计算完成 - 消息 {message_id}: {adjusted_score:.3f} "
                f"(匹配:{interest_match_score:.2f}, 关系:{relationship_score:.2f}, 提及:{mentioned_score:.2f})"
            )

            return InterestCalculationResult(
                success=True,
                message_id=message_id,
                interest_value=adjusted_score,
                should_take_action=should_take_action,
                should_reply=should_reply,
                should_act=should_take_action,
                calculation_time=calculation_time,
            )

        except Exception as e:
            logger.error(f"Affinity兴趣值计算失败: {e}", exc_info=True)
            return InterestCalculationResult(
                success=False, message_id=getattr(message, "message_id", ""), interest_value=0.0, error_message=str(e)
            )

    async def _calculate_interest_match_score(self, content: str, keywords: list[str] | None = None) -> float:
        """计算兴趣匹配度（使用智能兴趣匹配系统）"""

        # 调试日志：检查各个条件
        if not content:
            logger.debug("兴趣匹配返回0.0: 内容为空")
            return 0.0
        if not self.use_smart_matching:
            logger.debug("兴趣匹配返回0.0: 智能匹配未启用")
            return 0.0
        if not bot_interest_manager.is_initialized:
            logger.debug("兴趣匹配返回0.0: bot_interest_manager未初始化")
            return 0.0

        logger.debug(f"开始兴趣匹配计算，内容: {content[:50]}...")

        try:
            # 使用机器人的兴趣标签系统进行智能匹配
            match_result = await bot_interest_manager.calculate_interest_match(content, keywords or [])
            logger.debug(f"兴趣匹配结果: {match_result}")

            if match_result:
                # 返回匹配分数，考虑置信度和匹配标签数量
                affinity_config = global_config.affinity_flow
                match_count_bonus = min(
                    len(match_result.matched_tags) * affinity_config.match_count_bonus, affinity_config.max_match_bonus
                )
                final_score = match_result.overall_score * 1.15 * match_result.confidence + match_count_bonus
                logger.debug(f"兴趣匹配最终得分: {final_score}")
                return final_score
            else:
                logger.debug("兴趣匹配返回0.0: match_result为None")
                return 0.0

        except Exception as e:
            logger.warning(f"智能兴趣匹配失败: {e}")
            return 0.0

    async def _calculate_relationship_score(self, user_id: str) -> float:
        """计算用户关系分"""
        if not user_id:
            return global_config.affinity_flow.base_relationship_score

        # 优先使用内存中的关系分
        if user_id in self.user_relationships:
            relationship_value = self.user_relationships[user_id]
            return min(relationship_value, 1.0)

        # 如果内存中没有，尝试从关系追踪器获取
        try:
            from .relationship_tracker import ChatterRelationshipTracker

            global_tracker = ChatterRelationshipTracker()
            if global_tracker:
                relationship_score = await global_tracker.get_user_relationship_score(user_id)
                # 同时更新内存缓存
                self.user_relationships[user_id] = relationship_score
                return relationship_score
        except Exception as e:
            logger.debug(f"获取用户关系分失败: {e}")

        # 默认新用户的基础分
        return global_config.affinity_flow.base_relationship_score

    def _calculate_mentioned_score(self, message: "DatabaseMessages", bot_nickname: str) -> float:
        """计算提及分"""
        is_mentioned = getattr(message, "is_mentioned", False)
        is_at = getattr(message, "is_at", False)
        processed_plain_text = getattr(message, "processed_plain_text", "")

        # 判断是否为私聊
        chat_info_group_id = getattr(message, "chat_info_group_id", None)
        is_private_chat = not chat_info_group_id  # 如果没有group_id则是私聊

        logger.debug(f"[提及分计算] is_mentioned={is_mentioned}, is_at={is_at}, is_private_chat={is_private_chat}")

        if is_mentioned:
            if is_at:
                logger.debug("[提及分计算] 直接@机器人，返回1.0")
                return 1.0  # 直接@机器人，最高分
            else:
                logger.debug("[提及分计算] 提及机器人，返回0.8")
                return 0.8  # 提及机器人名字，高分
        else:
            # 检查是否被提及（文本匹配）
            bot_aliases = [bot_nickname, *global_config.bot.alias_names]
            is_text_mentioned = any(alias in processed_plain_text for alias in bot_aliases if alias)

            # 如果被提及或是私聊，都视为提及了bot
            if is_text_mentioned:
                logger.debug("[提及分计算] 文本提及机器人，返回提及分")
                return global_config.affinity_flow.mention_bot_interest_score
            elif is_private_chat:
                logger.debug("[提及分计算] 私聊消息，返回提及分")
                return global_config.affinity_flow.mention_bot_interest_score
            else:
                logger.debug("[提及分计算] 未提及机器人，返回0.0")
                return 0.0  # 未提及机器人

    def _apply_no_reply_boost(self, base_score: float) -> float:
        """应用连续不回复的概率提升"""
        if self.no_reply_count > 0 and self.no_reply_count < self.max_no_reply_count:
            boost = self.no_reply_count * self.probability_boost_per_no_reply
            return min(1.0, base_score + boost)
        return base_score

    def _extract_keywords_from_database(self, message: "DatabaseMessages") -> list[str]:
        """从数据库消息中提取关键词"""
        keywords = []

        # 尝试从 key_words 字段提取（存储的是JSON字符串）
        key_words = getattr(message, "key_words", "")
        if key_words:
            try:
                extracted = orjson.loads(key_words)
                if isinstance(extracted, list):
                    keywords = extracted
            except (orjson.JSONDecodeError, TypeError):
                keywords = []

        # 如果没有 keywords，尝试从 key_words_lite 提取
        if not keywords:
            key_words_lite = getattr(message, "key_words_lite", "")
            if key_words_lite:
                try:
                    extracted = orjson.loads(key_words_lite)
                    if isinstance(extracted, list):
                        keywords = extracted
                except (orjson.JSONDecodeError, TypeError):
                    keywords = []

        # 如果还是没有，从消息内容中提取（降级方案）
        if not keywords:
            content = getattr(message, "processed_plain_text", "") or ""
            keywords = self._extract_keywords_from_content(content)

        return keywords[:15]  # 返回前15个关键词

    def _extract_keywords_from_content(self, content: str) -> list[str]:
        """从内容中提取关键词（降级方案）"""
        import re

        # 清理文本
        content = re.sub(r"[^\w\s\u4e00-\u9fff]", " ", content)  # 保留中文、英文、数字
        words = content.split()

        # 过滤和关键词提取
        keywords = []
        for word in words:
            word = word.strip()
            if (
                len(word) >= 2  # 至少2个字符
                and word.isalnum()  # 字母数字
                and not word.isdigit()
            ):  # 不是纯数字
                keywords.append(word.lower())

        # 去重并限制数量
        unique_keywords = list(set(keywords))
        return unique_keywords[:10]  # 返回前10个唯一关键词

    def update_no_reply_count(self, replied: bool):
        """更新连续不回复计数"""
        if replied:
            self.no_reply_count = 0
        else:
            self.no_reply_count = min(self.no_reply_count + 1, self.max_no_reply_count)
