"""
注意力优化器 - 提示词块重排

通过对可交换的block组进行随机排序，增加提示词结构多样性，
避免因固定的提示词结构导致模型注意力退化。
"""

import random
from typing import Any, ClassVar

from src.common.logger import get_logger

logger = get_logger("attention_optimizer_shuffle")


class BlockShuffler:
    """提示词Block重排器"""

    # 可交换的block组定义（组内block可以随机排序）
    # 每个组是一个列表，包含可以互换位置的block名称
    SWAPPABLE_BLOCK_GROUPS: ClassVar = [
        # 用户相关信息组（记忆、关系、表达习惯）
        ["memory_block", "relation_info_block", "expression_habits_block"],
        # 上下文增强组（工具、知识、跨群）
        ["tool_info_block", "knowledge_prompt", "cross_context_block"],
        # 元信息组（时间、身份、日程）
        ["time_block", "identity_block", "schedule_block"],
    ]

    @staticmethod
    def shuffle_prompt_blocks(prompt_template: str, context_data: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """
        根据定义的SWAPPABLE_BLOCK_GROUPS，对上下文数据中的block进行随机重排，
        并返回可能已修改的prompt模板和重排后的上下文。

        Args:
            prompt_template (str): 原始的提示词模板.
            context_data (dict[str, Any]): 包含各个block内容的上下文数据.

        Returns:
            tuple[str, dict[str, Any]]: (可能被修改的模板, 重排后的上下文数据).
        """
        try:
            # 这是一个简化的示例实现。
            # 实际的块重排需要在模板渲染前，通过操作占位符的顺序来实现。
            # 这里我们假设一个更直接的实现，即重新构建模板字符串。

            # 复制上下文以避免修改原始字典
            shuffled_context = context_data.copy()

            # 示例：假设模板中的占位符格式为 {block_name}
            # 我们需要解析模板，找到可重排的组，并重新构建模板字符串。

            # 注意：这是一个复杂的逻辑，通常需要一个简单的模板引擎或正则表达式来完成。
            # 为保持此函数职责单一，这里仅演示核心的重排逻辑，
            # 完整的模板重建逻辑应在调用此函数的地方处理。

            for group in BlockShuffler.SWAPPABLE_BLOCK_GROUPS:
                # 过滤出在当前上下文中实际存在的、非空的block
                existing_blocks = [
                    block for block in group if context_data.get(block)
                ]

                if len(existing_blocks) > 1:
                    # 随机打乱顺序
                    random.shuffle(existing_blocks)
                    logger.debug(f"重排block组: {group} -> {existing_blocks}")

                    # 这里的实现需要调用者根据 `existing_blocks` 的新顺序
                    # 去动态地重新组织 `prompt_template` 字符串。
                    # 例如，找到模板中与 `group` 相关的占位符部分，然后按新顺序替换它们。

            # 在这个简化版本中，我们不修改模板，仅返回原始模板和（未被使用的）重排后上下文
            # 实际应用中，调用方需要根据重排结果修改模板
            return prompt_template, shuffled_context

        except Exception as e:
            logger.error(f"Block重排失败: {e}", exc_info=True)
            return prompt_template, context_data
