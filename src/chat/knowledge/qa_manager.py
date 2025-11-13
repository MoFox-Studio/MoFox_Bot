import time
from typing import Any

from src.chat.utils.utils import get_embedding
from src.config.config import global_config, model_config
from src.llm_models.utils_model import LLMRequest

from .embedding_store import EmbeddingManager
from .global_logger import logger
from .kg_manager import KGManager

# from .lpmmconfig import global_config
from .utils.dyn_topk import dyn_select_top_k

MAX_KNOWLEDGE_LENGTH = 10000  # 最大知识长度


class QAManager:
    def __init__(
        self,
        embed_manager: EmbeddingManager,
        kg_manager: KGManager,
    ):
        self.embed_manager = embed_manager
        self.kg_manager = kg_manager
        self.qa_model = LLMRequest(model_set=model_config.model_task_config.lpmm_qa, request_type="lpmm.qa")

    async def process_query(
        self, question: str
    ) -> tuple[list[tuple[str, float, float]], dict[str, float] | None] | None:
        """处理查询"""

        # 生成问题的Embedding
        part_start_time = time.perf_counter()
        question_embedding = await get_embedding(question)
        if question_embedding is None:
            logger.error("生成问题Embedding失败")
            return None
        part_end_time = time.perf_counter()
        logger.debug(f"Embedding用时：{part_end_time - part_start_time:.5f}s")

        # 根据问题Embedding查询Relation Embedding库
        part_start_time = time.perf_counter()
        relation_search_res = self.embed_manager.relation_embedding_store.search_top_k(
            question_embedding,
            global_config.lpmm_knowledge.qa_relation_search_top_k,
        )
        if relation_search_res is None:
            return None
        # 过滤阈值
        # 考虑动态阈值：当存在显著数值差异的结果时，保留显著结果；否则，保留所有结果
        relation_search_res = dyn_select_top_k(relation_search_res, 0.5, 1.0)
        if not relation_search_res or relation_search_res[0][1] < global_config.lpmm_knowledge.qa_relation_threshold:
            # 未找到相关关系
            logger.debug("未找到相关关系，跳过关系检索")
            relation_search_res = []

        part_end_time = time.perf_counter()
        logger.debug(f"关系检索用时：{part_end_time - part_start_time:.5f}s")

        for res in relation_search_res:
            if store_item := self.embed_manager.relation_embedding_store.store.get(res[0]):
                rel_str = store_item.str
            print(f"找到相关关系，相似度：{(res[1] * 100):.2f}%  -  {rel_str}")

        # TODO: 使用LLM过滤三元组结果
        # logger.info(f"LLM过滤三元组用时：{time.time() - part_start_time:.2f}s")
        # part_start_time = time.time()

        # 根据问题Embedding查询Paragraph Embedding库
        part_start_time = time.perf_counter()
        paragraph_search_res = self.embed_manager.paragraphs_embedding_store.search_top_k(
            question_embedding,
            global_config.lpmm_knowledge.qa_paragraph_search_top_k,
        )
        part_end_time = time.perf_counter()
        logger.debug(f"文段检索用时：{part_end_time - part_start_time:.5f}s")

        if len(relation_search_res) != 0:
            logger.info("找到相关关系，将使用RAG进行检索")
            # 使用KG检索
            part_start_time = time.perf_counter()
            result, ppr_node_weights = self.kg_manager.kg_search(
                relation_search_res, paragraph_search_res, self.embed_manager
            )
            part_end_time = time.perf_counter()
            logger.info(f"RAG检索用时：{part_end_time - part_start_time:.5f}s")
        else:
            logger.info("未找到相关关系，将使用文段检索结果")
            result = paragraph_search_res
            if result and result[0][1] < global_config.lpmm_knowledge.qa_paragraph_threshold:
                result = []
            ppr_node_weights = None

        # 过滤阈值
        result = dyn_select_top_k(result, 0.5, 1.0)

        for res in result:
            raw_paragraph = self.embed_manager.paragraphs_embedding_store.store[res[0]].str
            print(f"找到相关文段，相关系数：{res[1]:.8f}\n{raw_paragraph}\n\n")

        return result, ppr_node_weights

    async def get_knowledge(self, question: str) -> dict[str, Any] | None:
        """
        获取知识，返回结构化字典

        Args:
            question: 用户提出的问题

        Returns:
            一个包含 'knowledge_items' 和 'summary' 的字典，或者在没有结果时返回 None
        """
        processed_result = await self.process_query(question)
        if not processed_result or not processed_result[0]:
            logger.debug("知识库查询结果为空。")
            return None

        query_res = processed_result[0]

        knowledge_items = []
        for res_hash, relevance, *_ in query_res:
            if store_item := self.embed_manager.paragraphs_embedding_store.store.get(res_hash):
                knowledge_items.append(
                    {"content": store_item.str, "source": "内部知识库", "relevance": f"{relevance:.4f}"}
                )

        if not knowledge_items:
            return None

        # 使用LLM生成总结
        knowledge_text_for_summary = "\n\n".join([item["content"] for item in knowledge_items[:5]])  # 最多总结前5条
        summary_prompt = (
            f"根据以下信息，为问题 '{question}' 生成一个简洁的、不超过50字的摘要：\n\n{knowledge_text_for_summary}"
        )

        try:
            summary, (_, _, _) = await self.qa_model.generate_response_async(summary_prompt)
        except Exception as e:
            logger.error(f"生成知识摘要失败: {e}")
            summary = "无法生成摘要。"

        return {"knowledge_items": knowledge_items, "summary": summary.strip() if summary else "没有可用的摘要。"}
