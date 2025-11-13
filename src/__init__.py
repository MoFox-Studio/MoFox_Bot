import random
from collections.abc import Sequence

from colorama import Fore, init

from src.common.logger import get_logger

egg = get_logger("小彩蛋")


def weighted_choice(data: Sequence[str], weights: list[float] | None = None) -> str:
    """
    从 data 中按权重随机返回一条。
    若 weights 为 None，则所有元素权重默认为 1。
    """
    if weights is None:
        weights = [1.0] * len(data)

    if len(data) != len(weights):
        raise ValueError("data 和 weights 长度必须相等")

    # 计算累计权重区间
    total = 0.0
    acc = []
    for w in weights:
        total += w
        acc.append(total)

    if total <= 0:
        raise ValueError("总权重必须大于 0")

    # 随机落点
    r = random.random() * total
    # 二分查找落点所在的区间
    left, right = 0, len(acc) - 1
    while left < right:
        mid = (left + right) // 2
        if r < acc[mid]:
            right = mid
        else:
            left = mid + 1
    return data[left]


class BaseMain:
    """基础主程序类"""

    def __init__(self):
        """初始化基础主程序"""
        self.easter_egg()

    @staticmethod
    def easter_egg():
        # 彩蛋
        init()
        items = [
            "多年以后，面对AI行刑队，张三将会回想起他2023年在会议上讨论人工智能的那个下午",
            "你知道吗？诺狐的耳朵很软，很好rua",
            "喵喵~你的麦麦被猫娘入侵了喵~",
        ]
        w = [10, 5, 2]
        text = weighted_choice(items, w)
        rainbow_colors = [Fore.RED, Fore.YELLOW, Fore.GREEN, Fore.CYAN, Fore.BLUE, Fore.MAGENTA]
        rainbow_text = ""
        for i, char in enumerate(text):
            rainbow_text += rainbow_colors[i % len(rainbow_colors)] + char
        egg.info(rainbow_text)
