"""
嵌入向量生成器：优先使用配置的 embedding API，sentence-transformers 作为备选
"""

from __future__ import annotations

import asyncio

import numpy as np

from src.common.logger import get_logger

logger = get_logger(__name__)


class EmbeddingGenerator:
    """
    嵌入向量生成器

    策略：
    1. 优先使用配置的 embedding API（通过 LLMRequest）
    2. 如果 API 不可用，回退到本地 sentence-transformers
    3. 如果 sentence-transformers 未安装，使用随机向量（仅测试）

    优点：
    - 降低本地运算负载
    - 即使未安装 sentence-transformers 也可正常运行
    - 保持与现有系统的一致性
    """

    def __init__(
        self,
        use_api: bool = True,
        fallback_model_name: str = "paraphrase-multilingual-MiniLM-L12-v2",
    ):
        """
        初始化嵌入生成器

        Args:
            use_api: 是否优先使用 API（默认 True）
            fallback_model_name: 回退本地模型名称
        """
        self.use_api = use_api
        self.fallback_model_name = fallback_model_name

        # API 相关
        self._llm_request = None
        self._api_available = False
        self._api_dimension = None

        # 本地模型相关
        self._local_model = None
        self._local_model_loaded = False

    async def _initialize_api(self):
        """初始化 embedding API"""
        if self._api_available:
            return

        try:
            from src.config.config import model_config
            from src.llm_models.utils_model import LLMRequest

            embedding_config = model_config.model_task_config.embedding
            self._llm_request = LLMRequest(
                model_set=embedding_config,
                request_type="memory_graph.embedding"
            )

            # 获取嵌入维度
            if hasattr(embedding_config, "embedding_dimension") and embedding_config.embedding_dimension:
                self._api_dimension = embedding_config.embedding_dimension

            self._api_available = True
            logger.info(f"✅ Embedding API 初始化成功 (维度: {self._api_dimension})")

        except Exception as e:
            logger.warning(f"⚠️  Embedding API 初始化失败: {e}")
            self._api_available = False

    def _load_local_model(self):
        """延迟加载本地模型"""
        if not self._local_model_loaded:
            try:
                from sentence_transformers import SentenceTransformer

                logger.info(f"📦 加载本地嵌入模型: {self.fallback_model_name}")
                self._local_model = SentenceTransformer(self.fallback_model_name)
                self._local_model_loaded = True
                logger.info("✅ 本地嵌入模型加载成功")
            except ImportError:
                logger.warning(
                    "⚠️  sentence-transformers 未安装，将使用随机向量（仅测试用）\n"
                    "   安装方法: pip install sentence-transformers"
                )
                self._local_model_loaded = False
            except Exception as e:
                logger.warning(f"⚠️  本地模型加载失败: {e}")
                self._local_model_loaded = False

    async def generate(self, text: str) -> np.ndarray:
        """
        生成单个文本的嵌入向量

        策略：
        1. 优先使用 API
        2. API 失败则使用本地模型
        3. 本地模型不可用则使用随机向量

        Args:
            text: 输入文本

        Returns:
            嵌入向量
        """
        if not text or not text.strip():
            logger.warning("输入文本为空，返回零向量")
            dim = self._get_dimension()
            return np.zeros(dim, dtype=np.float32)

        try:
            # 策略 1: 使用 API
            if self.use_api:
                embedding = await self._generate_with_api(text)
                if embedding is not None:
                    return embedding

            # 策略 2: 使用本地模型
            embedding = await self._generate_with_local_model(text)
            if embedding is not None:
                return embedding

            # 策略 3: 随机向量（仅测试）
            logger.warning(f"⚠️  所有嵌入策略失败，使用随机向量: {text[:30]}...")
            dim = self._get_dimension()
            return np.random.rand(dim).astype(np.float32)

        except Exception as e:
            logger.error(f"❌ 嵌入生成失败: {e}", exc_info=True)
            dim = self._get_dimension()
            return np.random.rand(dim).astype(np.float32)

    async def _generate_with_api(self, text: str) -> np.ndarray | None:
        """使用 API 生成嵌入"""
        try:
            # 初始化 API
            if not self._api_available:
                await self._initialize_api()

            if not self._api_available or not self._llm_request:
                return None

            # 调用 API
            embedding_list, model_name = await self._llm_request.get_embedding(text)

            if embedding_list and len(embedding_list) > 0:
                embedding = np.array(embedding_list, dtype=np.float32)
                logger.debug(f"🌐 API 生成嵌入: {text[:30]}... -> {len(embedding)}维 (模型: {model_name})")
                return embedding

            return None

        except Exception as e:
            logger.debug(f"API 嵌入生成失败: {e}")
            return None

    async def _generate_with_local_model(self, text: str) -> np.ndarray | None:
        """使用本地模型生成嵌入"""
        try:
            # 加载本地模型
            if not self._local_model_loaded:
                self._load_local_model()

            if not self._local_model_loaded or not self._local_model:
                return None

            # 在线程池中运行
            loop = asyncio.get_event_loop()
            embedding = await loop.run_in_executor(None, self._encode_single_local, text)

            logger.debug(f"💻 本地生成嵌入: {text[:30]}... -> {len(embedding)}维")
            return embedding

        except Exception as e:
            logger.debug(f"本地模型嵌入生成失败: {e}")
            return None

    def _encode_single_local(self, text: str) -> np.ndarray:
        """同步编码单个文本（本地模型）"""
        if self._local_model is None:
            raise RuntimeError("本地模型未加载")
        embedding = self._local_model.encode(text, convert_to_numpy=True)  # type: ignore
        return embedding.astype(np.float32)

    def _get_dimension(self) -> int:
        """获取嵌入维度"""
        # 优先使用 API 维度
        if self._api_dimension:
            return self._api_dimension

        # 其次使用本地模型维度
        if self._local_model_loaded and self._local_model:
            try:
                return self._local_model.get_sentence_embedding_dimension()
            except Exception:
                pass

        # 默认 384（sentence-transformers 常用维度）
        return 384

    async def generate_batch(self, texts: list[str]) -> list[np.ndarray]:
        """
        批量生成嵌入向量

        Args:
            texts: 文本列表

        Returns:
            嵌入向量列表
        """
        if not texts:
            return []

        try:
            # 过滤空文本
            valid_texts = [t for t in texts if t and t.strip()]
            if not valid_texts:
                logger.warning("所有文本为空，返回零向量列表")
                dim = self._get_dimension()
                return [np.zeros(dim, dtype=np.float32) for _ in texts]

            # 使用 API 批量生成（如果可用）
            if self.use_api:
                results = await self._generate_batch_with_api(valid_texts)
                if results:
                    return results

            # 回退到逐个生成
            results = []
            for text in valid_texts:
                embedding = await self.generate(text)
                results.append(embedding)

            logger.info(f"✅ 批量生成嵌入: {len(texts)} 个文本")
            return results

        except Exception as e:
            logger.error(f"❌ 批量嵌入生成失败: {e}", exc_info=True)
            dim = self._get_dimension()
            return [np.random.rand(dim).astype(np.float32) for _ in texts]

    async def _generate_batch_with_api(self, texts: list[str]) -> list[np.ndarray] | None:
        """使用 API 批量生成"""
        try:
            # 对于大多数 API，批量调用就是多次单独调用
            # 这里保持简单，逐个调用
            results = []
            for text in texts:
                embedding = await self._generate_with_api(text)
                if embedding is None:
                    return None  # 如果任何一个失败，返回 None 触发回退
                results.append(embedding)
            return results
        except Exception as e:
            logger.debug(f"API 批量生成失败: {e}")
            return None

    def get_embedding_dimension(self) -> int:
        """获取嵌入向量维度"""
        return self._get_dimension()


# 全局单例
_global_generator: EmbeddingGenerator | None = None


def get_embedding_generator(
    use_api: bool = True,
    fallback_model_name: str = "paraphrase-multilingual-MiniLM-L12-v2",
) -> EmbeddingGenerator:
    """
    获取全局嵌入生成器单例

    Args:
        use_api: 是否优先使用 API
        fallback_model_name: 回退本地模型名称

    Returns:
        EmbeddingGenerator 实例
    """
    global _global_generator
    if _global_generator is None:
        _global_generator = EmbeddingGenerator(
            use_api=use_api,
            fallback_model_name=fallback_model_name
        )
    return _global_generator
