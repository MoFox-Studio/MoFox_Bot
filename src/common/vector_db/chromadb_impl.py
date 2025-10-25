import threading
from typing import Any

import chromadb
from chromadb.config import Settings

from src.common.logger import get_logger

from .base import VectorDBBase

logger = get_logger("chromadb_impl")


class ChromaDBImpl(VectorDBBase):
    """
    ChromaDB 的具体实现，遵循 VectorDBBase 接口。
    采用单例模式，确保全局只有一个 ChromaDB 客户端实例。
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            with cls._lock:
                if not cls._instance:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, path: str = "data/chroma_db", **kwargs: Any):
        """
        初始化 ChromaDB 客户端。
        由于是单例，这个初始化只会执行一次。
        """
        if not hasattr(self, "_initialized"):
            with self._lock:
                if not hasattr(self, "_initialized"):
                    try:
                        self.client = chromadb.PersistentClient(
                            path=path, settings=Settings(anonymized_telemetry=False)
                        )
                        self._collections: dict[str, Any] = {}
                        self._initialized = True
                        logger.info(f"ChromaDB 客户端已初始化，数据库路径: {path}")
                    except Exception as e:
                        logger.error(f"ChromaDB 初始化失败: {e}")
                        self.client = None
                        self._initialized = False
                        raise ConnectionError(f"ChromaDB 初始化失败: {e}") from e

    def get_or_create_collection(self, name: str, **kwargs: Any) -> Any:
        if not self.client:
            raise ConnectionError("ChromaDB 客户端未初始化")

        if name in self._collections:
            return self._collections[name]

        try:
            collection = self.client.get_or_create_collection(name=name, **kwargs)
            self._collections[name] = collection
            logger.info(f"成功获取或创建集合: '{name}'")
            return collection
        except Exception as e:
            logger.error(f"获取或创建集合 '{name}' 失败: {e}")
            return None

    def add(
        self,
        collection_name: str,
        embeddings: list[list[float]],
        documents: list[str] | None = None,
        metadatas: list[dict[str, Any]] | None = None,
        ids: list[str] | None = None,
    ) -> None:
        collection = self.get_or_create_collection(collection_name)
        if collection:
            try:
                collection.add(
                    embeddings=embeddings,
                    documents=documents,
                    metadatas=metadatas,
                    ids=ids,
                )
            except Exception as e:
                logger.error(f"向集合 '{collection_name}' 添加数据失败: {e}")

    def query(
        self,
        collection_name: str,
        query_embeddings: list[list[float]],
        n_results: int = 1,
        where: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, list[Any]]:
        collection = self.get_or_create_collection(collection_name)
        if collection:
            try:
                query_params = {
                    "query_embeddings": query_embeddings,
                    "n_results": n_results,
                    **kwargs,
                }

                # 修复ChromaDB的where条件格式
                if where:
                    processed_where = self._process_where_condition(where)
                    if processed_where:
                        query_params["where"] = processed_where

                return collection.query(**query_params)
            except Exception as e:
                logger.error(f"查询集合 '{collection_name}' 失败: {e}")
                # 如果查询失败，尝试不使用where条件重新查询
                try:
                    fallback_params = {
                        "query_embeddings": query_embeddings,
                        "n_results": n_results,
                    }
                    logger.warning("使用回退查询模式（无where条件）")
                    return collection.query(**fallback_params)
                except Exception as fallback_e:
                    logger.error(f"回退查询也失败: {fallback_e}")
        return {}

    def _process_where_condition(self, where: dict[str, Any]) -> dict[str, Any] | None:
        """
        处理where条件，转换为ChromaDB支持的格式
        ChromaDB支持的格式：
        - 简单条件: {"field": "value"}
        - 操作符条件: {"field": {"$op": "value"}}
        - AND条件: {"$and": [condition1, condition2]}
        - OR条件: {"$or": [condition1, condition2]}
        """
        if not where:
            return None

        try:
            # 如果只有一个字段，直接返回
            if len(where) == 1:
                key, value = next(iter(where.items()))

                # 处理列表值（如memory_types）
                if isinstance(value, list):
                    if len(value) == 1:
                        return {key: value[0]}
                    else:
                        # 多个值使用 $in 操作符
                        return {key: {"$in": value}}
                else:
                    return {key: value}

            # 多个字段使用 $and 操作符
            conditions = []
            for key, value in where.items():
                if isinstance(value, list):
                    if len(value) == 1:
                        conditions.append({key: value[0]})
                    else:
                        conditions.append({key: {"$in": value}})
                else:
                    conditions.append({key: value})

            return {"$and": conditions}

        except Exception as e:
            logger.warning(f"处理where条件失败: {e}, 使用简化条件")
            # 回退到只使用第一个条件
            if where:
                key, value = next(iter(where.items()))
                if isinstance(value, list) and value:
                    return {key: value[0]}
                elif not isinstance(value, list):
                    return {key: value}
            return None

    def get(
        self,
        collection_name: str,
        ids: list[str] | None = None,
        where: dict[str, Any] | None = None,
        limit: int | None = None,
        offset: int | None = None,
        where_document: dict[str, Any] | None = None,
        include: list[str] | None = None,
    ) -> dict[str, Any]:
        """根据条件从集合中获取数据"""
        collection = self.get_or_create_collection(collection_name)
        if collection:
            try:
                # 处理where条件
                processed_where = None
                if where:
                    processed_where = self._process_where_condition(where)

                return collection.get(
                    ids=ids,
                    where=processed_where,
                    limit=limit,
                    offset=offset,
                    where_document=where_document,
                    include=include or ["documents", "metadatas", "embeddings"],
                )
            except Exception as e:
                logger.error(f"从集合 '{collection_name}' 获取数据失败: {e}")
                # 如果获取失败，尝试不使用where条件重新获取
                try:
                    logger.warning("使用回退获取模式（无where条件）")
                    return collection.get(
                        ids=ids,
                        limit=limit,
                        offset=offset,
                        where_document=where_document,
                        include=include or ["documents", "metadatas", "embeddings"],
                    )
                except Exception as fallback_e:
                    logger.error(f"回退获取也失败: {fallback_e}")
        return {}

    def delete(
        self,
        collection_name: str,
        ids: list[str] | None = None,
        where: dict[str, Any] | None = None,
    ) -> None:
        collection = self.get_or_create_collection(collection_name)
        if collection:
            try:
                collection.delete(ids=ids, where=where)
            except Exception as e:
                logger.error(f"从集合 '{collection_name}' 删除数据失败: {e}")

    def count(self, collection_name: str) -> int:
        collection = self.get_or_create_collection(collection_name)
        if collection:
            try:
                return collection.count()
            except Exception as e:
                logger.error(f"获取集合 '{collection_name}' 计数失败: {e}")
        return 0

    def delete_collection(self, name: str) -> None:
        if not self.client:
            raise ConnectionError("ChromaDB 客户端未初始化")

        try:
            self.client.delete_collection(name=name)
            if name in self._collections:
                del self._collections[name]
            logger.info(f"集合 '{name}' 已被删除")
        except Exception as e:
            logger.error(f"删除集合 '{name}' 失败: {e}")
