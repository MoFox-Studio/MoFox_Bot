"""
LLM反注入系统主模块

本模块实现了完整的LLM反注入防护流程，按照设计的流程图进行消息处理：
1. 检查系统是否启用
2. 黑白名单验证
3. 规则集检测
4. LLM二次分析（可选）
5. 处理模式选择（严格/宽松）
6. 消息加盾或丢弃
"""

import time
from typing import Any

from src.common.logger import get_logger
from src.config.config import global_config

from .core import MessageShield, PromptInjectionDetector
from .decision import CounterAttackGenerator, ProcessingDecisionMaker
from .management import AntiInjectionStatistics, UserBanManager
from .processors.message_processor import MessageProcessor
from .types import ProcessResult

logger = get_logger("anti_injector")


class AntiPromptInjector:
    """LLM反注入系统主类"""

    def __init__(self):
        """初始化反注入系统"""
        self.config = global_config.anti_prompt_injection
        self.detector = PromptInjectionDetector()
        self.shield = MessageShield()

        # 初始化子模块
        self.statistics = AntiInjectionStatistics()
        self.user_ban_manager = UserBanManager(self.config)
        self.counter_attack_generator = CounterAttackGenerator()
        self.decision_maker = ProcessingDecisionMaker(self.config)
        self.message_processor = MessageProcessor()

    async def process_message(
        self, message_data: dict, chat_stream=None
    ) -> tuple[ProcessResult, str | None, str | None]:
        """处理字典格式的消息并返回结果

        Args:
            message_data: 消息数据字典
            chat_stream: 聊天流对象（可选）

        Returns:
            Tuple[ProcessResult, Optional[str], Optional[str]]:
            - 处理结果状态枚举
            - 处理后的消息内容（如果有修改）
            - 处理结果说明
        """
        start_time = time.time()

        try:
            # 1. 检查系统是否启用
            if not self.config.enabled:
                return ProcessResult.ALLOWED, None, "反注入系统未启用"

            # 统计更新 - 只有在系统启用时才进行统计
            await self.statistics.update_stats(total_messages=1)

            # 2. 从字典中提取必要信息
            processed_plain_text = message_data.get("processed_plain_text", "")
            user_id = message_data.get("user_id", "")
            platform = message_data.get("chat_info_platform", "") or message_data.get("user_platform", "")

            logger.debug(f"开始处理字典消息: {processed_plain_text}")

            # 3. 检查用户是否被封禁
            if self.config.auto_ban_enabled and user_id and platform:
                ban_result = await self.user_ban_manager.check_user_ban(user_id, platform)
                if ban_result is not None:
                    logger.info(f"用户被封禁: {ban_result[2]}")
                    return ProcessResult.BLOCKED_BAN, None, ban_result[2]

            # 4. 白名单检测
            if self.message_processor.check_whitelist_dict(user_id, platform, self.config.whitelist):
                return ProcessResult.ALLOWED, None, "用户在白名单中，跳过检测"

            # 5. 提取用户新增内容（去除引用部分）
            text_to_detect = self.message_processor.extract_text_content_from_dict(message_data)
            logger.debug(f"提取的检测文本: '{text_to_detect}' (长度: {len(text_to_detect)})")

            # 委托给内部实现
            return await self._process_message_internal(
                text_to_detect=text_to_detect,
                user_id=user_id,
                platform=platform,
                processed_plain_text=processed_plain_text,
                start_time=start_time,
            )

        except Exception as e:
            logger.error(f"反注入处理异常: {e}", exc_info=True)
            await self.statistics.update_stats(error_count=1)

            # 异常情况下直接阻止消息
            return ProcessResult.BLOCKED_INJECTION, None, f"反注入系统异常，消息已阻止: {e!s}"

        finally:
            # 更新处理时间统计
            process_time = time.time() - start_time
            await self.statistics.update_stats(processing_time_delta=process_time, last_processing_time=process_time)

    async def _process_message_internal(
        self, text_to_detect: str, user_id: str, platform: str, processed_plain_text: str, start_time: float
    ) -> tuple[ProcessResult, str | None, str | None]:
        """内部消息处理逻辑（共用的检测核心）"""

        # 如果是纯引用消息，直接允许通过
        if text_to_detect == "[纯引用消息]":
            logger.debug("检测到纯引用消息，跳过注入检测")
            return ProcessResult.ALLOWED, None, "纯引用消息，跳过检测"

        detection_result = await self.detector.detect(text_to_detect)

        # 处理检测结果
        if detection_result.is_injection:
            await self.statistics.update_stats(detected_injections=1)

            # 记录违规行为
            if self.config.auto_ban_enabled and user_id and platform:
                await self.user_ban_manager.record_violation(user_id, platform, detection_result)

            # 根据处理模式决定如何处理
            if self.config.process_mode == "strict":
                # 严格模式：直接拒绝
                await self.statistics.update_stats(blocked_messages=1)
                return (
                    ProcessResult.BLOCKED_INJECTION,
                    None,
                    f"检测到提示词注入攻击，消息已拒绝 (置信度: {detection_result.confidence:.2f})",
                )

            elif self.config.process_mode == "lenient":
                # 宽松模式：加盾处理
                if self.shield.is_shield_needed(detection_result.confidence, detection_result.matched_patterns):
                    await self.statistics.update_stats(shielded_messages=1)

                    # 创建加盾后的消息内容
                    shielded_content = self.shield.create_shielded_message(
                        processed_plain_text, detection_result.confidence
                    )

                    summary = self.shield.create_safety_summary(
                        detection_result.confidence, detection_result.matched_patterns
                    )

                    return ProcessResult.SHIELDED, shielded_content, f"检测到可疑内容已加盾处理: {summary}"
                else:
                    # 置信度不高，允许通过
                    return ProcessResult.ALLOWED, None, "检测到轻微可疑内容，已允许通过"

            elif self.config.process_mode == "auto":
                # 自动模式：根据威胁等级自动选择处理方式
                auto_action = self.decision_maker.determine_auto_action(detection_result)

                if auto_action == "block":
                    # 高威胁：直接丢弃
                    await self.statistics.update_stats(blocked_messages=1)
                    return (
                        ProcessResult.BLOCKED_INJECTION,
                        None,
                        f"自动模式：检测到高威胁内容，消息已拒绝 (置信度: {detection_result.confidence:.2f})",
                    )

                elif auto_action == "shield":
                    # 中等威胁：加盾处理
                    await self.statistics.update_stats(shielded_messages=1)

                    shielded_content = self.shield.create_shielded_message(
                        processed_plain_text, detection_result.confidence
                    )

                    summary = self.shield.create_safety_summary(
                        detection_result.confidence, detection_result.matched_patterns
                    )

                    return ProcessResult.SHIELDED, shielded_content, f"自动模式：检测到中等威胁已加盾处理: {summary}"

                else:  # auto_action == "allow"
                    # 低威胁：允许通过
                    return ProcessResult.ALLOWED, None, "自动模式：检测到轻微可疑内容，已允许通过"

            elif self.config.process_mode == "counter_attack":
                # 反击模式：生成反击消息并丢弃原消息
                await self.statistics.update_stats(blocked_messages=1)

                # 生成反击消息
                counter_message = await self.counter_attack_generator.generate_counter_attack_message(
                    processed_plain_text, detection_result
                )

                if counter_message:
                    logger.info(f"反击模式：已生成反击消息并阻止原消息 (置信度: {detection_result.confidence:.2f})")
                    return (
                        ProcessResult.COUNTER_ATTACK,
                        counter_message,
                        f"检测到提示词注入攻击，已生成反击回应 (置信度: {detection_result.confidence:.2f})",
                    )
                else:
                    # 如果反击消息生成失败，降级为严格模式
                    logger.warning("反击消息生成失败，降级为严格阻止模式")
                    return (
                        ProcessResult.BLOCKED_INJECTION,
                        None,
                        f"检测到提示词注入攻击，消息已拒绝 (置信度: {detection_result.confidence:.2f})",
                    )

        # 正常消息
        return ProcessResult.ALLOWED, None, "消息检查通过"

    async def handle_message_storage(
        self, result: ProcessResult, modified_content: str | None, reason: str, message_data: dict
    ) -> None:
        """处理违禁消息的数据库存储，根据处理模式决定如何处理"""
        mode = self.config.process_mode
        message_id = message_data.get("message_id")

        if not message_id:
            logger.warning("无法处理消息存储：缺少 message_id")
            return

        if mode == "strict":
            if result == ProcessResult.BLOCKED_INJECTION:
                await self._delete_message_from_storage(message_data)
                logger.info(f"[严格模式] 违禁消息已从数据库中删除: {reason}")
            elif result == ProcessResult.SHIELDED:
                if modified_content:
                    await self._update_message_in_storage(message_data, modified_content)
                    logger.info(f"[严格模式] 违禁消息内容已替换为加盾版本: {reason}")

        elif mode == "lenient":
            if result == ProcessResult.SHIELDED:
                if modified_content:
                    await self._update_message_in_storage(message_data, modified_content)
                    logger.info(f"[宽松模式] 违禁消息内容已替换为加盾版本: {reason}")

        elif mode == "auto":
            if result == ProcessResult.BLOCKED_INJECTION:
                await self._delete_message_from_storage(message_data)
                logger.info(f"[自动模式] 高威胁消息已删除: {reason}")
            elif result == ProcessResult.SHIELDED:
                if modified_content:
                    await self._update_message_in_storage(message_data, modified_content)
                    logger.info(f"[自动模式] 中等威胁消息已加盾: {reason}")

        elif mode == "counter_attack":
            if result == ProcessResult.COUNTER_ATTACK:
                await self._delete_message_from_storage(message_data)
                logger.info(f"[反击模式] 违禁消息已从数据库中删除: {reason}")

    @staticmethod
    async def _delete_message_from_storage(message_data: dict) -> None:
        """从数据库中删除违禁消息记录"""
        try:
            from sqlalchemy import delete

            from src.common.database.sqlalchemy_models import Messages, get_db_session

            message_id = message_data.get("message_id")
            if not message_id:
                logger.warning("无法删除消息：缺少message_id")
                return

            async with get_db_session() as session:
                # 删除对应的消息记录
                stmt = delete(Messages).where(Messages.message_id == message_id)
                result = await session.execute(stmt)
                await session.commit()

                if result.rowcount > 0:
                    logger.debug(f"成功删除违禁消息记录: {message_id}")
                else:
                    logger.debug(f"未找到要删除的消息记录: {message_id}")

        except Exception as e:
            logger.error(f"删除违禁消息记录失败: {e}")

    @staticmethod
    async def _update_message_in_storage(message_data: dict, new_content: str) -> None:
        """更新数据库中的消息内容为加盾版本"""
        try:
            from sqlalchemy import update

            from src.common.database.sqlalchemy_models import Messages, get_db_session

            message_id = message_data.get("message_id")
            if not message_id:
                logger.warning("无法更新消息：缺少message_id")
                return

            async with get_db_session() as session:
                # 更新消息内容
                stmt = (
                    update(Messages)
                    .where(Messages.message_id == message_id)
                    .values(processed_plain_text=new_content, display_message=new_content)
                )
                result = await session.execute(stmt)
                await session.commit()

                if result.rowcount > 0:
                    logger.debug(f"成功更新消息内容为加盾版本: {message_id}")
                else:
                    logger.debug(f"未找到要更新的消息记录: {message_id}")

        except Exception as e:
            logger.error(f"更新消息内容失败: {e}")

    async def get_stats(self) -> dict[str, Any]:
        """获取统计信息"""
        return await self.statistics.get_stats()

    async def reset_stats(self):
        """重置统计信息"""
        await self.statistics.reset_stats()


# 全局反注入器实例
_global_injector: AntiPromptInjector | None = None


def get_anti_injector() -> AntiPromptInjector:
    """获取全局反注入器实例"""
    global _global_injector
    if _global_injector is None:
        _global_injector = AntiPromptInjector()
    return _global_injector


def initialize_anti_injector() -> AntiPromptInjector:
    """初始化反注入器"""
    global _global_injector
    _global_injector = AntiPromptInjector()
    return _global_injector
