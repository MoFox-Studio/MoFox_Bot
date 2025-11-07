"""
记忆提取器：从工具参数中提取和验证记忆元素
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.common.logger import get_logger
from src.memory_graph.models import MemoryType
from src.memory_graph.utils.time_parser import TimeParser

logger = get_logger(__name__)


class MemoryExtractor:
    """
    记忆提取器

    负责：
    1. 从工具调用参数中提取记忆元素
    2. 验证参数完整性和有效性
    3. 标准化时间表达
    4. 清洗和格式化数据
    """

    def __init__(self, time_parser: TimeParser | None = None):
        """
        初始化记忆提取器

        Args:
            time_parser: 时间解析器（可选）
        """
        self.time_parser = time_parser or TimeParser()

    def extract_from_tool_params(self, params: dict[str, Any]) -> dict[str, Any]:
        """
        从工具参数中提取记忆元素

        Args:
            params: 工具调用参数，例如：
                {
                    "subject": "我",
                    "memory_type": "事件",
                    "topic": "吃饭",
                    "object": "白米饭",
                    "attributes": {"时间": "今天", "地点": "家里"},
                    "importance": 0.3
                }

        Returns:
            提取和标准化后的参数字典
        """
        try:
            # 1. 验证必需参数
            self._validate_required_params(params)

            # 2. 提取基础元素
            extracted = {
                "subject": self._clean_text(params["subject"]),
                "memory_type": self._parse_memory_type(params["memory_type"]),
                "topic": self._clean_text(params["topic"]),
            }

            # 3. 提取可选的客体
            if params.get("object"):
                extracted["object"] = self._clean_text(params["object"])

            # 4. 提取和标准化属性
            if params.get("attributes"):
                extracted["attributes"] = self._process_attributes(params["attributes"])
            else:
                extracted["attributes"] = {}

            # 5. 提取重要性
            extracted["importance"] = self._parse_importance(params.get("importance", 0.5))

            # 6. 添加时间戳
            extracted["timestamp"] = datetime.now()

            logger.debug(f"提取记忆元素: {extracted['subject']} - {extracted['topic']}")
            return extracted

        except Exception as e:
            logger.error(f"记忆提取失败: {e}", exc_info=True)
            raise ValueError(f"记忆提取失败: {e}")

    def _validate_required_params(self, params: dict[str, Any]) -> None:
        """
        验证必需参数

        Args:
            params: 参数字典

        Raises:
            ValueError: 如果缺少必需参数
        """
        required_fields = ["subject", "memory_type", "topic"]

        for field in required_fields:
            if field not in params or not params[field]:
                raise ValueError(f"缺少必需参数: {field}")

    def _clean_text(self, text: Any) -> str:
        """
        清洗文本

        Args:
            text: 输入文本

        Returns:
            清洗后的文本
        """
        if not text:
            return ""

        text = str(text).strip()

        # 移除多余的空格
        text = " ".join(text.split())

        # 移除特殊字符（保留基本标点）
        # text = re.sub(r'[^\w\s\u4e00-\u9fff,，.。!！?？;；:：、]', '', text)

        return text

    def _parse_memory_type(self, type_str: str) -> MemoryType:
        """
        解析记忆类型

        Args:
            type_str: 类型字符串

        Returns:
            MemoryType 枚举

        Raises:
            ValueError: 如果类型无效
        """
        type_str = type_str.strip()

        # 尝试直接匹配
        try:
            return MemoryType(type_str)
        except ValueError:
            pass

        # 模糊匹配
        type_mapping = {
            "事件": MemoryType.EVENT,
            "event": MemoryType.EVENT,
            "事实": MemoryType.FACT,
            "fact": MemoryType.FACT,
            "关系": MemoryType.RELATION,
            "relation": MemoryType.RELATION,
            "观点": MemoryType.OPINION,
            "opinion": MemoryType.OPINION,
        }

        if type_str.lower() in type_mapping:
            return type_mapping[type_str.lower()]

        raise ValueError(f"无效的记忆类型: {type_str}")

    def _parse_importance(self, importance: Any) -> float:
        """
        解析重要性值

        Args:
            importance: 重要性值（可以是数字、字符串等）

        Returns:
            0-1之间的浮点数
        """
        try:
            value = float(importance)
            # 限制在 0-1 范围内
            return max(0.0, min(1.0, value))
        except (ValueError, TypeError):
            logger.warning(f"无效的重要性值: {importance}，使用默认值 0.5")
            return 0.5

    def _process_attributes(self, attributes: dict[str, Any]) -> dict[str, Any]:
        """
        处理属性字典

        Args:
            attributes: 原始属性字典

        Returns:
            处理后的属性字典
        """
        processed = {}

        for key, value in attributes.items():
            key = key.strip()

            # 特殊处理：时间属性
            if key in ["时间", "time", "when"]:
                parsed_time = self.time_parser.parse(str(value))
                if parsed_time:
                    processed["时间"] = parsed_time.isoformat()
                else:
                    processed["时间"] = str(value)

            # 特殊处理：地点属性
            elif key in ["地点", "place", "where", "位置"]:
                processed["地点"] = self._clean_text(value)

            # 特殊处理：原因属性
            elif key in ["原因", "reason", "why", "因为"]:
                processed["原因"] = self._clean_text(value)

            # 特殊处理：方式属性
            elif key in ["方式", "how", "manner"]:
                processed["方式"] = self._clean_text(value)

            # 其他属性
            else:
                processed[key] = self._clean_text(value)

        return processed

    def extract_link_params(self, params: dict[str, Any]) -> dict[str, Any]:
        """
        提取记忆关联参数（用于 link_memories 工具）

        Args:
            params: 工具参数，例如：
                {
                    "source_memory_description": "我今天不开心",
                    "target_memory_description": "我摔东西",
                    "relation_type": "导致",
                    "importance": 0.6
                }

        Returns:
            提取后的参数
        """
        try:
            required = ["source_memory_description", "target_memory_description", "relation_type"]

            for field in required:
                if field not in params or not params[field]:
                    raise ValueError(f"缺少必需参数: {field}")

            extracted = {
                "source_description": self._clean_text(params["source_memory_description"]),
                "target_description": self._clean_text(params["target_memory_description"]),
                "relation_type": self._clean_text(params["relation_type"]),
                "importance": self._parse_importance(params.get("importance", 0.6)),
            }

            logger.debug(
                f"提取关联参数: {extracted['source_description']} --{extracted['relation_type']}--> "
                f"{extracted['target_description']}"
            )

            return extracted

        except Exception as e:
            logger.error(f"关联参数提取失败: {e}", exc_info=True)
            raise ValueError(f"关联参数提取失败: {e}")

    def validate_relation_type(self, relation_type: str) -> str:
        """
        验证关系类型

        Args:
            relation_type: 关系类型字符串

        Returns:
            标准化的关系类型
        """
        # 因果关系映射
        causality_relations = {
            "因为": "因为",
            "所以": "所以",
            "导致": "导致",
            "引起": "导致",
            "造成": "导致",
            "因": "因为",
            "果": "所以",
        }

        # 引用关系映射
        reference_relations = {
            "引用": "引用",
            "基于": "基于",
            "关于": "关于",
            "参考": "引用",
        }

        # 相关关系
        related_relations = {
            "相关": "相关",
            "有关": "相关",
            "联系": "相关",
        }

        relation_type = relation_type.strip()

        # 查找匹配
        for mapping in [causality_relations, reference_relations, related_relations]:
            if relation_type in mapping:
                return mapping[relation_type]

        # 未找到映射，返回原值
        logger.warning(f"未识别的关系类型: {relation_type}，使用原值")
        return relation_type
