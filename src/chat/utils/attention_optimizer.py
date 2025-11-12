"""
注意力优化器 - 防止提示词过度相似导致LLM注意力机制退化

通过轻量级随机化技术，在保持语义不变的前提下增加提示词结构多样性，
避免短时间内重复发送高度相似的提示词导致模型回复趋同。

优化策略：
1. 轻量级噪声：随机调整空白字符、换行数量
2. 块重排：定义可交换的block组，随机调整顺序
3. 语义变体：使用同义措辞替换固定模板文本
"""

import hashlib
import random
import re
from typing import Any, Literal

from src.common.logger import get_logger
from src.config.config import global_config

logger = get_logger("attention_optimizer")


class AttentionOptimizer:
    """提示词注意力优化器"""

    # 可交换的block组定义（组内block可以随机排序）
    # 每个组是一个列表，包含可以互换位置的block名称
    SWAPPABLE_BLOCK_GROUPS = [
        # 用户相关信息组（记忆、关系、表达习惯）
        ["memory_block", "relation_info_block", "expression_habits_block"],
        # 上下文增强组（工具、知识、跨群）
        ["tool_info_block", "knowledge_prompt", "cross_context_block"],
        # 元信息组（时间、身份、日程）
        ["time_block", "identity_block", "schedule_block"],
    ]

    # 语义等价的文本替换模板
    # 格式: {原始文本: [替换选项1, 替换选项2, ...]}
    SEMANTIC_VARIANTS = {
        "当前时间": ["当前时间", "现在是", "此时此刻", "时间"],
        "最近的系统通知": ["最近的系统通知", "系统通知", "通知消息", "最新通知"],
        "聊天历史": ["聊天历史", "对话记录", "历史消息", "之前的对话"],
        "你的任务是": ["你的任务是", "请", "你需要", "你应当"],
        "请注意": ["请注意", "注意", "请留意", "需要注意"],
    }

    def __init__(
        self,
        enable_noise: bool = True,
        enable_semantic_variants: bool = False,
        noise_strength: Literal["light", "medium", "heavy"] = "light",
        cache_key_suffix: str = "",
    ):
        """
        初始化注意力优化器

        Args:
            enable_noise: 是否启用轻量级噪声注入（空白字符调整）
            enable_semantic_variants: 是否启用语义变体替换（实验性）
            noise_strength: 噪声强度 (light/medium/heavy)
            cache_key_suffix: 缓存键后缀，用于区分不同的优化配置
        """
        self.enable_noise = enable_noise
        self.enable_semantic_variants = enable_semantic_variants
        self.noise_strength = noise_strength
        self.cache_key_suffix = cache_key_suffix

        # 噪声强度配置
        self.noise_config = {
            "light": {"newline_range": (1, 2), "space_range": (0, 2), "indent_adjust": False},
            "medium": {"newline_range": (1, 3), "space_range": (0, 4), "indent_adjust": True},
            "heavy": {"newline_range": (1, 4), "space_range": (0, 6), "indent_adjust": True},
        }



    def optimize_prompt(self, prompt_text: str, context_data: dict[str, Any]) -> str:
        """
        优化提示词，增加结构多样性

        Args:
            prompt_text: 原始提示词文本
            context_data: 上下文数据字典，包含各个block的内容

        Returns:
            优化后的提示词文本
        """
        try:
            optimized = prompt_text

            # 步骤2: 语义变体替换（如果启用）
            if self.enable_semantic_variants:
                optimized = self._apply_semantic_variants(optimized)

            # 步骤3: 轻量级噪声注入（如果启用）
            if self.enable_noise:
                optimized = self._inject_noise(optimized)

            # 计算变化率
            change_rate = self._calculate_change_rate(prompt_text, optimized)
            logger.debug(f"提示词优化完成，变化率: {change_rate:.2%}")

            return optimized

        except Exception as e:
            logger.error(f"提示词优化失败: {e}", exc_info=True)
            return prompt_text  # 失败时返回原始文本

    def _shuffle_blocks(self, prompt_text: str, context_data: dict[str, Any]) -> str:
        """
        重排可交换的block组

        Args:
            prompt_text: 原始提示词
            context_data: 包含各block内容的字典

        Returns:
            重排后的提示词
        """
        try:
            # 对每个可交换组进行随机排序
            shuffled_context = context_data.copy()

            for group in self.SWAPPABLE_BLOCK_GROUPS:
                # 过滤出实际存在且非空的block
                existing_blocks = [
                    block for block in group if block in context_data and context_data[block]
                ]

                if len(existing_blocks) > 1:
                    # 随机打乱顺序
                    shuffled = existing_blocks.copy()
                    random.shuffle(shuffled)

                    # 如果打乱后的顺序与原顺序不同，记录日志
                    if shuffled != existing_blocks:
                        logger.debug(f"重排block组: {existing_blocks} -> {shuffled}")

                    # 注意：实际的重排需要在模板格式化之前进行
                    # 这里只是演示逻辑，真正的实现需要在 _format_with_context 中处理

            # 由于block重排需要在模板构建阶段进行，这里只返回原文本
            # 真正的重排逻辑需要集成到 Prompt 类的 _format_with_context 方法中
            return prompt_text

        except Exception as e:
            logger.error(f"Block重排失败: {e}", exc_info=True)
            return prompt_text

    def _apply_semantic_variants(self, text: str) -> str:
        """
        应用语义等价的文本替换

        Args:
            text: 原始文本

        Returns:
            替换后的文本
        """
        try:
            result = text

            for original, variants in self.SEMANTIC_VARIANTS.items():
                if original in result:
                    # 随机选择一个变体（包括原始文本）
                    replacement = random.choice(variants)
                    result = result.replace(original, replacement, 1)  # 只替换第一次出现

            return result

        except Exception as e:
            logger.error(f"语义变体替换失败: {e}", exc_info=True)
            return text

    def _inject_noise(self, text: str) -> str:
        """
        注入轻量级噪声（空白字符调整）

        Args:
            text: 原始文本

        Returns:
            注入噪声后的文本
        """
        try:
            config = self.noise_config[self.noise_strength]
            result = text

            # 1. 调整block之间的换行数量
            result = self._adjust_newlines(result, config["newline_range"])

            # 2. 在某些位置添加随机空格（保持可读性）
            result = self._adjust_spaces(result, config["space_range"])

            # 3. 调整缩进（仅在medium/heavy模式下）
            if config["indent_adjust"]:
                result = self._adjust_indentation(result)

            return result

        except Exception as e:
            logger.error(f"噪声注入失败: {e}", exc_info=True)
            return text

    def _adjust_newlines(self, text: str, newline_range: tuple[int, int]) -> str:
        """
        调整连续换行的数量

        Args:
            text: 原始文本
            newline_range: 换行数量范围 (min, max)

        Returns:
            调整后的文本
        """
        # 匹配连续的换行符
        pattern = r"\n{2,}"

        def replace_newlines(match):
            # 随机选择新的换行数量
            count = random.randint(*newline_range)
            return "\n" * count

        return re.sub(pattern, replace_newlines, text)

    def _adjust_spaces(self, text: str, space_range: tuple[int, int]) -> str:
        """
        在某些位置添加随机空格

        Args:
            text: 原始文本
            space_range: 空格数量范围 (min, max)

        Returns:
            调整后的文本
        """
        # 在行尾随机添加空格（不可见但会改变文本哈希）
        lines = text.split("\n")
        result_lines = []

        for line in lines:
            if line.strip() and random.random() < 0.3:  # 30%概率添加空格
                spaces = " " * random.randint(*space_range)
                result_lines.append(line + spaces)
            else:
                result_lines.append(line)

        return "\n".join(result_lines)

    def _adjust_indentation(self, text: str) -> str:
        """
        微调某些行的缩进（保持语义）

        Args:
            text: 原始文本

        Returns:
            调整后的文本
        """
        lines = text.split("\n")
        result_lines = []

        for line in lines:
            # 检测列表项
            list_match = re.match(r"^(\s*)([-*•])\s", line)
            if list_match and random.random() < 0.5:
                indent = list_match.group(1)
                marker = list_match.group(2)
                # 随机调整缩进（±2个空格）
                adjust = random.choice([-2, 0, 2])
                new_indent = " " * max(0, len(indent) + adjust)
                new_line = line.replace(indent + marker, new_indent + marker, 1)
                result_lines.append(new_line)
            else:
                result_lines.append(line)

        return "\n".join(result_lines)

    def _calculate_change_rate(self, original: str, optimized: str) -> float:
        """
        计算文本变化率

        Args:
            original: 原始文本
            optimized: 优化后的文本

        Returns:
            变化率（0-1之间的浮点数）
        """
        if not original or not optimized:
            return 0.0

        # 使用简单的字符差异比率
        diff_chars = sum(1 for a, b in zip(original, optimized) if a != b)
        max_len = max(len(original), len(optimized))

        return diff_chars / max_len if max_len > 0 else 0.0

    def get_cache_key(self, prompt_text: str) -> str:
        """
        生成优化后提示词的缓存键

        由于注意力优化会改变提示词内容，缓存键也需要相应调整

        Args:
            prompt_text: 提示词文本

        Returns:
            缓存键字符串
        """
        # 计算文本哈希
        text_hash = hashlib.md5(prompt_text.encode()).hexdigest()[:8]

        # 添加随机后缀，确保相似提示词有不同的缓存键
        random_suffix = random.randint(1000, 9999)

        return f"{text_hash}_{random_suffix}_{self.cache_key_suffix}"


def get_attention_optimizer_from_config() -> AttentionOptimizer:
    """
    从全局配置创建注意力优化器实例

    Returns:
        配置好的 AttentionOptimizer 实例
    """
    # 从配置中读取设置（如果存在）
    config = getattr(global_config, "attention_optimization", None)

    if not config:
        # 使用默认配置
        return AttentionOptimizer(
            enable_noise=True,
            enable_semantic_variants=False,  # 实验性功能，默认关闭
            noise_strength="light",
        )

    # config 是 Pydantic 模型对象，直接访问属性
    return AttentionOptimizer(
        enable_noise=config.enable_noise,
        enable_semantic_variants=config.enable_semantic_variants,
        noise_strength=config.noise_strength,
    )


# 全局单例
_global_optimizer: AttentionOptimizer | None = None


def get_attention_optimizer() -> AttentionOptimizer:
    """获取全局注意力优化器实例"""
    global _global_optimizer
    if _global_optimizer is None:
        _global_optimizer = get_attention_optimizer_from_config()
    return _global_optimizer
