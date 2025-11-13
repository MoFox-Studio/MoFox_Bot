"""
在线朴素贝叶斯分类器
支持增量学习和知识衰减
"""
import math
from collections import Counter, defaultdict

from src.common.logger import get_logger

logger = get_logger("expressor.online_nb")


class OnlineNaiveBayes:
    """在线朴素贝叶斯分类器"""

    def __init__(self, alpha: float = 0.5, beta: float = 0.5, gamma: float = 1.0, vocab_size: int = 200000):
        """
        Args:
            alpha: 词频平滑参数
            beta: 类别先验平滑参数
            gamma: 衰减因子 (0-1之间，1表示不衰减)
            vocab_size: 词汇表大小
        """
        self.alpha = alpha
        self.beta = beta
        self.gamma = gamma
        self.V = vocab_size

        # 类别统计
        self.cls_counts: dict[str, float] = defaultdict(float)  # cid -> total token count
        self.token_counts: dict[str, dict[str, float]] = defaultdict(
            lambda: defaultdict(float)
        )  # cid -> term -> count

        # 缓存
        self._logZ: dict[str, float] = {}  # cache log(∑counts + Vα)

    def score_batch(self, tf: Counter, cids: list[str]) -> dict[str, float]:
        """
        批量计算候选的贝叶斯分数

        Args:
            tf: 查询文本的词频Counter
            cids: 候选类别ID列表

        Returns:
            每个候选的分数字典
        """
        total_cls = sum(self.cls_counts.values())
        n_cls = max(1, len(self.cls_counts))
        denom_prior = math.log(total_cls + self.beta * n_cls)

        out: dict[str, float] = {}
        for cid in cids:
            # 计算先验概率 log P(c)
            prior = math.log(self.cls_counts[cid] + self.beta) - denom_prior
            s = prior

            # 计算似然概率 log P(w|c)
            logZ = self._logZ_c(cid)
            tc = self.token_counts[cid]

            for term, qtf in tf.items():
                num = tc.get(term, 0.0) + self.alpha
                s += qtf * (math.log(num) - logZ)

            out[cid] = s
        return out

    def update_positive(self, tf: Counter, cid: str):
        """
        正反馈更新

        Args:
            tf: 词频Counter
            cid: 类别ID
        """
        inc = 0.0
        tc = self.token_counts[cid]

        # 更新词频统计
        for term, c in tf.items():
            tc[term] += float(c)
            inc += float(c)

        # 更新类别统计
        self.cls_counts[cid] += inc
        self._invalidate(cid)

    def decay(self, factor: float | None = None):
        """
        知识衰减（遗忘机制）

        Args:
            factor: 衰减因子，如果为None则使用self.gamma
        """
        g = self.gamma if factor is None else factor
        if g >= 1.0:
            return

        # 对所有统计进行衰减
        for cid in list(self.cls_counts.keys()):
            self.cls_counts[cid] *= g
            for term in list(self.token_counts[cid].keys()):
                self.token_counts[cid][term] *= g
            self._invalidate(cid)

        logger.debug(f"应用知识衰减，衰减因子: {g}")

    def _logZ_c(self, cid: str) -> float:
        """
        计算归一化因子logZ

        Args:
            cid: 类别ID

        Returns:
            log(Z_c)
        """
        if cid not in self._logZ:
            Z = self.cls_counts[cid] + self.V * self.alpha
            self._logZ[cid] = math.log(max(Z, 1e-12))
        return self._logZ[cid]

    def _invalidate(self, cid: str):
        """
        使缓存失效

        Args:
            cid: 类别ID
        """
        if cid in self._logZ:
            del self._logZ[cid]

    def get_stats(self) -> dict:
        """获取统计信息"""
        return {
            "n_classes": len(self.cls_counts),
            "n_tokens": sum(len(tc) for tc in self.token_counts.values()),
            "total_counts": sum(self.cls_counts.values()),
        }
