"""
消息集合向量存储系统
专用于存储和检索消息集合，以提供即时上下文。
"""

import time
from typing import Any

from src.chat.memory_system.memory_chunk import MessageCollection
from src.chat.utils.utils import get_embedding
from src.common.logger import get_logger
from src.common.vector_db import vector_db_service
from src.config.config import global_config

logger = get_logger(__name__)

class MessageCollectionStorage:
    """消息集合向量存储"""

    def __init__(self):
        self.config = global_config.memory
        self.vector_db_service = vector_db_service
        self.collection_name = "message_collections"
        self._initialize_storage()

    def _initialize_storage(self):
        """初始化存储"""
        try:
            self.vector_db_service.get_or_create_collection(
                name=self.collection_name,
                metadata={"description": "短期消息集合记忆", "hnsw:space": "cosine"},
            )
            logger.info(f"消息集合存储初始化完成，集合: '{self.collection_name}'")
        except Exception as e:
            logger.error(f"消息集合存储初始化失败: {e}", exc_info=True)
            raise

    async def add_collection(self, collection: MessageCollection):
        """添加一个新的消息集合，并处理容量和时间限制"""
        try:
            # 清理过期和超额的集合
            await self._cleanup_collections()

            # 向量化并存储
            embedding = await get_embedding(collection.combined_text)
            if not embedding:
                logger.warning(f"无法为消息集合 {collection.collection_id} 生成向量，跳过存储。")
                return

            collection.embedding = embedding

            self.vector_db_service.add(
                collection_name=self.collection_name,
                embeddings=[embedding],
                ids=[collection.collection_id],
                documents=[collection.combined_text],
                metadatas=[collection.to_dict()],
            )
            logger.debug(f"成功存储消息集合: {collection.collection_id}")

        except Exception as e:
            logger.error(f"存储消息集合失败: {e}", exc_info=True)

    async def _cleanup_collections(self):
        """清理超额和过期的消息集合"""
        try:
            # 基于时间清理
            if self.config.instant_memory_retention_hours > 0:
                expiration_time = time.time() - self.config.instant_memory_retention_hours * 3600
                expired_docs = self.vector_db_service.get(
                    collection_name=self.collection_name,
                    where={"created_at": {"$lt": expiration_time}},
                    include=[], # 只获取ID
                )
                if expired_docs and expired_docs.get("ids"):
                    self.vector_db_service.delete(collection_name=self.collection_name, ids=expired_docs["ids"])
                    logger.info(f"删除了 {len(expired_docs['ids'])} 个过期的瞬时记忆")

            # 基于数量清理
            current_count = self.vector_db_service.count(self.collection_name)
            if current_count > self.config.instant_memory_max_collections:
                num_to_delete = current_count - self.config.instant_memory_max_collections

                # 获取所有文档的元数据以进行排序
                all_docs = self.vector_db_service.get(
                    collection_name=self.collection_name,
                    include=["metadatas"]
                )

                if all_docs and all_docs.get("ids"):
                    # 在内存中排序找到最旧的文档
                    sorted_docs = sorted(
                        zip(all_docs["ids"], all_docs["metadatas"]),
                        key=lambda item: item[1].get("created_at", 0),
                    )

                    ids_to_delete = [doc[0] for doc in sorted_docs[:num_to_delete]]

                    if ids_to_delete:
                        self.vector_db_service.delete(collection_name=self.collection_name, ids=ids_to_delete)
                        logger.info(f"消息集合已满，删除最旧的 {len(ids_to_delete)} 个集合")

        except Exception as e:
            logger.error(f"清理消息集合失败: {e}", exc_info=True)


    async def get_relevant_collection(self, query_text: str, n_results: int = 1) -> list[MessageCollection]:
        """根据查询文本检索最相关的消息集合"""
        if not query_text.strip():
            return []

        try:
            query_embedding = await get_embedding(query_text)
            if not query_embedding:
                return []

            results = self.vector_db_service.query(
                collection_name=self.collection_name,
                query_embeddings=[query_embedding],
                n_results=n_results,
            )

            collections = []
            if results and results.get("ids") and results["ids"][0]:
                collections.extend(MessageCollection.from_dict(metadata) for metadata in results["metadatas"][0])

            return collections
        except Exception as e:
            logger.error(f"检索相关消息集合失败: {e}", exc_info=True)
            return []

    async def get_message_collection_context(self, query_text: str, chat_id: str) -> str:
        """获取消息集合上下文，用于添加到 prompt 中。优先展示当前聊天的上下文。"""
        try:
            collections = await self.get_relevant_collection(query_text, n_results=5)
            if not collections:
                return ""

            # 根据传入的 chat_id 对集合进行排序
            collections.sort(key=lambda c: c.chat_id == chat_id, reverse=True)

            context_parts = []
            for collection in collections:
                if not collection.combined_text:
                    continue

                header = "## 📝 相关对话上下文\n"
                if collection.chat_id == chat_id:
                    # 匹配的ID，使用更明显的标识
                    context_parts.append(
                        f"{header} [🔥 来自当前聊天的上下文]\n```\n{collection.combined_text}\n```"
                    )
                else:
                    # 不匹配的ID
                    context_parts.append(
                        f"{header} [💡 来自其他聊天的相关上下文 (ID: {collection.chat_id})]\n```\n{collection.combined_text}\n```"
                    )

            if not context_parts:
                return ""

            # 格式化消息集合为 prompt 上下文
            final_context = "\n\n---\n\n".join(context_parts) + "\n\n---"

            logger.info(f"🔗 为查询 '{query_text[:50]}...' 在聊天 '{chat_id}' 中找到 {len(collections)} 个相关消息集合上下文")
            return f"\n{final_context}\n"

        except Exception as e:
            logger.error(f"get_message_collection_context 失败: {e}")
            return ""

    def clear_all(self):
        """清空所有消息集合"""
        try:
            # In ChromaDB, the easiest way to clear a collection is to delete and recreate it.
            self.vector_db_service.delete_collection(name=self.collection_name)
            self._initialize_storage()
            logger.info(f"已清空所有消息集合: '{self.collection_name}'")
        except Exception as e:
            logger.error(f"清空消息集合失败: {e}", exc_info=True)

    def get_stats(self) -> dict[str, Any]:
        """获取存储统计信息"""
        try:
            count = self.vector_db_service.count(self.collection_name)
            return {
                "collection_name": self.collection_name,
                "total_collections": count,
                "storage_limit": self.config.instant_memory_max_collections,
            }
        except Exception as e:
            logger.error(f"获取消息集合存储统计失败: {e}")
            return {}
