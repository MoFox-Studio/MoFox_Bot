"""AffinityFlow 风格兴趣值计算组件

基于原有的 AffinityFlow 兴趣度评分系统，提供标准化的兴趣值计算功能
"""

import asyncio
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
        if self.max_no_reply_count > 0:
            self.probability_boost_per_no_reply = (
                affinity_config.no_reply_threshold_adjustment / self.max_no_reply_count
            )
        else:
            self.probability_boost_per_no_reply = 0.0  # 避免除以零的错误

        # 用户关系数据缓存
        self.user_relationships: dict[str, float] = {}  # user_id -> relationship_score

        # 回复后阈值降低机制
        self.enable_post_reply_boost = affinity_config.enable_post_reply_boost
        self.post_reply_boost_remaining = 0  # 剩余的回复后降低次数
        self.post_reply_threshold_reduction = affinity_config.post_reply_threshold_reduction
        self.post_reply_boost_max_count = affinity_config.post_reply_boost_max_count
        self.post_reply_boost_decay_rate = affinity_config.post_reply_boost_decay_rate

        logger.info("[Affinity兴趣计算器] 初始化完成:")
        logger.info(f"  - 权重配置: {self.score_weights}")
        logger.info(f"  - 回复阈值: {self.reply_threshold}")
        logger.info(f"  - 智能匹配: {self.use_smart_matching}")
        logger.info(f"  - 回复后连续对话: {self.enable_post_reply_boost}")

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

            raw_total_score = (
                interest_match_score * self.score_weights["interest_match"]
                + relationship_score * self.score_weights["relationship"]
                + mentioned_score * self.score_weights["mentioned"]
            )

            # 限制总分上限为1.0，确保分数在合理范围内
            total_score = min(raw_total_score, 1.0)

            logger.debug(
                f"[Affinity兴趣计算] 综合得分计算: {interest_match_score:.3f}*{self.score_weights['interest_match']} + "
                f"{relationship_score:.3f}*{self.score_weights['relationship']} + "
                f"{mentioned_score:.3f}*{self.score_weights['mentioned']} = {raw_total_score:.3f}"
            )

            if raw_total_score > 1.0:
                logger.debug(f"[Affinity兴趣计算] 原始分数 {raw_total_score:.3f} 超过1.0，已限制为 {total_score:.3f}")

            # 5. 考虑连续不回复的阈值调整
            adjusted_score = total_score
            adjusted_reply_threshold, adjusted_action_threshold = self._apply_no_reply_threshold_adjustment()
            logger.debug(
                f"[Affinity兴趣计算] 连续不回复调整: 回复阈值 {self.reply_threshold:.3f} → {adjusted_reply_threshold:.3f}, "
                f"动作阈值 {global_config.affinity_flow.non_reply_action_interest_threshold:.3f} → {adjusted_action_threshold:.3f}"
            )

            # 6. 决定是否回复和执行动作
            should_reply = adjusted_score >= adjusted_reply_threshold
            should_take_action = adjusted_score >= adjusted_action_threshold

            logger.debug(
                f"[Affinity兴趣计算] 阈值判断: {adjusted_score:.3f} >= 回复阈值:{adjusted_reply_threshold:.3f}? = {should_reply}"
            )
            logger.debug(
                f"[Affinity兴趣计算] 阈值判断: {adjusted_score:.3f} >= 动作阈值:{adjusted_action_threshold:.3f}? = {should_take_action}"
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
        """计算兴趣匹配度（使用智能兴趣匹配系统，带超时保护）"""

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
            # 使用机器人的兴趣标签系统进行智能匹配（1.5秒超时保护）
            match_result = await asyncio.wait_for(
                bot_interest_manager.calculate_interest_match(content, keywords or []),
                timeout=1.5
            )
            logger.debug(f"兴趣匹配结果: {match_result}")

            if match_result:
                # 返回匹配分数，考虑置信度和匹配标签数量
                affinity_config = global_config.affinity_flow
                match_count_bonus = min(
                    len(match_result.matched_tags) * affinity_config.match_count_bonus, affinity_config.max_match_bonus
                )
                final_score = match_result.overall_score * 1.15 * match_result.confidence + match_count_bonus
                # 限制兴趣匹配分数上限为1.0，防止总分超标
                final_score = min(final_score, 1.0)
                logger.debug(f"兴趣匹配最终得分: {final_score:.3f} (原始: {match_result.overall_score * 1.15 * match_result.confidence + match_count_bonus:.3f})")
                return final_score
            else:
                logger.debug("兴趣匹配返回0.0: match_result为None")
                return 0.0

        except asyncio.TimeoutError:
            logger.warning("⏱️ 兴趣匹配计算超时(>1.5秒)，返回默认分值0.5以保留其他分数")
            return 0.5  # 超时时返回默认分值，避免丢失提及分和关系分
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

        # 如果内存中没有，尝试从统一的评分API获取
        try:
            from src.plugin_system.apis import person_api

            relationship_data = await person_api.get_user_relationship_data(user_id)
            if relationship_data:
                relationship_score = relationship_data.get("relationship_score", global_config.affinity_flow.base_relationship_score)
                # 同时更新内存缓存
                self.user_relationships[user_id] = relationship_score
                return relationship_score
        except Exception as e:
            logger.debug(f"获取用户关系分失败: {e}")

        # 默认新用户的基础分
        return global_config.affinity_flow.base_relationship_score

    def _calculate_mentioned_score(self, message: "DatabaseMessages", bot_nickname: str) -> float:
        """计算提及分 - 区分强提及和弱提及

        强提及（被@、被回复、私聊）: 使用 strong_mention_interest_score
        弱提及（文本匹配名字/别名）: 使用 weak_mention_interest_score
        """
        from src.chat.utils.utils import is_mentioned_bot_in_message

        # 使用统一的提及检测函数
        is_mentioned, mention_type = is_mentioned_bot_in_message(message)

        if not is_mentioned:
            logger.debug("[提及分计算] 未提及机器人，返回0.0")
            return 0.0

        # mention_type: 0=未提及, 1=弱提及, 2=强提及
        if mention_type >= 2:
            # 强提及：被@、被回复、私聊
            score = global_config.affinity_flow.strong_mention_interest_score
            logger.debug(f"[提及分计算] 检测到强提及（@/回复/私聊），返回分值: {score}")
            return score
        elif mention_type >= 1:
            # 弱提及：文本匹配bot名字或别名
            score = global_config.affinity_flow.weak_mention_interest_score
            logger.debug(f"[提及分计算] 检测到弱提及（文本匹配），返回分值: {score}")
            return score
        else:
            logger.debug("[提及分计算] 未提及机器人，返回0.0")
            return 0.0

    def _apply_no_reply_threshold_adjustment(self) -> tuple[float, float]:
        """应用阈值调整（包括连续不回复和回复后降低机制）

        Returns:
            tuple[float, float]: (调整后的回复阈值, 调整后的动作阈值)
        """
        # 基础阈值
        base_reply_threshold = self.reply_threshold
        base_action_threshold = global_config.affinity_flow.non_reply_action_interest_threshold

        total_reduction = 0.0

        # 1. 连续不回复的阈值降低
        if self.no_reply_count > 0 and self.no_reply_count < self.max_no_reply_count:
            no_reply_reduction = self.no_reply_count * self.probability_boost_per_no_reply
            total_reduction += no_reply_reduction
            logger.debug(f"[阈值调整] 连续不回复降低: {no_reply_reduction:.3f} (计数: {self.no_reply_count})")

        # 2. 回复后的阈值降低（使bot更容易连续对话）
        if self.enable_post_reply_boost and self.post_reply_boost_remaining > 0:
            # 计算衰减后的降低值
            decay_factor = self.post_reply_boost_decay_rate ** (
                self.post_reply_boost_max_count - self.post_reply_boost_remaining
            )
            post_reply_reduction = self.post_reply_threshold_reduction * decay_factor
            total_reduction += post_reply_reduction
            logger.debug(
                f"[阈值调整] 回复后降低: {post_reply_reduction:.3f} "
                f"(剩余次数: {self.post_reply_boost_remaining}, 衰减: {decay_factor:.2f})"
            )

        # 应用总降低量
        adjusted_reply_threshold = max(0.0, base_reply_threshold - total_reduction)
        adjusted_action_threshold = max(0.0, base_action_threshold - total_reduction)

        return adjusted_reply_threshold, adjusted_action_threshold

    def _apply_no_reply_boost(self, base_score: float) -> float:
        """【已弃用】应用连续不回复的概率提升

        注意：此方法已被 _apply_no_reply_threshold_adjustment 替代
        保留用于向后兼容
        """
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

    def on_reply_sent(self):
        """当机器人发送回复后调用，激活回复后阈值降低机制"""
        if self.enable_post_reply_boost:
            # 重置回复后降低计数器
            self.post_reply_boost_remaining = self.post_reply_boost_max_count
            logger.debug(
                f"[回复后机制] 激活连续对话模式，阈值将在接下来 {self.post_reply_boost_max_count} 条消息中降低"
            )
            # 同时重置不回复计数
            self.no_reply_count = 0

    def on_message_processed(self, replied: bool):
        """消息处理完成后调用，更新各种计数器

        Args:
            replied: 是否回复了此消息
        """
        # 更新不回复计数
        self.update_no_reply_count(replied)

        # 如果已回复，激活回复后降低机制
        if replied:
            self.on_reply_sent()
        else:
            # 如果没有回复，减少回复后降低剩余次数
            if self.post_reply_boost_remaining > 0:
                self.post_reply_boost_remaining -= 1
                logger.debug(
                    f"[回复后机制] 未回复消息，剩余降低次数: {self.post_reply_boost_remaining}"
                )
