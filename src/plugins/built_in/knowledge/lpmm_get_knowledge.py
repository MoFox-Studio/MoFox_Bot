from typing import Any, ClassVar

from src.chat.knowledge.knowledge_lib import qa_manager
from src.common.logger import get_logger
from src.config.config import global_config
from src.plugin_system import BaseTool, ToolParamType

logger = get_logger("lpmm_get_knowledge_tool")


class SearchKnowledgeFromLPMMTool(BaseTool):
    """从LPMM知识库中搜索相关信息的工具"""

    name = "lpmm_search_knowledge"
    description = "从知识库中搜索相关信息，如果你需要知识，就使用这个工具"
    parameters: ClassVar = [
        ("query", ToolParamType.STRING, "搜索查询关键词", True, None),
        ("threshold", ToolParamType.FLOAT, "相似度阈值，0.0到1.0之间", False, None),
    ]
    available_for_llm = global_config.lpmm_knowledge.enable

    async def execute(self, function_args: dict[str, Any]) -> dict[str, Any]:
        """执行知识库搜索

        Args:
            function_args: 工具参数

        Returns:
            Dict: 工具执行结果
        """
        try:
            query: str = function_args.get("query")  # type: ignore
            # threshold = function_args.get("threshold", 0.4)

            # 检查LPMM知识库是否启用
            if qa_manager is None:
                logger.debug("LPMM知识库已禁用，跳过知识获取")
                return {"type": "info", "id": query, "content": "LPMM知识库已禁用"}

            # 调用知识库搜索

            knowledge_info = await qa_manager.get_knowledge(query)

            logger.debug(f"知识库查询结果: {knowledge_info}")

            if knowledge_info and knowledge_info.get("knowledge_items"):
                knowledge_parts: ClassVar = []
                for i, item in enumerate(knowledge_info["knowledge_items"]):
                    knowledge_parts.append(f"- {item.get('content', 'N/A')}")

                knowledge_text = "\n".join(knowledge_parts)
                summary = knowledge_info.get("summary", "无总结")
                content = f"关于 '{query}', 你知道以下信息：\n{knowledge_text}\n\n总结: {summary}"
            else:
                content = f"关于 '{query}'，你的知识库里好像没有相关的信息呢"
            return {"type": "lpmm_knowledge", "id": query, "content": content}
        except Exception as e:
            # 捕获异常并记录错误
            logger.error(f"知识库搜索工具执行失败: {e!s}")
            # 在其他异常情况下，确保 id 仍然是 query (如果它被定义了)
            query_id = query if "query" in locals() else "unknown_query"
            return {"type": "info", "id": query_id, "content": f"lpmm知识库搜索失败，炸了: {e!s}"}
