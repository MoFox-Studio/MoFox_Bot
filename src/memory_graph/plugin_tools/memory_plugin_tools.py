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
    description = """记录对话中有价值的信息，构建长期记忆。

## 应该记录的内容类型：

### 高优先级记录（importance 0.7-1.0）
- 个人核心信息：姓名、年龄、职业、学历、联系方式
- 重要关系：家人、亲密朋友、恋人关系
- 核心目标：人生规划、职业目标、重要决定
- 关键事件：毕业、入职、搬家、重要成就

### 中等优先级（importance 0.5-0.7）
- 生活状态：工作内容、学习情况、日常习惯
- 兴趣偏好：喜欢/不喜欢的事物、消费偏好
- 观点态度：价值观、对事物的看法
- 技能知识：掌握的技能、专业领域
- 一般事件：日常活动、例行任务

### 低优先级（importance 0.3-0.5）
- 临时状态：今天心情、当前活动
- 一般评价：对产品/服务的简单评价
- 琐碎事件：买东西、看电影等常规活动

### ❌ 不应记录
- 单纯招呼语："你好"、"再见"、"谢谢"
- 无意义语气词："哦"、"嗯"、"好的"
- 纯粹回复确认：没有信息量的回应

## 记忆拆分原则
一句话多个信息点 → 多次调用创建多条记忆

示例："我最近在学Python，想找数据分析的工作"
→ 调用1：{{subject:"[从历史提取真实名字]", memory_type:"事实", topic:"学习", object:"Python", attributes:{{时间:"最近", 状态:"进行中"}}, importance:0.7}}
→ 调用2：{{subject:"[从历史提取真实名字]", memory_type:"目标", topic:"求职", object:"数据分析岗位", attributes:{{状态:"计划中"}}, importance:0.8}}"""
    
    parameters: ClassVar[list[tuple[str, ToolParamType, str, bool, list[str] | None]]] = [
        ("subject", ToolParamType.STRING, "记忆主体（重要！）。从对话历史中提取真实发送人名字。示例：如果看到'Prou(12345678): 我喜欢...'，subject应填'Prou'；如果看到'张三: 我在...'，subject应填'张三'。❌禁止使用'用户'这种泛指，必须用具体名字！", True, None),
        ("memory_type", ToolParamType.STRING, "记忆类型。【事件】=有明确时间点的动作（昨天吃饭、明天开会）【事实】=稳定状态（职业是程序员、住在北京）【观点】=主观看法（喜欢/讨厌/认为）【关系】=人际关系（朋友、同事）", True, ["事件", "事实", "关系", "观点"]),
        ("topic", ToolParamType.STRING, "记忆的核心内容（做什么/是什么状态/什么关系）。必须明确、具体，包含关键动词或状态词", True, None),
        ("object", ToolParamType.STRING, "记忆涉及的对象或目标。如果topic已经很完整可以不填，如果有明确对象建议填写", False, None),
        ("attributes", ToolParamType.STRING, "详细属性，JSON格式字符串。强烈建议包含：时间（具体到日期）、地点、状态、原因等上下文信息。例：{\"时间\":\"2025-11-06\",\"地点\":\"公司\",\"状态\":\"进行中\",\"原因\":\"项目需要\"}", False, None),
        ("importance", ToolParamType.FLOAT, "重要性评分 0.0-1.0。参考：日常琐事0.3-0.4，一般对话0.5-0.6，重要信息0.7-0.8，核心记忆0.9-1.0。不确定时用0.5", False, None),
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
                    "content": f"成功创建记忆（ID: {memory.id}）",
                    "memory_id": memory.id,  # 返回记忆ID供后续使用
                }
            else:
                return {
                    "name": self.name,
                    "content": "创建记忆失败",
                    "memory_id": None,
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
