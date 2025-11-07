"""
基于Online Naive Bayes的表达模型
支持候选表达的动态添加和在线学习
"""
import os
import pickle
from collections import Counter, defaultdict

from src.common.logger import get_logger

from .online_nb import OnlineNaiveBayes
from .tokenizer import Tokenizer

logger = get_logger("expressor.model")


class ExpressorModel:
    """直接使用朴素贝叶斯精排（可在线学习）"""

    def __init__(
        self, alpha: float = 0.5, beta: float = 0.5, gamma: float = 1.0, vocab_size: int = 200000, use_jieba: bool = True
    ):
        """
        Args:
            alpha: 词频平滑参数
            beta: 类别先验平滑参数
            gamma: 衰减因子
            vocab_size: 词汇表大小
            use_jieba: 是否使用jieba分词
        """
        # 初始化分词器
        self.tokenizer = Tokenizer(stopwords=set(), use_jieba=use_jieba)

        # 初始化在线朴素贝叶斯模型
        self.nb = OnlineNaiveBayes(alpha=alpha, beta=beta, gamma=gamma, vocab_size=vocab_size)

        # 候选表达管理
        self._candidates: dict[str, str] = {}  # cid -> text (style)
        self._situations: dict[str, str] = {}  # cid -> situation (不参与计算)

        logger.info(
            f"ExpressorModel初始化完成 (alpha={alpha}, beta={beta}, gamma={gamma}, vocab_size={vocab_size}, use_jieba={use_jieba})"
        )

    def add_candidate(self, cid: str, text: str, situation: str | None = None):
        """
        添加候选文本和对应的situation

        Args:
            cid: 候选ID
            text: 表达文本 (style)
            situation: 情境文本
        """
        self._candidates[cid] = text
        if situation is not None:
            self._situations[cid] = situation

        # 确保在nb模型中初始化该候选的计数
        if cid not in self.nb.cls_counts:
            self.nb.cls_counts[cid] = 0.0
        if cid not in self.nb.token_counts:
            self.nb.token_counts[cid] = defaultdict(float)

    def remove_candidate(self, cid: str) -> bool:
        """
        删除候选文本

        Args:
            cid: 候选ID

        Returns:
            是否删除成功
        """
        removed = False

        if cid in self._candidates:
            del self._candidates[cid]
            removed = True

        if cid in self._situations:
            del self._situations[cid]

        # 从nb模型中删除
        if cid in self.nb.cls_counts:
            del self.nb.cls_counts[cid]

        if cid in self.nb.token_counts:
            del self.nb.token_counts[cid]

        return removed

    def predict(self, text: str, k: int | None = None) -> tuple[str | None, dict[str, float]]:
        """
        直接对所有候选进行朴素贝叶斯评分

        Args:
            text: 查询文本
            k: 返回前k个候选，如果为None则返回所有

        Returns:
            (最佳候选ID, 所有候选的分数字典)
        """
        # 1. 分词
        toks = self.tokenizer.tokenize(text)
        if not toks or not self._candidates:
            return None, {}

        # 2. 计算词频
        tf = Counter(toks)
        all_cids = list(self._candidates.keys())

        # 3. 批量评分
        scores = self.nb.score_batch(tf, all_cids)

        if not scores:
            return None, {}

        # 4. 根据k参数限制返回的候选数量
        if k is not None and k > 0:
            sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
            limited_scores = dict(sorted_scores[:k])
            best = sorted_scores[0][0] if sorted_scores else None
            return best, limited_scores
        else:
            best = max(scores.items(), key=lambda x: x[1])[0]
            return best, scores

    def update_positive(self, text: str, cid: str):
        """
        更新正反馈学习

        Args:
            text: 输入文本
            cid: 目标类别ID
        """
        toks = self.tokenizer.tokenize(text)
        if not toks:
            return

        tf = Counter(toks)
        self.nb.update_positive(tf, cid)

    def decay(self, factor: float | None = None):
        """
        应用知识衰减

        Args:
            factor: 衰减因子，如果为None则使用模型配置的gamma
        """
        self.nb.decay(factor)

    def get_candidate_info(self, cid: str) -> tuple[str | None, str | None]:
        """
        获取候选信息

        Args:
            cid: 候选ID

        Returns:
            (style文本, situation文本)
        """
        style = self._candidates.get(cid)
        situation = self._situations.get(cid)
        return style, situation

    def get_all_candidates(self) -> dict[str, tuple[str, str]]:
        """
        获取所有候选

        Returns:
            {cid: (style, situation)}
        """
        result = {}
        for cid in self._candidates.keys():
            style, situation = self.get_candidate_info(cid)
            result[cid] = (style, situation)
        return result

    def save(self, path: str):
        """
        保存模型到文件

        Args:
            path: 保存路径
        """
        os.makedirs(os.path.dirname(path), exist_ok=True)

        data = {
            "candidates": self._candidates,
            "situations": self._situations,
            "nb_cls_counts": dict(self.nb.cls_counts),
            "nb_token_counts": {k: dict(v) for k, v in self.nb.token_counts.items()},
            "nb_alpha": self.nb.alpha,
            "nb_beta": self.nb.beta,
            "nb_gamma": self.nb.gamma,
            "nb_V": self.nb.V,
        }

        with open(path, "wb") as f:
            pickle.dump(data, f)

        logger.info(f"模型已保存到 {path}")

    def load(self, path: str):
        """
        从文件加载模型

        Args:
            path: 加载路径
        """
        if not os.path.exists(path):
            logger.warning(f"模型文件不存在: {path}")
            return

        with open(path, "rb") as f:
            data = pickle.load(f)

        self._candidates = data["candidates"]
        self._situations = data["situations"]

        # 恢复nb模型的参数
        self.nb.alpha = data["nb_alpha"]
        self.nb.beta = data["nb_beta"]
        self.nb.gamma = data["nb_gamma"]
        self.nb.V = data["nb_V"]

        # 恢复统计数据
        self.nb.cls_counts = defaultdict(float, data["nb_cls_counts"])
        self.nb.token_counts = defaultdict(lambda: defaultdict(float))
        for cid, tc in data["nb_token_counts"].items():
            self.nb.token_counts[cid] = defaultdict(float, tc)

        logger.info(f"模型已从 {path} 加载")

    def get_stats(self) -> dict:
        """获取模型统计信息"""
        nb_stats = self.nb.get_stats()
        return {
            "n_candidates": len(self._candidates),
            "n_classes": nb_stats["n_classes"],
            "n_tokens": nb_stats["n_tokens"],
            "total_counts": nb_stats["total_counts"],
        }
