"""
消息处理器

处理检测结果，执行相应的动作（允许/监控/加盾/阻止/反击）。
"""

from src.chat.security.interfaces import SecurityCheckResult
from src.common.logger import get_logger

from .counter_attack import CounterAttackGenerator

logger = get_logger("anti_injection.processor")


class MessageProcessor:
    """消息处理器"""

    def __init__(self, config: dict | None = None):
        """初始化消息处理器

        Args:
            config: 配置字典
        """
        self.config = config or {}
        self.counter_attack_gen = CounterAttackGenerator(config)

        # 处理模式
        self.process_mode = self.config.get("process_mode", "lenient")
        # strict: 严格模式，高/中风险直接丢弃
        # lenient: 宽松模式，中风险加盾，高风险丢弃
        # monitor: 监控模式，只记录不拦截
        # counter_attack: 反击模式，生成反击响应并丢弃原消息

    async def process(
        self, message: str, check_result: SecurityCheckResult
    ) -> tuple[bool, str | None, str]:
        """处理消息

        Args:
            message: 原始消息
            check_result: 安全检测结果

        Returns:
            tuple[bool, str | None, str]:
                - bool: 是否允许通过
                - str | None: 修改后的消息内容（如果有）
                - str: 处理说明
        """
        # 如果消息安全，直接通过
        if check_result.is_safe:
            return True, None, "消息安全，允许通过"

        # 根据处理模式和检测结果决定动作
        if self.process_mode == "monitor":
            return await self._process_monitor(message, check_result)
        elif self.process_mode == "strict":
            return await self._process_strict(message, check_result)
        elif self.process_mode == "counter_attack":
            return await self._process_counter_attack(message, check_result)
        else:  # lenient
            return await self._process_lenient(message, check_result)

    async def _process_monitor(
        self, message: str, check_result: SecurityCheckResult
    ) -> tuple[bool, str | None, str]:
        """监控模式：只记录不拦截"""
        logger.warning(
            f"[监控模式] 检测到风险消息 - 级别: {check_result.level.name}, "
            f"置信度: {check_result.confidence:.2f}, 原因: {check_result.reason}"
        )
        return True, None, f"监控模式：已记录风险 - {check_result.reason}"

    async def _process_strict(
        self, message: str, check_result: SecurityCheckResult
    ) -> tuple[bool, str | None, str]:
        """严格模式：中/高风险直接丢弃"""
        from src.chat.security.interfaces import SecurityLevel

        if check_result.level in [
            SecurityLevel.MEDIUM_RISK,
            SecurityLevel.HIGH_RISK,
            SecurityLevel.CRITICAL,
        ]:
            logger.warning(
                f"[严格模式] 消息已丢弃 - 级别: {check_result.level.name}, "
                f"置信度: {check_result.confidence:.2f}"
            )
            return (
                False,
                None,
                f"严格模式：消息已拒绝 - {check_result.reason} (置信度: {check_result.confidence:.2f})",
            )

        # 低风险允许通过
        return True, None, "严格模式：低风险消息允许通过"

    async def _process_lenient(
        self, message: str, check_result: SecurityCheckResult
    ) -> tuple[bool, str | None, str]:
        """宽松模式：中风险加盾，高风险丢弃"""
        from src.chat.security.interfaces import SecurityLevel

        if check_result.level in [SecurityLevel.HIGH_RISK, SecurityLevel.CRITICAL]:
            # 高风险：直接丢弃
            logger.warning(
                f"[宽松模式] 高风险消息已丢弃 - 级别: {check_result.level.name}, "
                f"置信度: {check_result.confidence:.2f}"
            )
            return (
                False,
                None,
                f"宽松模式：高风险消息已拒绝 - {check_result.reason}",
            )

        elif check_result.level == SecurityLevel.MEDIUM_RISK:
            # 中等风险：加盾处理
            shielded_message = self._shield_message(message, check_result)
            logger.info(
                f"[宽松模式] 中风险消息已加盾 - 置信度: {check_result.confidence:.2f}"
            )
            return (
                True,
                shielded_message,
                f"宽松模式：中风险消息已加盾处理 - {check_result.reason}",
            )

        # 低风险允许通过
        return True, None, "宽松模式：低风险消息允许通过"

    async def _process_counter_attack(
        self, message: str, check_result: SecurityCheckResult
    ) -> tuple[bool, str | None, str]:
        """反击模式：生成反击响应并丢弃原消息"""
        from src.chat.security.interfaces import SecurityLevel

        # 只对中/高风险消息进行反击
        if check_result.level in [
            SecurityLevel.MEDIUM_RISK,
            SecurityLevel.HIGH_RISK,
            SecurityLevel.CRITICAL,
        ]:
            # 生成反击响应
            counter_message = await self.counter_attack_gen.generate(message, check_result)

            logger.warning(
                f"[反击模式] 已生成反击响应 - 级别: {check_result.level.name}, "
                f"置信度: {check_result.confidence:.2f}"
            )

            # 返回False表示丢弃原消息，counter_message将作为系统响应发送
            return (
                False,
                counter_message,
                f"反击模式：已生成反击响应 - {check_result.reason}",
            )

        # 低风险允许通过
        return True, None, "反击模式：低风险消息允许通过"

    def _shield_message(self, message: str, check_result: SecurityCheckResult) -> str:
        """为消息加盾

        在消息前后添加安全标记，提醒AI这是可疑内容
        """
        shield_prefix = self.config.get("shield_prefix", "🛡️ ")
        shield_suffix = self.config.get("shield_suffix", " 🛡️")

        # 根据置信度决定加盾强度
        if check_result.confidence > 0.7:
            # 高置信度：强加盾
            safety_note = (
                f"\n\n[安全提醒: 此消息包含可疑内容，请谨慎处理。检测原因: {check_result.reason}]"
            )
            return f"{shield_prefix}{message}{shield_suffix}{safety_note}"
        else:
            # 低置信度：轻加盾
            return f"{shield_prefix}{message}{shield_suffix}"

    async def handle_blocked_message(
        self, message_data: dict, reason: str
    ) -> None:
        """处理被阻止的消息（可选的数据库操作）

        Args:
            message_data: 消息数据字典
            reason: 阻止原因
        """
        try:
            # 如果配置了记录被阻止的消息
            if self.config.get("log_blocked_messages", True):
                logger.info(f"消息已阻止 - 原因: {reason}, 消息ID: {message_data.get('message_id', 'unknown')}")

            # 如果配置了删除数据库记录
            if self.config.get("delete_blocked_from_db", False):
                await self._delete_message_from_storage(message_data)

        except Exception as e:
            logger.error(f"处理被阻止消息失败: {e}")

    @staticmethod
    async def _delete_message_from_storage(message_data: dict) -> None:
        """从数据库中删除消息记录"""
        try:
            from sqlalchemy import delete

            from src.common.database.core import get_db_session
            from src.common.database.core.models import Messages

            message_id = message_data.get("message_id")
            if not message_id:
                return

            async with get_db_session() as session:
                stmt = delete(Messages).where(Messages.message_id == message_id)
                result = await session.execute(stmt)
                await session.commit()

                if result.rowcount > 0:
                    logger.debug(f"已从数据库删除被阻止的消息: {message_id}")

        except Exception as e:
            logger.error(f"删除消息记录失败: {e}")
