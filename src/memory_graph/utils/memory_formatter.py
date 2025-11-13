"""
记忆格式化工具

用于将记忆图系统的Memory对象转换为适合提示词的自然语言描述
"""

import logging
from datetime import datetime

from src.memory_graph.models import EdgeType, Memory, MemoryType, NodeType

logger = logging.getLogger(__name__)


def format_memory_for_prompt(memory: Memory, include_metadata: bool = False) -> str:
    """
    将记忆对象格式化为适合提示词的自然语言描述

    根据记忆的图结构，构建完整的主谓宾描述，包含：
    - 主语（subject node）
    - 谓语/动作（topic node）
    - 宾语/对象（object node，如果存在）
    - 属性信息（attributes，如时间、地点等）
    - 关系信息（记忆之间的关系）

    Args:
        memory: 记忆对象
        include_metadata: 是否包含元数据（时间、重要性等）

    Returns:
        格式化后的自然语言描述
    """
    try:
        # 1. 获取主体节点（主语）
        subject_node = memory.get_subject_node()
        if not subject_node:
            logger.warning(f"记忆 {memory.id} 缺少主体节点")
            return "（记忆格式错误：缺少主体）"

        subject_text = subject_node.content

        # 2. 查找主题节点（谓语/动作）
        topic_node = None
        for edge in memory.edges:
            if edge.edge_type == EdgeType.MEMORY_TYPE and edge.source_id == memory.subject_id:
                topic_node = memory.get_node_by_id(edge.target_id)
                break

        if not topic_node:
            logger.warning(f"记忆 {memory.id} 缺少主题节点")
            return f"{subject_text}（记忆格式错误：缺少主题）"

        topic_text = topic_node.content

        # 3. 查找客体节点（宾语）和核心关系
        object_node = None
        core_relation = None
        for edge in memory.edges:
            if edge.edge_type == EdgeType.CORE_RELATION and edge.source_id == topic_node.id:
                object_node = memory.get_node_by_id(edge.target_id)
                core_relation = edge.relation if edge.relation else ""
                break

        # 4. 收集属性节点
        attributes: dict[str, str] = {}
        for edge in memory.edges:
            if edge.edge_type == EdgeType.ATTRIBUTE:
                # 查找属性节点和值节点
                attr_node = memory.get_node_by_id(edge.target_id)
                if attr_node and attr_node.node_type == NodeType.ATTRIBUTE:
                    # 查找这个属性的值
                    for value_edge in memory.edges:
                        if (value_edge.edge_type == EdgeType.ATTRIBUTE
                            and value_edge.source_id == attr_node.id):
                            value_node = memory.get_node_by_id(value_edge.target_id)
                            if value_node and value_node.node_type == NodeType.VALUE:
                                attributes[attr_node.content] = value_node.content
                                break

        # 5. 构建自然语言描述
        parts = []

        # 主谓宾结构
        if object_node is not None:
            # 有完整的主谓宾
            if core_relation:
                parts.append(f"{subject_text}-{topic_text}{core_relation}{object_node.content}")
            else:
                parts.append(f"{subject_text}-{topic_text}{object_node.content}")
        else:
            # 只有主谓
            parts.append(f"{subject_text}-{topic_text}")

        # 添加属性信息
        if attributes:
            attr_parts = []
            # 优先显示时间和地点
            if "时间" in attributes:
                attr_parts.append(f"于{attributes['时间']}")
            if "地点" in attributes:
                attr_parts.append(f"在{attributes['地点']}")
            # 其他属性
            for key, value in attributes.items():
                if key not in ["时间", "地点"]:
                    attr_parts.append(f"{key}：{value}")

            if attr_parts:
                parts.append(f"（{' '.join(attr_parts)}）")

        description = "".join(parts)

        # 6. 添加元数据（可选）
        if include_metadata:
            metadata_parts = []

            # 记忆类型
            if memory.memory_type:
                metadata_parts.append(f"类型：{memory.memory_type.value}")

            # 重要性
            if memory.importance >= 0.8:
                metadata_parts.append("重要")
            elif memory.importance >= 0.6:
                metadata_parts.append("一般")

            # 时间（如果没有在属性中）
            if "时间" not in attributes:
                time_str = _format_relative_time(memory.created_at)
                if time_str:
                    metadata_parts.append(time_str)

            if metadata_parts:
                description += f" [{', '.join(metadata_parts)}]"

        return description

    except Exception as e:
        logger.error(f"格式化记忆失败: {e}", exc_info=True)
        return f"（记忆格式化错误: {str(e)[:50]}）"


def format_memories_for_prompt(
    memories: list[Memory],
    max_count: int | None = None,
    include_metadata: bool = False,
    group_by_type: bool = False
) -> str:
    """
    批量格式化多条记忆为提示词文本

    Args:
        memories: 记忆列表
        max_count: 最大记忆数量（可选）
        include_metadata: 是否包含元数据
        group_by_type: 是否按类型分组

    Returns:
        格式化后的文本，包含标题和列表
    """
    if not memories:
        return ""

    # 限制数量
    if max_count:
        memories = memories[:max_count]

    # 按类型分组
    if group_by_type:
        type_groups: dict[MemoryType, list[Memory]] = {}
        for memory in memories:
            if memory.memory_type not in type_groups:
                type_groups[memory.memory_type] = []
            type_groups[memory.memory_type].append(memory)

        # 构建分组文本
        parts = ["### 🧠 相关记忆 (Relevant Memories)", ""]

        type_order = [MemoryType.FACT, MemoryType.EVENT, MemoryType.RELATION, MemoryType.OPINION]
        for mem_type in type_order:
            if mem_type in type_groups:
                parts.append(f"#### {mem_type.value}")
                for memory in type_groups[mem_type]:
                    desc = format_memory_for_prompt(memory, include_metadata)
                    parts.append(f"- {desc}")
                parts.append("")

        return "\n".join(parts)

    else:
        # 不分组，直接列出
        parts = ["### 🧠 相关记忆 (Relevant Memories)", ""]

        for memory in memories:
            # 获取类型标签
            type_label = memory.memory_type.value if memory.memory_type else "未知"

            # 格式化记忆内容
            desc = format_memory_for_prompt(memory, include_metadata)

            # 添加类型标签
            parts.append(f"- **[{type_label}]** {desc}")

        return "\n".join(parts)


def get_memory_type_label(memory_type: str) -> str:
    """
    获取记忆类型的中文标签

    Args:
        memory_type: 记忆类型（可能是英文或中文）

    Returns:
        中文标签
    """
    # 映射表
    type_mapping = {
        # 英文到中文
        "event": "事件",
        "fact": "事实",
        "relation": "关系",
        "opinion": "观点",
        "preference": "偏好",
        "emotion": "情绪",
        "knowledge": "知识",
        "skill": "技能",
        "goal": "目标",
        "experience": "经历",
        "contextual": "情境",
        # 中文（保持不变）
        "事件": "事件",
        "事实": "事实",
        "关系": "关系",
        "观点": "观点",
        "偏好": "偏好",
        "情绪": "情绪",
        "知识": "知识",
        "技能": "技能",
        "目标": "目标",
        "经历": "经历",
        "情境": "情境",
    }

    # 转换为小写进行匹配
    memory_type_lower = memory_type.lower() if memory_type else ""

    return type_mapping.get(memory_type_lower, "未知")


def _format_relative_time(timestamp: datetime) -> str | None:
    """
    格式化相对时间（如"2天前"、"刚才"）

    Args:
        timestamp: 时间戳

    Returns:
        相对时间描述，如果太久远则返回None
    """
    try:
        now = datetime.now()
        delta = now - timestamp

        if delta.total_seconds() < 60:
            return "刚才"
        elif delta.total_seconds() < 3600:
            minutes = int(delta.total_seconds() / 60)
            return f"{minutes}分钟前"
        elif delta.total_seconds() < 86400:
            hours = int(delta.total_seconds() / 3600)
            return f"{hours}小时前"
        elif delta.days < 7:
            return f"{delta.days}天前"
        elif delta.days < 30:
            weeks = delta.days // 7
            return f"{weeks}周前"
        elif delta.days < 365:
            months = delta.days // 30
            return f"{months}个月前"
        else:
            # 超过一年不显示相对时间
            return None
    except Exception:
        return None


def format_memory_summary(memory: Memory) -> str:
    """
    生成记忆的简短摘要（用于日志和调试）

    Args:
        memory: 记忆对象

    Returns:
        简短摘要
    """
    try:
        subject_node = memory.get_subject_node()
        subject_text = subject_node.content if subject_node else "?"

        topic_text = "?"
        for edge in memory.edges:
            if edge.edge_type == EdgeType.MEMORY_TYPE and edge.source_id == memory.subject_id:
                topic_node = memory.get_node_by_id(edge.target_id)
                if topic_node:
                    topic_text = topic_node.content
                    break

        return f"{subject_text} - {memory.memory_type.value if memory.memory_type else '?'}: {topic_text}"
    except Exception:
        return f"记忆 {memory.id[:8]}"


# 导出主要函数
__all__ = [
    "format_memories_for_prompt",
    "format_memory_for_prompt",
    "format_memory_summary",
    "get_memory_type_label",
]
