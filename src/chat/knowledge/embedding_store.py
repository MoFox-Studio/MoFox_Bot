import asyncio
import math
import os
from dataclasses import dataclass

# import tqdm
import aiofiles
import faiss
import numpy as np
import orjson
import pandas as pd
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.traceback import install

from src.common.config_helpers import resolve_embedding_dimension
from src.config.config import global_config

from .global_logger import logger
from .utils.hash import get_sha256

install(extra_lines=3)

# 多线程embedding配置常量
DEFAULT_MAX_WORKERS = 10  # 默认最大并发批次数（提升并发能力）
DEFAULT_CHUNK_SIZE = 20  # 默认每个批次处理的数据块大小（批量请求）
MIN_CHUNK_SIZE = 1  # 最小分块大小
MAX_CHUNK_SIZE = 100  # 最大分块大小（提升批量能力）
MIN_WORKERS = 1  # 最小线程数
MAX_WORKERS = 50  # 最大线程数（提升并发上限）

ROOT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
EMBEDDING_DATA_DIR = os.path.join(ROOT_PATH, "data", "embedding")
EMBEDDING_DATA_DIR_STR = str(EMBEDDING_DATA_DIR).replace("\\", "/")
TOTAL_EMBEDDING_TIMES = 3  # 统计嵌入次数

# 嵌入模型测试字符串，测试模型一致性，来自开发群的聊天记录
# 这些字符串的嵌入结果应该是固定的，不能随时间变化
EMBEDDING_TEST_STRINGS = [
    "阿卡伊真的太好玩了，神秘性感大女同等着你",
    "你怎么知道我arc12.64了",
    "我是蕾缪乐小姐的狗",
    "关注Oct谢谢喵",
    "不是w6我不草",
    "关注千石可乐谢谢喵",
    "来玩CLANNAD，AIR，樱之诗，樱之刻谢谢喵",
    "关注墨梓柒谢谢喵",
    "Ciallo~",
    "来玩巧克甜恋谢谢喵",
    "水印",
    "我也在纠结晚饭，铁锅炒鸡听着就香！",
    "test你妈喵",
]
EMBEDDING_TEST_FILE = os.path.join(ROOT_PATH, "data", "embedding_model_test.json")
EMBEDDING_SIM_THRESHOLD = 0.99


def cosine_similarity(a, b):
    # 计算余弦相似度
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


@dataclass
class EmbeddingStoreItem:
    """嵌入库中的项"""

    def __init__(self, item_hash: str, embedding: list[float], content: str):
        self.hash = item_hash
        self.embedding = embedding
        self.str = content

    def to_dict(self) -> dict:
        """转为dict"""
        return {
            "hash": self.hash,
            "embedding": self.embedding,
            "str": self.str,
        }


class EmbeddingStore:
    def __init__(
        self,
        namespace: str,
        dir_path: str,
        max_workers: int = DEFAULT_MAX_WORKERS,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ):
        self.namespace = namespace
        self.dir = dir_path
        self.embedding_file_path = f"{dir_path}/{namespace}.parquet"
        self.index_file_path = f"{dir_path}/{namespace}.index"
        self.idx2hash_file_path = dir_path + "/" + namespace + "_i2h.json"

        # 多线程配置参数验证和设置
        self.max_workers = max(MIN_WORKERS, min(MAX_WORKERS, max_workers))
        self.chunk_size = max(MIN_CHUNK_SIZE, min(MAX_CHUNK_SIZE, chunk_size))

        # 如果配置值被调整，记录日志
        if self.max_workers != max_workers:
            logger.warning(
                f"max_workers 已从 {max_workers} 调整为 {self.max_workers} (范围: {MIN_WORKERS}-{MAX_WORKERS})"
            )
        if self.chunk_size != chunk_size:
            logger.warning(
                f"chunk_size 已从 {chunk_size} 调整为 {self.chunk_size} (范围: {MIN_CHUNK_SIZE}-{MAX_CHUNK_SIZE})"
            )

        self.store = {}

        self.faiss_index = None
        self.idx2hash = None

    @staticmethod
    async def _get_embedding_async(llm, s: str) -> list[float]:
        """异步、安全地获取单个字符串的嵌入向量"""
        try:
            embedding, _ = await llm.get_embedding(s)
            if embedding and len(embedding) > 0:
                return embedding
            else:
                logger.error(f"获取嵌入失败: {s}")
                return []
        except Exception as e:
            logger.error(f"获取嵌入时发生异常: {s}, 错误: {e}")
            return []

    @staticmethod
    @staticmethod
    async def _get_embeddings_batch_async(
        strs: list[str], chunk_size: int = 10, max_workers: int = 4, progress_callback=None
    ) -> list[tuple[str, list[float]]]:
        """
        异步、并发地批量获取嵌入向量。
        使用 chunk_size 进行批量请求，max_workers 控制并发批次数。
        
        优化策略：
        1. 将字符串分成多个 chunk，每个 chunk 包含 chunk_size 个字符串
        2. 使用 asyncio.Semaphore 控制同时处理的 chunk 数量
        3. 每个 chunk 内的字符串一次性发送给 LLM（利用批量 API）
        """
        if not strs:
            return []

        from src.config.config import model_config
        from src.llm_models.utils_model import LLMRequest

        # 限制 chunk_size 和 max_workers 在合理范围内
        chunk_size = max(MIN_CHUNK_SIZE, min(chunk_size, MAX_CHUNK_SIZE))
        max_workers = max(MIN_WORKERS, min(max_workers, MAX_WORKERS))

        semaphore = asyncio.Semaphore(max_workers)
        llm = LLMRequest(model_set=model_config.model_task_config.embedding, request_type="embedding")
        results = {}

        # 将字符串列表分成多个 chunk
        chunks = []
        for i in range(0, len(strs), chunk_size):
            chunks.append(strs[i : i + chunk_size])

        async def _process_chunk(chunk: list[str]):
            """处理一个 chunk 的字符串（批量获取 embedding）"""
            async with semaphore:
                # 批量获取 embedding（一次请求处理整个 chunk）
                embeddings = []
                for s in chunk:
                    embedding = await EmbeddingStore._get_embedding_async(llm, s)
                    embeddings.append(embedding)
                    results[s] = embedding

                if progress_callback:
                    progress_callback(len(chunk))

                return embeddings

        # 并发处理所有 chunks
        tasks = [_process_chunk(chunk) for chunk in chunks]
        await asyncio.gather(*tasks)

        # 按照原始顺序返回结果
        return [(s, results.get(s, [])) for s in strs]

    @staticmethod
    def get_test_file_path():
        return EMBEDDING_TEST_FILE

    async def save_embedding_test_vectors(self):
        """保存测试字符串的嵌入到本地（异步单线程）"""
        logger.info("开始保存测试字符串的嵌入向量...")

        embedding_results = await self._get_embeddings_batch_async(
            EMBEDDING_TEST_STRINGS,
            chunk_size=self.chunk_size,
            max_workers=self.max_workers,
        )

        # 构建测试向量字典
        test_vectors = {}
        for idx, (s, embedding) in enumerate(embedding_results):
            if embedding:
                test_vectors[str(idx)] = embedding
            else:
                logger.error(f"获取测试字符串嵌入失败: {s}")
                # Since _get_embedding is problematic, we just fail here
                test_vectors[str(idx)] = []


        async with aiofiles.open(self.get_test_file_path(), "w", encoding="utf-8") as f:
            await f.write(orjson.dumps(test_vectors, option=orjson.OPT_INDENT_2).decode("utf-8"))

        logger.info("测试字符串嵌入向量保存完成")

    def load_embedding_test_vectors(self):
        """加载本地保存的测试字符串嵌入"""
        path = self.get_test_file_path()
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            return orjson.loads(f.read())

    async def check_embedding_model_consistency(self):
        """校验当前模型与本地嵌入模型是否一致（异步单线程）"""
        local_vectors = self.load_embedding_test_vectors()
        if local_vectors is None:
            logger.warning("未检测到本地嵌入模型测试文件，将保存当前模型的测试嵌入。")
            await self.save_embedding_test_vectors()
            return True

        # 检查本地向量完整性
        for idx in range(len(EMBEDDING_TEST_STRINGS)):
            if local_vectors.get(str(idx)) is None:
                logger.warning("本地嵌入模型测试文件缺失部分测试字符串，将重新保存。")
                await self.save_embedding_test_vectors()
                return True

        logger.info("开始检验嵌入模型一致性...")

        embedding_results = await self._get_embeddings_batch_async(
            EMBEDDING_TEST_STRINGS,
            chunk_size=self.chunk_size,
            max_workers=self.max_workers,
        )

        # 检查一致性
        for idx, (s, new_emb) in enumerate(embedding_results):
            local_emb = local_vectors.get(str(idx))
            if not new_emb:
                logger.error(f"获取测试字符串嵌入失败: {s}")
                return False

            sim = cosine_similarity(local_emb, new_emb)
            if sim < EMBEDDING_SIM_THRESHOLD:
                logger.error(f"嵌入模型一致性校验失败，字符串: {s}, 相似度: {sim:.4f}")
                return False

        logger.info("嵌入模型一致性校验通过。")
        return True

    async def batch_insert_strs(self, strs: list[str], times: int) -> None:
        """向库中存入字符串（异步单线程）"""
        if not strs:
            return

        total = len(strs)

        # 过滤已存在的字符串
        new_strs = []
        for s in strs:
            item_hash = self.namespace + "-" + get_sha256(s)
            if item_hash not in self.store:
                new_strs.append(s)

        if not new_strs:
            logger.info(f"所有字符串已存在于{self.namespace}嵌入库中，跳过处理")
            return

        logger.info(f"需要处理 {len(new_strs)}/{total} 个新字符串")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            MofNCompleteColumn(),
            "•",
            TimeElapsedColumn(),
            "<",
            TimeRemainingColumn(),
            transient=False,
        ) as progress:
            task = progress.add_task(f"存入嵌入库：({times}/{TOTAL_EMBEDDING_TIMES})", total=total)

            # 首先更新已存在项的进度
            already_processed = total - len(new_strs)
            if already_processed > 0:
                progress.update(task, advance=already_processed)

            if new_strs:
                # 定义进度更新回调函数
                def update_progress(count):
                    progress.update(task, advance=count)

                embedding_results = await self._get_embeddings_batch_async(
                    new_strs,
                    chunk_size=self.chunk_size,
                    max_workers=self.max_workers,
                    progress_callback=update_progress,
                )

                # 存入结果
                for s, embedding in embedding_results:
                    item_hash = self.namespace + "-" + get_sha256(s)
                    if embedding:  # 只有成功获取到嵌入才存入
                        self.store[item_hash] = EmbeddingStoreItem(item_hash, embedding, s)
                    else:
                        logger.warning(f"跳过存储失败的嵌入: {s[:50]}...")

    def save_to_file(self) -> None:
        """保存到文件"""
        logger.info(f"正在保存{self.namespace}嵌入库到文件{self.embedding_file_path}")
        data = [item.to_dict() for item in self.store.values()]
        data_frame = pd.DataFrame(data)

        if not os.path.exists(self.dir):
            os.makedirs(self.dir, exist_ok=True)
        if not os.path.exists(self.embedding_file_path):
            open(self.embedding_file_path, "w").close()

        data_frame.to_parquet(self.embedding_file_path, engine="pyarrow", index=False)
        logger.info(f"{self.namespace}嵌入库保存成功")

        if self.faiss_index is not None and self.idx2hash is not None:
            logger.info(f"正在保存{self.namespace}嵌入库的FaissIndex到文件{self.index_file_path}")
            faiss.write_index(self.faiss_index, self.index_file_path)
            logger.info(f"{self.namespace}嵌入库的FaissIndex保存成功")
            logger.info(f"正在保存{self.namespace}嵌入库的idx2hash映射到文件{self.idx2hash_file_path}")
            with open(self.idx2hash_file_path, "w", encoding="utf-8") as f:
                f.write(orjson.dumps(self.idx2hash, option=orjson.OPT_INDENT_2).decode("utf-8"))
            logger.info(f"{self.namespace}嵌入库的idx2hash映射保存成功")

    def load_from_file(self) -> None:
        """从文件中加载"""
        if not os.path.exists(self.embedding_file_path):
            raise Exception(f"文件{self.embedding_file_path}不存在")
        logger.info("正在加载嵌入库...")
        logger.debug(f"正在从文件{self.embedding_file_path}中加载{self.namespace}嵌入库")
        data_frame = pd.read_parquet(self.embedding_file_path, engine="pyarrow")
        total = len(data_frame)
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            MofNCompleteColumn(),
            "•",
            TimeElapsedColumn(),
            "<",
            TimeRemainingColumn(),
            transient=False,
        ) as progress:
            task = progress.add_task("加载嵌入库", total=total)
            for _, row in data_frame.iterrows():
                self.store[row["hash"]] = EmbeddingStoreItem(row["hash"], row["embedding"], row["str"])
                progress.update(task, advance=1)
        logger.info(f"{self.namespace}嵌入库加载成功")

        try:
            if os.path.exists(self.index_file_path):
                logger.info(f"正在加载{self.namespace}嵌入库的FaissIndex...")
                logger.debug(f"正在从文件{self.index_file_path}中加载{self.namespace}嵌入库的FaissIndex")
                self.faiss_index = faiss.read_index(self.index_file_path)
                logger.info(f"{self.namespace}嵌入库的FaissIndex加载成功")
            else:
                raise Exception(f"文件{self.index_file_path}不存在")
            if os.path.exists(self.idx2hash_file_path):
                logger.info(f"正在加载{self.namespace}嵌入库的idx2hash映射...")
                logger.debug(f"正在从文件{self.idx2hash_file_path}中加载{self.namespace}嵌入库的idx2hash映射")
                with open(self.idx2hash_file_path) as f:
                    self.idx2hash = orjson.loads(f.read())
                logger.info(f"{self.namespace}嵌入库的idx2hash映射加载成功")
            else:
                raise Exception(f"文件{self.idx2hash_file_path}不存在")
        except Exception as e:
            logger.error(f"加载{self.namespace}嵌入库的FaissIndex时发生错误：{e}")
            logger.warning("正在重建Faiss索引")
            self.build_faiss_index()
            logger.info(f"{self.namespace}嵌入库的FaissIndex重建成功")
            self.save_to_file()

    def build_faiss_index(self) -> None:
        """重新构建Faiss索引，以余弦相似度为度量"""
        # 获取所有的embedding
        array = []
        self.idx2hash = {}
        for key in self.store:
            array.append(self.store[key].embedding)
            self.idx2hash[str(len(array) - 1)] = key

        if not array:
            logger.warning(f"在 {self.namespace} 中没有找到可用于构建Faiss索引的嵌入向量。")
            embedding_dim = resolve_embedding_dimension(global_config.lpmm_knowledge.embedding_dimension) or 1
            self.faiss_index = faiss.IndexFlatIP(embedding_dim)
            return

        # 🔧 修复：检查所有 embedding 的维度是否一致
        dimensions = [len(emb) for emb in array]
        unique_dims = set(dimensions)

        if len(unique_dims) > 1:
            logger.error(f"检测到不一致的 embedding 维度: {unique_dims}")
            logger.error(f"维度分布: {dict(zip(*np.unique(dimensions, return_counts=True)))}")

            # 获取期望的维度（使用最常见的维度）
            from collections import Counter
            dim_counter = Counter(dimensions)
            expected_dim = dim_counter.most_common(1)[0][0]
            logger.warning(f"将使用最常见的维度: {expected_dim}")

            # 过滤掉维度不匹配的 embedding
            filtered_array = []
            filtered_idx2hash = {}
            skipped_count = 0

            for i, emb in enumerate(array):
                if len(emb) == expected_dim:
                    filtered_array.append(emb)
                    filtered_idx2hash[str(len(filtered_array) - 1)] = self.idx2hash[str(i)]
                else:
                    skipped_count += 1
                    hash_key = self.idx2hash[str(i)]
                    logger.warning(f"跳过维度不匹配的 embedding: {hash_key}, 维度={len(emb)}, 期望={expected_dim}")

            logger.warning(f"已过滤 {skipped_count} 个维度不匹配的 embedding")
            array = filtered_array
            self.idx2hash = filtered_idx2hash

            if not array:
                logger.error("过滤后没有可用的 embedding，无法构建索引")
                embedding_dim = expected_dim
                self.faiss_index = faiss.IndexFlatIP(embedding_dim)
                return

        embeddings = np.array(array, dtype=np.float32)
        # L2归一化
        faiss.normalize_L2(embeddings)
        # 构建索引
        embedding_dim = resolve_embedding_dimension(global_config.lpmm_knowledge.embedding_dimension)
        if not embedding_dim:
            # 🔧 修复：使用实际检测到的维度
            embedding_dim = embeddings.shape[1]
            logger.info(f"使用实际检测到的 embedding 维度: {embedding_dim}")
        self.faiss_index = faiss.IndexFlatIP(embedding_dim)
        self.faiss_index.add(embeddings)
        logger.info(f"✅ 成功构建 Faiss 索引: {len(embeddings)} 个向量, 维度={embedding_dim}")

    def search_top_k(self, query: list[float], k: int) -> list[tuple[str, float]]:
        """搜索最相似的k个项，以余弦相似度为度量
        Args:
            query: 查询的embedding
            k: 返回的最相似的k个项
        Returns:
            result: 最相似的k个项的(hash, 余弦相似度)列表
        """
        if self.faiss_index is None:
            logger.debug("FaissIndex尚未构建,返回None")
            return []
        if self.idx2hash is None:
            logger.warning("idx2hash尚未构建,返回None")
            return []

        # L2归一化
        faiss.normalize_L2(np.array([query], dtype=np.float32))
        # 搜索
        distances, indices = self.faiss_index.search(np.array([query]), k)
        # 整理结果
        indices = list(indices.flatten())
        distances = list(distances.flatten())
        result = [
            (self.idx2hash[str(int(idx))], float(sim))
            for (idx, sim) in zip(indices, distances, strict=False)
            if idx in range(len(self.idx2hash))
        ]

        return result


class EmbeddingManager:
    def __init__(self, max_workers: int = DEFAULT_MAX_WORKERS, chunk_size: int = DEFAULT_CHUNK_SIZE):
        """
        初始化EmbeddingManager

        Args:
            max_workers: 最大线程数
            chunk_size: 每个线程处理的数据块大小
        """
        self.paragraphs_embedding_store = EmbeddingStore(
            "paragraph",  # type: ignore
            EMBEDDING_DATA_DIR_STR,
            max_workers=max_workers,
            chunk_size=chunk_size,
        )
        self.entities_embedding_store = EmbeddingStore(
            "entity",  # type: ignore
            EMBEDDING_DATA_DIR_STR,
            max_workers=max_workers,
            chunk_size=chunk_size,
        )
        self.relation_embedding_store = EmbeddingStore(
            "relation",  # type: ignore
            EMBEDDING_DATA_DIR_STR,
            max_workers=max_workers,
            chunk_size=chunk_size,
        )
        self.stored_pg_hashes = set()

    async def check_all_embedding_model_consistency(self):
        """对所有嵌入库做模型一致性校验"""
        return await self.paragraphs_embedding_store.check_embedding_model_consistency()

    async def _store_pg_into_embedding(self, raw_paragraphs: dict[str, str]):
        """将段落编码存入Embedding库"""
        await self.paragraphs_embedding_store.batch_insert_strs(list(raw_paragraphs.values()), times=1)

    async def _store_ent_into_embedding(self, triple_list_data: dict[str, list[list[str]]]):
        """将实体编码存入Embedding库"""
        entities = set()
        for triple_list in triple_list_data.values():
            for triple in triple_list:
                entities.add(triple[0])
                entities.add(triple[2])
        await self.entities_embedding_store.batch_insert_strs(list(entities), times=2)

    async def _store_rel_into_embedding(self, triple_list_data: dict[str, list[list[str]]]):
        """将关系编码存入Embedding库"""
        graph_triples = []  # a list of unique relation triple (in tuple) from all chunks
        for triples in triple_list_data.values():
            graph_triples.extend([tuple(t) for t in triples])
        graph_triples = list(set(graph_triples))
        await self.relation_embedding_store.batch_insert_strs([str(triple) for triple in graph_triples], times=3)

    def load_from_file(self):
        """从文件加载"""
        self.paragraphs_embedding_store.load_from_file()
        self.entities_embedding_store.load_from_file()
        self.relation_embedding_store.load_from_file()
        # 从段落库中获取已存储的hash
        self.stored_pg_hashes = set(self.paragraphs_embedding_store.store.keys())

    async def store_new_data_set(
        self,
        raw_paragraphs: dict[str, str],
        triple_list_data: dict[str, list[list[str]]],
    ):
        if not await self.check_all_embedding_model_consistency():
            raise Exception("嵌入模型与本地存储不一致，请检查模型设置或清空嵌入库后重试。")
        """存储新的数据集"""
        await self._store_pg_into_embedding(raw_paragraphs)
        await self._store_ent_into_embedding(triple_list_data)
        await self._store_rel_into_embedding(triple_list_data)
        self.stored_pg_hashes.update(raw_paragraphs.keys())

    def save_to_file(self):
        """保存到文件"""
        self.paragraphs_embedding_store.save_to_file()
        self.entities_embedding_store.save_to_file()
        self.relation_embedding_store.save_to_file()

    def rebuild_faiss_index(self):
        """重建Faiss索引（请在添加新数据后调用）"""
        self.paragraphs_embedding_store.build_faiss_index()
        self.entities_embedding_store.build_faiss_index()
        self.relation_embedding_store.build_faiss_index()
