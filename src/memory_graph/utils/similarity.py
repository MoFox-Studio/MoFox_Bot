"""
相似度计算工具

提供统一的向量相似度计算函数
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np


def cosine_similarity(vec1: "np.ndarray", vec2: "np.ndarray") -> float:
    """
    计算两个向量的余弦相似度

    Args:
        vec1: 第一个向量
        vec2: 第二个向量

    Returns:
        余弦相似度 (0.0-1.0)
    """
    try:
        import numpy as np

        # 确保是numpy数组
        if not isinstance(vec1, np.ndarray):
            vec1 = np.array(vec1)
        if not isinstance(vec2, np.ndarray):
            vec2 = np.array(vec2)

        # 归一化
        vec1_norm = np.linalg.norm(vec1)
        vec2_norm = np.linalg.norm(vec2)

        if vec1_norm == 0 or vec2_norm == 0:
            return 0.0

        # 余弦相似度
        similarity = np.dot(vec1, vec2) / (vec1_norm * vec2_norm)

        # 确保在 [0, 1] 范围内（处理浮点误差）
        return float(np.clip(similarity, 0.0, 1.0))

    except Exception:
        return 0.0


__all__ = ["cosine_similarity"]
