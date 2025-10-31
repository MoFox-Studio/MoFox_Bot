"""
文本分词器，支持中文Jieba分词
"""

from src.common.logger import get_logger

logger = get_logger("expressor.tokenizer")


class Tokenizer:
    """文本分词器，支持中文Jieba分词"""

    def __init__(self, stopwords: set = None, use_jieba: bool = True):
        """
        Args:
            stopwords: 停用词集合
            use_jieba: 是否使用jieba分词
        """
        self.stopwords = stopwords or set()
        self.use_jieba = use_jieba

        if use_jieba:
            try:
                import jieba

                jieba.initialize()
                logger.info("Jieba分词器初始化成功")
            except ImportError:
                logger.warning("Jieba未安装，将使用字符级分词")
                self.use_jieba = False

    def tokenize(self, text: str) -> list[str]:
        """
        分词并返回token列表

        Args:
            text: 输入文本

        Returns:
            token列表
        """
        if not text:
            return []

        # 使用jieba分词
        if self.use_jieba:
            try:
                import jieba

                tokens = list(jieba.cut(text))
            except Exception as e:
                logger.warning(f"Jieba分词失败，使用字符级分词: {e}")
                tokens = list(text)
        else:
            # 简单按字符分词
            tokens = list(text)

        # 过滤停用词和空字符串
        tokens = [token.strip() for token in tokens if token.strip() and token not in self.stopwords]

        return tokens
