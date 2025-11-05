"""
记忆系统插件工具

将 MemoryTools 适配为 BaseTool 格式，供 LLM 使用
"""

from __future__ import annotations

from typing import Any, ClassVar

from src.common.logger import get_logger
from src.plugin_system.base.base_tool import BaseTool
from src.plugin_system.base.component_types import ToolParamType

logger = get_logger(__name__)


class CreateMemoryTool(BaseTool):
    """创建记忆工具"""

    name = "create_memory"
    description = "创建一个新的记忆。记忆由主体、类型、主题、客体（可选）和属性组成。用于记录重要的信息、事件、想法等。"
    
    parameters: ClassVar[list[tuple[str, ToolParamType, str, bool, list[str] | None]]] = [
        ("subject", ToolParamType.STRING, "记忆的主体，通常是'我'、'用户'或具体的人名", True, None),
        ("memory_type", ToolParamType.STRING, "记忆类型", True, ["事件", "事实", "关系", "观点"]),
        ("topic", ToolParamType.STRING, "记忆的主题，即发生的事情或状态", True, None),
        ("object", ToolParamType.STRING, "记忆的客体，即主题作用的对象（可选）", False, None),
        ("attributes", ToolParamType.STRING, "记忆的属性（JSON格式字符串），如 {\"时间\":\"今天\",\"地点\":\"家里\"}", False, None),
        ("importance", ToolParamType.FLOAT, "记忆的重要性（0.0-1.0），默认0.5", False, None),
    ]
    
    available_for_llm = True

    async def execute(self, function_args: dict[str, Any]) -> dict[str, Any]:
        """执行创建记忆"""
        try:
            # 获取全局 memory_manager
            from src.memory_graph.manager_singleton import get_memory_manager
            
            manager = get_memory_manager()
            if not manager:
                return {
                    "name": self.name,
                    "content": "记忆系统未初始化"
                }
            
            # 提取参数
            subject = function_args.get("subject", "")
            memory_type = function_args.get("memory_type", "")
            topic = function_args.get("topic", "")
            obj = function_args.get("object")
            
            # 处理 attributes（可能是字符串或字典）
            attributes_raw = function_args.get("attributes", {})
            if isinstance(attributes_raw, str):
                import orjson
                try:
                    attributes = orjson.loads(attributes_raw)
                except Exception:
                    attributes = {}
            else:
                attributes = attributes_raw
            
            importance = function_args.get("importance", 0.5)
            
            # 创建记忆
            memory = await manager.create_memory(
                subject=subject,
                memory_type=memory_type,
                topic=topic,
                object_=obj,
                attributes=attributes,
                importance=importance,
            )
            
            if memory:
                logger.info(f"[CreateMemoryTool] 成功创建记忆: {memory.id}")
                return {
                    "name": self.name,
                    "content": f"成功创建记忆（ID: {memory.id}）"
                }
            else:
                return {
                    "name": self.name,
                    "content": "创建记忆失败"
                }
                
        except Exception as e:
            logger.error(f"[CreateMemoryTool] 执行失败: {e}", exc_info=True)
            return {
                "name": self.name,
                "content": f"创建记忆时出错: {str(e)}"
            }


class LinkMemoriesTool(BaseTool):
    """关联记忆工具"""

    name = "link_memories"
    description = "在两个记忆之间建立关联关系。用于连接相关的记忆，形成知识网络。"
    
    parameters: ClassVar[list[tuple[str, ToolParamType, str, bool, list[str] | None]]] = [
        ("source_query", ToolParamType.STRING, "源记忆的搜索查询（如记忆的主题关键词）", True, None),
        ("target_query", ToolParamType.STRING, "目标记忆的搜索查询", True, None),
        ("relation", ToolParamType.STRING, "关系类型", True, ["导致", "引用", "相似", "相反", "部分"]),
        ("strength", ToolParamType.FLOAT, "关系强度（0.0-1.0），默认0.7", False, None),
    ]
    
    available_for_llm = False  # 暂不对 LLM 开放

    async def execute(self, function_args: dict[str, Any]) -> dict[str, Any]:
        """执行关联记忆"""
        try:
            from src.memory_graph.manager_singleton import get_memory_manager
            
            manager = get_memory_manager()
            if not manager:
                return {
                    "name": self.name,
                    "content": "记忆系统未初始化"
                }
            
            source_query = function_args.get("source_query", "")
            target_query = function_args.get("target_query", "")
            relation = function_args.get("relation", "引用")
            strength = function_args.get("strength", 0.7)
            
            # 关联记忆
            success = await manager.link_memories(
                source_description=source_query,
                target_description=target_query,
                relation_type=relation,
                importance=strength,
            )
            
            if success:
                logger.info(f"[LinkMemoriesTool] 成功关联记忆: {source_query} -> {target_query}")
                return {
                    "name": self.name,
                    "content": f"成功建立关联: {source_query} --{relation}--> {target_query}"
                }
            else:
                return {
                    "name": self.name,
                    "content": "关联记忆失败，可能找不到匹配的记忆"
                }
                
        except Exception as e:
            logger.error(f"[LinkMemoriesTool] 执行失败: {e}", exc_info=True)
            return {
                "name": self.name,
                "content": f"关联记忆时出错: {str(e)}"
            }


class SearchMemoriesTool(BaseTool):
    """搜索记忆工具"""

    name = "search_memories"
    description = "搜索相关的记忆。根据查询词搜索记忆库，返回最相关的记忆。"
    
    parameters: ClassVar[list[tuple[str, ToolParamType, str, bool, list[str] | None]]] = [
        ("query", ToolParamType.STRING, "搜索查询词，描述想要找什么样的记忆", True, None),
        ("top_k", ToolParamType.INTEGER, "返回的记忆数量，默认5", False, None),
        ("min_importance", ToolParamType.FLOAT, "最低重要性阈值（0.0-1.0），只返回重要性不低于此值的记忆", False, None),
    ]
    
    available_for_llm = False  # 暂不对 LLM 开放，记忆检索在提示词构建时自动执行

    async def execute(self, function_args: dict[str, Any]) -> dict[str, Any]:
        """执行搜索记忆"""
        try:
            from src.memory_graph.manager_singleton import get_memory_manager
            
            manager = get_memory_manager()
            if not manager:
                return {
                    "name": self.name,
                    "content": "记忆系统未初始化"
                }
            
            query = function_args.get("query", "")
            top_k = function_args.get("top_k", 5)
            min_importance_raw = function_args.get("min_importance")
            min_importance = float(min_importance_raw) if min_importance_raw is not None else 0.0
            
            # 搜索记忆
            memories = await manager.search_memories(
                query=query,
                top_k=top_k,
                min_importance=min_importance,
            )
            
            if memories:
                # 格式化结果
                result_lines = [f"找到 {len(memories)} 条相关记忆：\n"]
                for i, mem in enumerate(memories, 1):
                    topic = mem.metadata.get("topic", "N/A")
                    mem_type = mem.metadata.get("memory_type", "N/A")
                    importance = mem.importance
                    result_lines.append(
                        f"{i}. [{mem_type}] {topic} (重要性: {importance:.2f})"
                    )
                
                result_text = "\n".join(result_lines)
                logger.info(f"[SearchMemoriesTool] 搜索成功: 查询='{query}', 结果数={len(memories)}")
                
                return {
                    "name": self.name,
                    "content": result_text
                }
            else:
                return {
                    "name": self.name,
                    "content": f"未找到与 '{query}' 相关的记忆"
                }
                
        except Exception as e:
            logger.error(f"[SearchMemoriesTool] 执行失败: {e}", exc_info=True)
            return {
                "name": self.name,
                "content": f"搜索记忆时出错: {str(e)}"
            }
