"""
表达系统工具函数
提供消息过滤、文本相似度计算、加权随机抽样等功能
"""
import difflib
import random
import re
from typing import Any

from src.common.logger import get_logger

logger = get_logger("express_utils")


def filter_message_content(content: str | None) -> str:
    """
    过滤消息内容，移除回复、@、图片等格式

    Args:
        content: 原始消息内容

    Returns:
        过滤后的纯文本内容
    """
    if not content:
        return ""

    # 移除以[回复开头、]结尾的部分，包括后面的"，说："部分
    content = re.sub(r"\[回复.*?\]，说：\s*", "", content)
    # 移除@<...>格式的内容
    content = re.sub(r"@<[^>]*>", "", content)
    # 移除[图片:...]格式的图片ID
    content = re.sub(r"\[图片:[^\]]*\]", "", content)
    # 移除[表情包：...]格式的内容
    content = re.sub(r"\[表情包：[^\]]*\]", "", content)

    return content.strip()


def calculate_similarity(text1: str, text2: str) -> float:
    """
    计算两个文本的相似度，返回0-1之间的值

    Args:
        text1: 第一个文本
        text2: 第二个文本

    Returns:
        相似度值 (0-1)
    """
    return difflib.SequenceMatcher(None, text1, text2).ratio()


def weighted_sample(population: list[dict], k: int, weight_key: str | None = None) -> list[dict]:
    """
    加权随机抽样函数

    Args:
        population: 待抽样的数据列表
        k: 抽样数量
        weight_key: 权重字段名，如果为None则等概率抽样

    Returns:
        抽样结果列表
    """
    if not population or k <= 0:
        return []

    if len(population) <= k:
        return population.copy()

    # 如果指定了权重字段
    if weight_key and all(weight_key in item for item in population):
        try:
            # 获取权重
            weights = [float(item.get(weight_key, 1.0)) for item in population]
            # 使用random.choices进行加权抽样
            return random.choices(population, weights=weights, k=k)
        except (ValueError, TypeError) as e:
            logger.warning(f"加权抽样失败，使用等概率抽样: {e}")

    # 等概率抽样
    selected = []
    population_copy = population.copy()

    for _ in range(k):
        if not population_copy:
            break
        # 随机选择一个元素
        idx = random.randint(0, len(population_copy) - 1)
        selected.append(population_copy.pop(idx))

    return selected


def normalize_text(text: str) -> str:
    """
    标准化文本，移除多余空白字符

    Args:
        text: 输入文本

    Returns:
        标准化后的文本
    """
    # 替换多个连续空白字符为单个空格
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_keywords(text: str, max_keywords: int = 10) -> list[str]:
    """
    简单的关键词提取（基于词频）

    Args:
        text: 输入文本
        max_keywords: 最大关键词数量

    Returns:
        关键词列表
    """
    if not text:
        return []

    try:
        import jieba.analyse

        # 使用TF-IDF提取关键词
        keywords = jieba.analyse.extract_tags(text, topK=max_keywords)
        return keywords
    except ImportError:
        logger.warning("jieba未安装，无法提取关键词")
        # 简单分词
        words = text.split()
        return words[:max_keywords]


def format_expression_pair(situation: str, style: str, index: int | None = None) -> str:
    """
    格式化表达方式对

    Args:
        situation: 情境
        style: 风格
        index: 序号（可选）

    Returns:
        格式化后的字符串
    """
    if index is not None:
        return f'{index}. 当"{situation}"时，使用"{style}"'
    else:
        return f'当"{situation}"时，使用"{style}"'


def parse_expression_pair(text: str) -> tuple[str, str] | None:
    """
    解析表达方式对文本

    Args:
        text: 格式化的表达方式对文本

    Returns:
        (situation, style) 或 None
    """
    # 匹配格式：当"..."时，使用"..."
    match = re.search(r'当"(.+?)"时，使用"(.+?)"', text)
    if match:
        return match.group(1), match.group(2)
    return None


def batch_filter_duplicates(expressions: list[dict[str, Any]], key_fields: list[str]) -> list[dict[str, Any]]:
    """
    批量去重表达方式

    Args:
        expressions: 表达方式列表
        key_fields: 用于去重的字段名列表

    Returns:
        去重后的表达方式列表
    """
    seen = set()
    unique_expressions = []

    for expr in expressions:
        # 构建去重key
        key_values = tuple(expr.get(field, "") for field in key_fields)

        if key_values not in seen:
            seen.add(key_values)
            unique_expressions.append(expr)

    return unique_expressions


def calculate_time_weight(last_active_time: float, current_time: float, half_life_days: int = 30) -> float:
    """
    根据时间计算权重（时间衰减）

    Args:
        last_active_time: 最后活跃时间戳
        current_time: 当前时间戳
        half_life_days: 半衰期天数

    Returns:
        权重值 (0-1)
    """
    time_diff_days = (current_time - last_active_time) / 86400  # 转换为天数
    if time_diff_days < 0:
        return 1.0

    # 使用指数衰减公式
    decay_rate = 0.693 / half_life_days  # ln(2) / half_life
    weight = max(0.01, min(1.0, 2 ** (-decay_rate * time_diff_days)))

    return weight


def merge_expressions_from_multiple_chats(
    expressions_dict: dict[str, list[dict[str, Any]]], max_total: int = 100
) -> list[dict[str, Any]]:
    """
    合并多个聊天室的表达方式

    Args:
        expressions_dict: {chat_id: [expressions]}
        max_total: 最大合并数量

    Returns:
        合并后的表达方式列表
    """
    all_expressions = []

    # 收集所有表达方式
    for chat_id, expressions in expressions_dict.items():
        for expr in expressions:
            # 添加source_id标识
            expr_with_source = expr.copy()
            expr_with_source["source_id"] = chat_id
            all_expressions.append(expr_with_source)

    # 按count或last_active_time排序
    if all_expressions and "count" in all_expressions[0]:
        all_expressions.sort(key=lambda x: x.get("count", 0), reverse=True)
    elif all_expressions and "last_active_time" in all_expressions[0]:
        all_expressions.sort(key=lambda x: x.get("last_active_time", 0), reverse=True)

    # 去重（基于situation和style）
    all_expressions = batch_filter_duplicates(all_expressions, ["situation", "style"])

    # 限制数量
    return all_expressions[:max_total]
