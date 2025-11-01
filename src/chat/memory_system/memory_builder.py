"""
记忆构建模块
从对话流中提取高质量、结构化记忆单元
输出格式要求:
{
    "memories": [
        {
            "type": "记忆类型",
            "display": "一句优雅自然的中文描述，用于直接展示及提示词拼接",
            "subject": ["主体1", "主体2"],
            "predicate": "谓语(动作/状态)",
            "object": "宾语(对象/属性或结构体)",
            "keywords": ["关键词1", "关键词2"],
            "importance": "重要性等级(1-4)",
            "confidence": "置信度(1-4)",
            "reasoning": "提取理由"
        }
    ]
}

注意：
1. `subject` 可包含多个主体，请用数组表示；若主体不明确，请根据上下文给出最合理的称呼
2. `display` 字段必填，必须是完整顺畅的自然语言，禁止依赖字符串拼接
3. 主谓宾用于索引和检索结构化信息，提示词构建仅使用 `display`
4. 只提取确实值得记忆的信息，不要提取琐碎内容
5. 确保信息准确、具体、有价值
6. 重要性: 1=低, 2=一般, 3=高, 4=关键；置信度: 1=低, 2=中等, 3=高, 4=已验证
"""

import re
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, TypeVar

E = TypeVar("E", bound=Enum)


import orjson
from json_repair import repair_json

from src.chat.memory_system.memory_chunk import (
    ConfidenceLevel,
    ImportanceLevel,
    MemoryChunk,
    MemoryType,
    create_memory_chunk,
)
from src.common.logger import get_logger
from src.llm_models.utils_model import LLMRequest

logger = get_logger(__name__)


CHINESE_TO_MEMORY_TYPE: dict[str, MemoryType] = {
    "个人事实": MemoryType.PERSONAL_FACT,
    "事件": MemoryType.EVENT,
    "偏好": MemoryType.PREFERENCE,
    "观点": MemoryType.OPINION,
    "关系": MemoryType.RELATIONSHIP,
    "情感": MemoryType.EMOTION,
    "知识": MemoryType.KNOWLEDGE,
    "技能": MemoryType.SKILL,
    "目标": MemoryType.GOAL,
    "经验": MemoryType.EXPERIENCE,
    "上下文": MemoryType.CONTEXTUAL,
}


class ExtractionStrategy(Enum):
    """提取策略"""

    LLM_BASED = "llm_based"  # 基于LLM的智能提取
    RULE_BASED = "rule_based"  # 基于规则的提取
    HYBRID = "hybrid"  # 混合策略


@dataclass
class ExtractionResult:
    """提取结果"""

    memories: list[MemoryChunk]
    confidence_scores: list[float]
    extraction_time: float
    strategy_used: ExtractionStrategy


class MemoryExtractionError(Exception):
    """记忆提取过程中发生的不可恢复错误"""


class MemoryBuilder:
    """记忆构建器"""

    def __init__(self, llm_model: LLMRequest):
        self.llm_model = llm_model
        self.extraction_stats = {
            "total_extractions": 0,
            "successful_extractions": 0,
            "failed_extractions": 0,
            "average_confidence": 0.0,
        }

    async def build_memories(
        self, conversation_text: str, context: dict[str, Any], user_id: str, timestamp: float
    ) -> list[MemoryChunk]:
        """从对话中构建记忆"""
        start_time = time.time()

        try:
            logger.debug(f"开始从对话构建记忆，文本长度: {len(conversation_text)}")

            # 使用LLM提取记忆
            memories = await self._extract_with_llm(conversation_text, context, user_id, timestamp)

            # 后处理和验证
            validated_memories = self._validate_and_enhance_memories(memories, context)

            # 更新统计
            extraction_time = time.time() - start_time
            self._update_extraction_stats(len(validated_memories), extraction_time)

            logger.info(f"✅ 成功构建 {len(validated_memories)} 条记忆，耗时 {extraction_time:.2f}秒")
            return validated_memories

        except MemoryExtractionError as e:
            logger.error(f"❌ 记忆构建失败（响应解析错误）: {e}")
            self.extraction_stats["failed_extractions"] += 1
            raise
        except Exception as e:
            logger.error(f"❌ 记忆构建失败: {e}", exc_info=True)
            self.extraction_stats["failed_extractions"] += 1
            raise

    async def _extract_with_llm(
        self, text: str, context: dict[str, Any], user_id: str, timestamp: float
    ) -> list[MemoryChunk]:
        """使用LLM提取记忆"""
        try:
            prompt = self._build_llm_extraction_prompt(text, context)

            response, _ = await self.llm_model.generate_response_async(prompt, temperature=0.3)
            
            # 记录原始响应用于调试
            if response:
                logger.debug(f"LLM记忆提取原始响应长度: {len(response)}, 前300字符: {response[:300]}")
            else:
                logger.warning("LLM记忆提取返回空响应")

            # 解析LLM响应
            memories = self._parse_llm_response(response, user_id, timestamp, context)

            return memories

        except MemoryExtractionError:
            raise
        except Exception as e:
            logger.error(f"LLM提取失败: {e}")
            raise MemoryExtractionError(str(e)) from e

    def _build_llm_extraction_prompt(self, text: str, context: dict[str, Any]) -> str:
        """构建LLM提取提示"""
        current_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        message_type = context.get("message_type", "normal")

        bot_name = context.get("bot_name")
        bot_identity = context.get("bot_identity")
        bot_personality = context.get("bot_personality")
        bot_personality_side = context.get("bot_personality_side")
        bot_aliases = context.get("bot_aliases") or []
        if isinstance(bot_aliases, str):
            bot_aliases = [bot_aliases]

        bot_name_display = bot_name or "机器人"
        alias_display = "、".join(a for a in bot_aliases if a) or "无"
        persona_details = []
        if bot_identity:
            persona_details.append(f"身份: {bot_identity}")
        if bot_personality:
            persona_details.append(f"核心人设: {bot_personality}")
        if bot_personality_side:
            persona_details.append(f"侧写: {bot_personality_side}")
        persona_display = "；".join(persona_details) if persona_details else "无"

        prompt = f"""
你是一个专业的记忆提取专家。请从以下对话中主动识别并提取所有可能重要的信息，特别是包含个人事实、事件、偏好、观点等要素的内容。

当前时间: {current_date}
消息类型: {message_type}

## 🤖 机器人身份（仅供参考，禁止写入记忆）
- 机器人名称: {bot_name_display}
- 别名: {alias_display}
- 机器人人设概述: {persona_display}

这些信息是机器人的固定设定，可用于帮助你理解对话。你可以在需要时记录机器人自身的状态、行为或设定，但要与用户信息清晰区分，避免误将系统ID写入记忆。

请务必遵守以下命名规范：
- 当说话者是机器人时，请使用“{bot_name_display}”或其他明确称呼作为主语；
- 记录关键事实时，请准确标记主体是机器人还是用户，避免混淆。

对话内容:
{text}

## 🎯 重点记忆类型识别指南

### 1. **个人事实** (personal_fact) - 高优先级记忆
**包括但不限于：**
- 基本信息：姓名、年龄、职业、学校、专业、工作地点
- 生活状况：住址、电话、邮箱、社交账号
- 身份特征：生日、星座、血型、国籍、语言能力
- 健康信息：身体状况、疾病史、药物过敏、运动习惯
- 家庭情况：家庭成员、婚姻状况、子女信息、宠物信息

**判断标准：** 涉及个人身份和生活的重要信息，都应该记忆

### 2. **事件** (event) - 高优先级记忆
**包括但不限于：**
- 重要时刻：生日聚会、毕业典礼、婚礼、旅行
- 日常活动：上班、上学、约会、看电影、吃饭
- 特殊经历：考试、面试、会议、搬家、购物
- 计划安排：约会、会议、旅行、活动


**判断标准：** 涉及时间地点的具体活动和经历，都应该记忆

### 3. **偏好** (preference) - 高优先级记忆
**包括但不限于：**
- 饮食偏好：喜欢的食物、餐厅、口味、禁忌
- 娱乐喜好：喜欢的电影、音乐、游戏、书籍
- 生活习惯：作息时间、运动方式、购物习惯
- 消费偏好：品牌喜好、价格敏感度、购物场所
- 风格偏好：服装风格、装修风格、颜色喜好

**判断标准：** 任何表达"喜欢"、"不喜欢"、"习惯"、"经常"等偏好的内容，都应该记忆

### 4. **观点** (opinion) - 高优先级记忆
**包括但不限于：**
- 评价看法：对事物的评价、意见、建议
- 价值判断：认为什么重要、什么不重要
- 态度立场：支持、反对、中立的态度
- 感受反馈：对经历的感受、反馈

**判断标准：** 任何表达主观看法和态度的内容，都应该记忆

### 5. **关系** (relationship) - 中等优先级记忆
**包括但不限于：**
- 人际关系：朋友、同事、家人、恋人的关系状态
- 社交互动：与他人的互动、交流、合作
- 群体归属：所属团队、组织、社群

### 6. **情感** (emotion) - 中等优先级记忆
**包括但不限于：**
- 情绪状态：开心、难过、生气、焦虑、兴奋
- 情感变化：情绪的转变、原因和结果

### 7. **目标** (goal) - 中等优先级记忆
**包括但不限于：**
- 计划安排：短期计划、长期目标
- 愿望期待：想要实现的事情、期望的结果

## 📝 记忆提取原则

### ✅ 积极提取原则：
1. **宁可错记，不可遗漏** - 对于可能的个人信息优先记忆
2. **持续追踪** - 相同信息的多次提及要强化记忆
3. **上下文关联** - 结合对话背景理解信息重要性
4. **细节丰富** - 记录具体的细节和描述

### 🚫 禁止使用模糊代称原则：
1. **绝对禁止使用"用户"作为代称** - 必须使用明确的名字或称呼
2. **优先使用真实姓名** - 如果知道对方的名字，必须使用真实姓名
3. **使用昵称或特定称呼** - 如果没有真实姓名，使用对话中出现的昵称
4. **无法获取具体名字时拒绝构建** - 如果不知道对方的具体名字，宁可不构建这条记忆，也不要使用"用户"、"该对话者"等模糊代称

### 🕒 时间处理原则（重要）：
1. **绝对时间要求** - 涉及时间的记忆必须使用绝对时间（年月日）
2. **相对时间转换** - 将"明天"、"后天"、"下周"等相对时间转换为具体日期
3. **时间格式规范** - 使用"YYYY-MM-DD"格式记录日期
4. **当前时间参考** - 当前时间：{current_date}，基于此计算相对时间

**相对时间转换示例：**
- "明天" → "2024-09-30"
- "后天" → "2024-10-01"
- "下周" → "2024-10-07"
- "下个月" → "2024-10-01"
- "明年" → "2025-01-01"

### 🎯 重要性等级标准：
- **4分 (关键)**：个人核心信息（姓名、联系方式、重要日期）
- **3分 (高)**：重要偏好、观点、经历事件
- **2分 (一般)**：一般性信息、日常活动、感受表达
- **1分 (低)**：琐碎细节、重复信息、临时状态

### 🔍 置信度标准：
- **4分 (已验证)**：用户明确确认的信息
- **3分 (高)**：用户直接表达的清晰信息
- **2分 (中等)**：需要推理或上下文判断的信息
- **1分 (低)**：模糊或不完整的信息

输出格式要求:
{{
    "memories": [
        {{
            "type": "记忆类型",
            "display": "一句自然流畅的中文描述，用于直接展示和提示词构建",
            "subject": "主语(通常是用户)",
            "predicate": "谓语(动作/状态)",
            "object": "宾语(对象/属性)",
            "keywords": ["关键词1", "关键词2"],
            "importance": "重要性等级(1-4)",
            "confidence": "置信度(1-4)",
            "reasoning": "提取理由"
        }}
    ]
}}

注意：
1. `display` 字段必填，必须是完整顺畅的自然语言，禁止依赖字符串拼接
2. **display 字段格式要求**: 使用自然流畅的中文描述，**绝对禁止使用"用户"作为代称**，格式示例：
   - 杰瑞喵养了一只名叫Whiskers的猫。
   - why ocean QAQ特别喜欢拿铁咖啡。
   - 在2024年5月15日，velida QAQ提到对新项目感到很有压力。
   - 杰瑞喵认为这个电影很有趣。
3. **必须使用明确的名字**：如果知道对话者的名字（如杰瑞喵、why ocean QAQ等），必须直接使用其名字
4. **不知道名字时不要构建**：如果无法从对话中确定对方的具体名字，宁可不构建这条记忆
5. 主谓宾用于索引和检索，提示词构建仅使用 `display` 的自然语言描述
6. 只提取确实值得记忆的信息，不要提取琐碎内容
7. 确保提取的信息准确、具体、有价值
8. 重要性等级: 1=低, 2=一般, 3=高, 4=关键；置信度: 1=低, 2=中等, 3=高, 4=已验证

## 🚨 时间处理要求（强制）：
- **绝对时间优先**：任何涉及时间的记忆都必须使用绝对日期格式
- **相对时间转换**：遇到"明天"、"后天"、"下周"等相对时间必须转换为具体日期
- **时间格式**：统一使用 "YYYY-MM-DD" 格式
- **计算依据**：基于当前时间 {current_date} 进行转换计算
"""

        return prompt

    def _extract_json_payload(self, response: str) -> str | None:
        """从模型响应中提取JSON部分，兼容Markdown代码块等格式
        
        增强的JSON提取策略，支持多种格式：
        1. Markdown代码块: ```json ... ```
        2. 普通代码块: ``` ... ```
        3. 大括号包围的JSON对象
        4. 直接的JSON字符串
        """
        if not response:
            return None

        stripped = response.strip()

        # 策略1: 优先处理Markdown代码块格式 ```json ... ``` 或 ``` ... ```
        code_block_patterns = [
            r"```json\s*(.*?)```",  # 明确标记json的代码块
            r"```\s*(.*?)```",      # 普通代码块
        ]
        
        for pattern in code_block_patterns:
            code_block_match = re.search(pattern, stripped, re.IGNORECASE | re.DOTALL)
            if code_block_match:
                candidate = code_block_match.group(1).strip()
                if candidate and (candidate.startswith("{") or candidate.startswith("[")):
                    logger.debug(f"从代码块中提取JSON，长度: {len(candidate)}")
                    return candidate

        # 策略2: 查找第一个完整的JSON对象（大括号匹配）
        start = stripped.find("{")
        if start != -1:
            # 使用栈来找到匹配的结束大括号
            brace_count = 0
            in_string = False
            escape_next = False
            
            for i in range(start, len(stripped)):
                char = stripped[i]
                
                if escape_next:
                    escape_next = False
                    continue
                    
                if char == "\\":
                    escape_next = True
                    continue
                
                if char == '"' and not escape_next:
                    in_string = not in_string
                    continue
                
                if not in_string:
                    if char == "{":
                        brace_count += 1
                    elif char == "}":
                        brace_count -= 1
                        if brace_count == 0:
                            # 找到完整的JSON对象
                            candidate = stripped[start : i + 1].strip()
                            logger.debug(f"通过大括号匹配提取JSON，长度: {len(candidate)}")
                            return candidate

        # 策略3: 简单的整体检查（作为最后的fallback）
        if stripped.startswith("{") and stripped.endswith("}"):
            logger.debug(f"整体作为JSON，长度: {len(stripped)}")
            return stripped

        # 所有策略都失败
        logger.warning(f"无法从响应中提取JSON，响应预览: {stripped[:200]}")
        return None

    def _parse_llm_response(
        self, response: str, user_id: str, timestamp: float, context: dict[str, Any]
    ) -> list[MemoryChunk]:
        """解析LLM响应"""
        if not response:
            raise MemoryExtractionError("LLM未返回任何响应")

        json_payload = self._extract_json_payload(response)
        if not json_payload:
            preview = response[:200] if response else "空响应"
            raise MemoryExtractionError(f"未在LLM响应中找到有效的JSON负载，响应片段: {preview}")

        try:
            data = orjson.loads(json_payload)
            logger.debug(f"JSON直接解析成功，数据keys: {list(data.keys()) if isinstance(data, dict) else type(data)}")
        except Exception as e:
            # 尝试使用 json_repair 修复 JSON
            logger.warning(f"JSON直接解析失败: {type(e).__name__}: {e}，尝试使用json_repair修复")
            logger.debug(f"失败的JSON片段(前500字符): {json_payload[:500]}")
            try:
                repaired_json = repair_json(json_payload)
                # repair_json 可能返回字符串或已解析的对象
                if isinstance(repaired_json, str):
                    data = orjson.loads(repaired_json)
                    logger.info(f"✅ JSON修复成功(字符串模式)，数据keys: {list(data.keys()) if isinstance(data, dict) else type(data)}")
                else:
                    data = repaired_json
                    logger.info(f"✅ JSON修复成功(对象模式)，数据keys: {list(data.keys()) if isinstance(data, dict) else type(data)}")
            except Exception as repair_error:
                preview = json_payload[:300]
                logger.error(f"❌ JSON修复也失败: {type(repair_error).__name__}: {repair_error}")
                logger.error(f"完整JSON payload(前800字符):\n{json_payload[:800]}")
                raise MemoryExtractionError(
                    f"LLM响应JSON解析失败\n"
                    f"原始错误: {type(e).__name__}: {e}\n"
                    f"修复错误: {type(repair_error).__name__}: {repair_error}\n"
                    f"JSON片段(前300字符): {preview}"
                ) from e

        # 提取 memories 列表，兼容多种格式
        memory_list = data.get("memories", [])
        
        # 如果没有 memories 字段，尝试其他可能的字段名
        if not memory_list:
            for possible_key in ["memory", "results", "items", "data"]:
                if possible_key in data:
                    memory_list = data[possible_key]
                    logger.debug(f"使用备选字段 '{possible_key}' 作为记忆列表")
                    break
        
        # 如果整个data就是一个列表，直接使用
        if not memory_list and isinstance(data, list):
            memory_list = data
            logger.debug("整个JSON就是记忆列表")
        
        if not isinstance(memory_list, list):
            logger.warning(f"记忆列表格式错误，期望list但得到 {type(memory_list)}, 尝试包装为列表")
            memory_list = [memory_list] if memory_list else []
        
        logger.debug(f"提取到 {len(memory_list)} 个记忆候选项")

        bot_identifiers = self._collect_bot_identifiers(context)
        system_identifiers = self._collect_system_identifiers(context)
        default_subjects = self._resolve_conversation_participants(context, user_id)

        bot_display = None
        if context:
            primary_bot_name = context.get("bot_name")
            if isinstance(primary_bot_name, str) and primary_bot_name.strip():
                bot_display = primary_bot_name.strip()
            if bot_display is None:
                aliases = context.get("bot_aliases")
                if isinstance(aliases, list | tuple | set):
                    for alias in aliases:
                        if isinstance(alias, str) and alias.strip():
                            bot_display = alias.strip()
                            break
                elif isinstance(aliases, str) and aliases.strip():
                    bot_display = aliases.strip()
            if bot_display is None:
                identity = context.get("bot_identity")
                if isinstance(identity, str) and identity.strip():
                    bot_display = identity.strip()

        if not bot_display:
            bot_display = "机器人"

        bot_display = self._clean_subject_text(bot_display)

        memories: list[MemoryChunk] = []

        for mem_data in memory_list:
            try:
                # 检查是否包含模糊代称
                display_text = mem_data.get("display", "")
                if any(
                    ambiguous_term in display_text for ambiguous_term in ["用户", "user", "the user", "对方", "对手"]
                ):
                    logger.debug(f"拒绝构建包含模糊代称的记忆，display字段: {display_text}")
                    continue

                subject_value = mem_data.get("subject")
                normalized_subject = self._normalize_subjects(
                    subject_value, bot_identifiers, system_identifiers, default_subjects, bot_display
                )

                if not normalized_subject:
                    logger.debug("跳过疑似机器人自身信息的记忆: %s", mem_data)
                    continue

                # 创建记忆块
                importance_level = self._parse_enum_value(
                    ImportanceLevel, mem_data.get("importance"), ImportanceLevel.NORMAL, "importance"
                )

                confidence_level = self._parse_enum_value(
                    ConfidenceLevel, mem_data.get("confidence"), ConfidenceLevel.MEDIUM, "confidence"
                )

                predicate_value = mem_data.get("predicate", "")
                object_value = mem_data.get("object", "")

                display_text = self._sanitize_display_text(mem_data.get("display"))
                used_fallback_display = False
                if not display_text:
                    display_text = self._compose_display_text(normalized_subject, predicate_value, object_value)
                    used_fallback_display = True

                memory = create_memory_chunk(
                    user_id=user_id,
                    subject=normalized_subject,
                    predicate=predicate_value,
                    obj=object_value,
                    memory_type=self._resolve_memory_type(mem_data.get("type")),
                    chat_id=context.get("chat_id"),
                    source_context=mem_data.get("reasoning", ""),
                    importance=importance_level,
                    confidence=confidence_level,
                    display=display_text,
                )

                if used_fallback_display:
                    logger.warning(
                        "LLM 记忆缺少自然语言 display 字段，已基于主谓宾临时生成描述",
                        fallback_generated=True,
                        memory_type=memory.memory_type.value,
                        subjects=memory.content.to_subject_list(),
                        predicate=predicate_value,
                        object_payload=object_value,
                    )

                # 添加关键词
                keywords = mem_data.get("keywords", [])
                for keyword in keywords:
                    memory.add_keyword(keyword)

                memories.append(memory)

            except Exception as e:
                logger.warning(f"解析单个记忆失败: {e}, 数据: {mem_data}")
                continue

        return memories

    def _resolve_memory_type(self, type_str: Any) -> MemoryType:
        """健壮地解析记忆类型，兼容中文和英文"""
        if not isinstance(type_str, str) or not type_str.strip():
            return MemoryType.CONTEXTUAL

        cleaned_type = type_str.strip()

        # 尝试中文映射
        if cleaned_type in CHINESE_TO_MEMORY_TYPE:
            return CHINESE_TO_MEMORY_TYPE[cleaned_type]

        # 尝试直接作为枚举值解析
        try:
            return MemoryType(cleaned_type.lower().replace(" ", "_"))
        except ValueError:
            pass

        # 尝试作为枚举名解析
        try:
            return MemoryType[cleaned_type.upper()]
        except KeyError:
            pass

        logger.warning(f"无法解析未知的记忆类型 '{type_str}'，回退到上下文类型")
        return MemoryType.CONTEXTUAL

    def _parse_enum_value(self, enum_cls: type[E], raw_value: Any, default: E, field_name: str) -> E:
        """解析枚举值，兼容数字/字符串表示"""
        if isinstance(raw_value, enum_cls):
            return raw_value

        if raw_value is None:
            return default

        # 直接尝试整数转换
        if isinstance(raw_value, int | float):
            int_value = int(raw_value)
            try:
                return enum_cls(int_value)
            except ValueError:
                logger.debug("%s=%s 无法解析为 %s", field_name, raw_value, enum_cls.__name__)
                return default

        if isinstance(raw_value, str):
            value_str = raw_value.strip()
            if not value_str:
                return default

            if value_str.isdigit():
                try:
                    return enum_cls(int(value_str))
                except ValueError:
                    logger.debug("%s='%s' 无法解析为 %s", field_name, value_str, enum_cls.__name__)
            else:
                normalized = value_str.replace("-", "_").replace(" ", "_").upper()
                for member in enum_cls:
                    if member.name == normalized:
                        return member
                for member in enum_cls:
                    if str(member.value).lower() == value_str.lower():
                        return member

                try:
                    return enum_cls(value_str)
                except ValueError:
                    logger.debug("%s='%s' 无法解析为 %s", field_name, value_str, enum_cls.__name__)

        try:
            return enum_cls(raw_value)
        except Exception:
            logger.debug(
                "%s=%s 类型 %s 无法解析为 %s，使用默认值 %s",
                field_name,
                raw_value,
                type(raw_value).__name__,
                enum_cls.__name__,
                default.name,
            )
            return default

    def _collect_bot_identifiers(self, context: dict[str, Any] | None) -> set[str]:
        identifiers: set[str] = {"bot", "机器人", "ai助手"}
        if not context:
            return identifiers

        for key in [
            "bot_name",
            "bot_identity",
            "bot_personality",
            "bot_personality_side",
            "bot_account",
        ]:
            value = context.get(key)
            if isinstance(value, str) and value.strip():
                identifiers.add(value.strip().lower())

        aliases = context.get("bot_aliases")
        if isinstance(aliases, list | tuple | set):
            for alias in aliases:
                if isinstance(alias, str) and alias.strip():
                    identifiers.add(alias.strip().lower())
        elif isinstance(aliases, str) and aliases.strip():
            identifiers.add(aliases.strip().lower())

        return identifiers

    def _collect_system_identifiers(self, context: dict[str, Any] | None) -> set[str]:
        identifiers: set[str] = set()
        if not context:
            return identifiers

        keys = [
            "chat_id",
            "stream_id",
            "stram_id",
            "session_id",
            "conversation_id",
            "message_id",
            "topic_id",
            "thread_id",
        ]

        for key in keys:
            value = context.get(key)
            if isinstance(value, str) and value.strip():
                identifiers.add(value.strip().lower())

        user_id_value = context.get("user_id")
        if isinstance(user_id_value, str) and user_id_value.strip():
            if self._looks_like_system_identifier(user_id_value):
                identifiers.add(user_id_value.strip().lower())

        return identifiers

    def _resolve_conversation_participants(self, context: dict[str, Any] | None, user_id: str) -> list[str]:
        participants: list[str] = []

        if context:
            candidate_keys = [
                "participants",
                "participant_names",
                "speaker_names",
                "members",
                "member_names",
                "mention_users",
                "audiences",
            ]

            for key in candidate_keys:
                value = context.get(key)
                if isinstance(value, list | tuple | set):
                    for item in value:
                        if isinstance(item, str):
                            cleaned = self._clean_subject_text(item)
                            if cleaned:
                                participants.append(cleaned)
                elif isinstance(value, str):
                    participants.extend(part for part in self._split_subject_string(value) if part)

        fallback = self._resolve_user_display(context, user_id)
        if fallback:
            participants.append(fallback)

        if context:
            bot_name = context.get("bot_name") or context.get("bot_identity")
            if isinstance(bot_name, str):
                cleaned = self._clean_subject_text(bot_name)
                if cleaned:
                    participants.append(cleaned)

        if not participants:
            participants = ["对话参与者"]

        deduplicated: list[str] = []
        seen = set()
        for name in participants:
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            deduplicated.append(name)

        return deduplicated

    def _resolve_user_display(self, context: dict[str, Any] | None, user_id: str) -> str:
        candidate_keys = [
            "user_display_name",
            "user_name",
            "nickname",
            "sender_name",
            "member_name",
            "display_name",
            "from_user_name",
            "author_name",
            "speaker_name",
        ]

        if context:
            for key in candidate_keys:
                value = context.get(key)
                if isinstance(value, str):
                    candidate = value.strip()
                    if candidate:
                        return self._clean_subject_text(candidate)

        if user_id and not self._looks_like_system_identifier(user_id):
            return self._clean_subject_text(user_id)

        return "该用户"

    def _clean_subject_text(self, text: str) -> str:
        if not text:
            return ""
        cleaned = re.sub(r"[\s\u3000]+", " ", text).strip()
        cleaned = re.sub(r"[、，,；;]+$", "", cleaned)
        return cleaned

    def _sanitize_display_text(self, value: Any) -> str:
        if value is None:
            return ""

        if isinstance(value, list | dict):
            try:
                value = orjson.dumps(value).decode("utf-8")
            except Exception:
                value = str(value)

        text = str(value).strip()
        if not text or text.lower() in {"null", "none", "undefined"}:
            return ""

        text = re.sub(r"[\s\u3000]+", " ", text)
        return text.strip("\n ")

    def _looks_like_system_identifier(self, value: str) -> bool:
        if not value:
            return False

        condensed = value.replace("-", "").replace("_", "").strip()
        if len(condensed) >= 16 and re.fullmatch(r"[0-9a-fA-F]+", condensed):
            return True

        if len(value) >= 12 and re.fullmatch(r"[0-9A-Z_:-]+", value) and any(ch.isdigit() for ch in value):
            return True

        return False

    def _split_subject_string(self, value: str) -> list[str]:
        if not value:
            return []

        replaced = re.sub(r"\band\b", "、", value, flags=re.IGNORECASE)
        replaced = replaced.replace("和", "、").replace("与", "、").replace("及", "、")
        replaced = replaced.replace("&", "、").replace("/", "、").replace("+", "、")

        tokens = [self._clean_subject_text(token) for token in re.split(r"[、,，;；]+", replaced)]
        return [token for token in tokens if token]

    def _normalize_subjects(
        self,
        subject: Any,
        bot_identifiers: set[str],
        system_identifiers: set[str],
        default_subjects: list[str],
        bot_display: str | None = None,
    ) -> list[str]:
        defaults = default_subjects or ["对话参与者"]

        raw_candidates: list[str] = []
        if isinstance(subject, list):
            for item in subject:
                if isinstance(item, str):
                    raw_candidates.extend(self._split_subject_string(item))
                elif item is not None:
                    raw_candidates.extend(self._split_subject_string(str(item)))
        elif isinstance(subject, str):
            raw_candidates.extend(self._split_subject_string(subject))
        elif subject is not None:
            raw_candidates.extend(self._split_subject_string(str(subject)))

        normalized: list[str] = []
        bot_primary = self._clean_subject_text(bot_display or "")

        for candidate in raw_candidates:
            if not candidate:
                continue

            lowered = candidate.lower()
            if lowered in bot_identifiers:
                normalized.append(bot_primary or candidate)
                continue

            if lowered in {"用户", "user", "the user", "对方", "对手"}:
                # 直接拒绝构建包含模糊代称的记忆
                logger.debug(f"拒绝构建包含模糊代称的记忆: {candidate}")
                return []  # 返回空列表表示拒绝构建

            if lowered in system_identifiers or self._looks_like_system_identifier(candidate):
                continue

            normalized.append(candidate)

        if not normalized:
            normalized = list(defaults)

        deduplicated: list[str] = []
        seen = set()
        for name in normalized:
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            deduplicated.append(name)

        return deduplicated

    def _extract_value_from_object(self, obj: str | dict[str, Any] | list[Any], keys: list[str]) -> str | None:
        if isinstance(obj, dict):
            for key in keys:
                value = obj.get(key)
                if value is None:
                    continue
                if isinstance(value, list):
                    compact = "、".join(str(item) for item in value[:3])
                    if compact:
                        return compact
                else:
                    value_str = str(value).strip()
                    if value_str:
                        return value_str
        elif isinstance(obj, list):
            compact = "、".join(str(item) for item in obj[:3])
            return compact or None
        elif isinstance(obj, str):
            return obj.strip() or None
        return None

    def _compose_display_text(self, subjects: list[str], predicate: str, obj: str | dict[str, Any] | list[Any]) -> str:
        subject_phrase = "、".join(subjects) if subjects else "对话参与者"
        predicate = (predicate or "").strip()

        if predicate == "is_named":
            name = self._extract_value_from_object(obj, ["name", "nickname"]) or ""
            name = self._clean_subject_text(name)
            if name:
                quoted = name if (name.startswith("「") and name.endswith("」")) else f"「{name}」"
                return f"{subject_phrase}的昵称是{quoted}"
        elif predicate == "is_age":
            age = self._extract_value_from_object(obj, ["age"]) or ""
            age = self._clean_subject_text(age)
            if age:
                return f"{subject_phrase}今年{age}岁"
        elif predicate == "is_profession":
            profession = self._extract_value_from_object(obj, ["profession", "job"]) or ""
            profession = self._clean_subject_text(profession)
            if profession:
                return f"{subject_phrase}的职业是{profession}"
        elif predicate == "lives_in":
            location = self._extract_value_from_object(obj, ["location", "city", "place"]) or ""
            location = self._clean_subject_text(location)
            if location:
                return f"{subject_phrase}居住在{location}"
        elif predicate == "has_phone":
            phone = self._extract_value_from_object(obj, ["phone", "number"]) or ""
            phone = self._clean_subject_text(phone)
            if phone:
                return f"{subject_phrase}的电话号码是{phone}"
        elif predicate == "has_email":
            email = self._extract_value_from_object(obj, ["email"]) or ""
            email = self._clean_subject_text(email)
            if email:
                return f"{subject_phrase}的邮箱是{email}"
        elif predicate in {"likes", "likes_food", "favorite_is"}:
            liked = self._extract_value_from_object(obj, ["item", "value", "name"]) or ""
            liked = self._clean_subject_text(liked)
            if liked:
                verb = "喜欢" if predicate != "likes_food" else "爱吃"
                if predicate == "favorite_is":
                    verb = "最喜欢"
                return f"{subject_phrase}{verb}{liked}"
        elif predicate in {"dislikes", "hates"}:
            disliked = self._extract_value_from_object(obj, ["item", "value", "name"]) or ""
            disliked = self._clean_subject_text(disliked)
            if disliked:
                verb = "不喜欢" if predicate == "dislikes" else "讨厌"
                return f"{subject_phrase}{verb}{disliked}"
        elif predicate == "mentioned_event":
            description = self._extract_value_from_object(obj, ["event_text", "description"]) or ""
            description = self._clean_subject_text(description)
            if description:
                return f"{subject_phrase}提到了：{description}"

        obj_text = self._extract_value_from_object(obj, ["value", "detail", "content"]) or ""
        obj_text = self._clean_subject_text(obj_text)

        if predicate and obj_text:
            return f"{subject_phrase}{predicate}{obj_text}".strip()
        if obj_text:
            return f"{subject_phrase}{obj_text}".strip()
        if predicate:
            return f"{subject_phrase}{predicate}".strip()
        return subject_phrase

    def _validate_and_enhance_memories(self, memories: list[MemoryChunk], context: dict[str, Any]) -> list[MemoryChunk]:
        """验证和增强记忆"""
        validated_memories = []

        for memory in memories:
            # 基本验证
            if not self._validate_memory(memory):
                continue

            # 增强记忆
            enhanced_memory = self._enhance_memory(memory, context)
            validated_memories.append(enhanced_memory)

        return validated_memories

    def _validate_memory(self, memory: MemoryChunk) -> bool:
        """验证记忆块"""
        # 检查基本字段
        if not memory.content.subject or not memory.content.predicate:
            logger.debug(f"记忆块缺少主语或谓语: {memory.memory_id}")
            return False

        # 检查内容长度
        content_length = len(memory.text_content)
        if content_length < 5 or content_length > 500:
            logger.debug(f"记忆块内容长度异常: {content_length}")
            return False

        # 检查置信度
        if memory.metadata.confidence == ConfidenceLevel.LOW:
            logger.debug(f"记忆块置信度过低: {memory.memory_id}")
            return False

        return True

    def _enhance_memory(self, memory: MemoryChunk, context: dict[str, Any]) -> MemoryChunk:
        """增强记忆块"""
        # 时间规范化处理
        self._normalize_time_in_memory(memory)

        # 添加时间上下文
        if not memory.temporal_context:
            memory.temporal_context = {
                "timestamp": memory.metadata.created_at,
                "timezone": context.get("timezone", "UTC"),
                "day_of_week": datetime.fromtimestamp(memory.metadata.created_at).strftime("%A"),
            }

        # 添加情感上下文（如果有）
        if context.get("sentiment"):
            memory.metadata.emotional_context = context["sentiment"]

        # 自动添加标签
        self._auto_tag_memory(memory)

        return memory

    def _normalize_time_in_memory(self, memory: MemoryChunk):
        """规范化记忆中的时间表达"""
        import re
        from datetime import datetime, timedelta

        # 获取当前时间作为参考
        current_time = datetime.fromtimestamp(memory.metadata.created_at)

        # 定义相对时间映射
        relative_time_patterns = {
            r"今天|今日": current_time.strftime("%Y-%m-%d"),
            r"昨天|昨日": (current_time - timedelta(days=1)).strftime("%Y-%m-%d"),
            r"明天|明日": (current_time + timedelta(days=1)).strftime("%Y-%m-%d"),
            r"后天": (current_time + timedelta(days=2)).strftime("%Y-%m-%d"),
            r"大后天": (current_time + timedelta(days=3)).strftime("%Y-%m-%d"),
            r"前天": (current_time - timedelta(days=2)).strftime("%Y-%m-%d"),
            r"大前天": (current_time - timedelta(days=3)).strftime("%Y-%m-%d"),
            r"本周|这周|这星期": current_time.strftime("%Y-%m-%d"),
            r"上周|上星期": (current_time - timedelta(weeks=1)).strftime("%Y-%m-%d"),
            r"下周|下星期": (current_time + timedelta(weeks=1)).strftime("%Y-%m-%d"),
            r"本月|这个月": current_time.strftime("%Y-%m-01"),
            r"上月|上个月": (current_time.replace(day=1) - timedelta(days=1)).strftime("%Y-%m-01"),
            r"下月|下个月": (current_time.replace(day=1) + timedelta(days=32)).replace(day=1).strftime("%Y-%m-01"),
            r"今年|今年": current_time.strftime("%Y"),
            r"去年|上一年": str(current_time.year - 1),
            r"明年|下一年": str(current_time.year + 1),
        }

        def _normalize_value(value):
            if isinstance(value, str):
                normalized = value
                for pattern, replacement in relative_time_patterns.items():
                    normalized = re.sub(pattern, replacement, normalized)
                return normalized
            if isinstance(value, dict):
                return {k: _normalize_value(v) for k, v in value.items()}
            if isinstance(value, list):
                return [_normalize_value(item) for item in value]
            return value

        # 规范化主语和谓语（通常是字符串）
        memory.content.subject = _normalize_value(memory.content.subject)
        memory.content.predicate = _normalize_value(memory.content.predicate)

        # 规范化宾语（可能是字符串、列表或字典）
        memory.content.object = _normalize_value(memory.content.object)

        # 记录时间规范化操作
        logger.debug(f"记忆 {memory.memory_id} 已进行时间规范化")

    def _auto_tag_memory(self, memory: MemoryChunk):
        """自动为记忆添加标签"""
        # 基于记忆类型的自动标签
        type_tags = {
            MemoryType.PERSONAL_FACT: ["个人信息", "基本资料"],
            MemoryType.EVENT: ["事件", "日程"],
            MemoryType.PREFERENCE: ["偏好", "喜好"],
            MemoryType.OPINION: ["观点", "态度"],
            MemoryType.RELATIONSHIP: ["关系", "社交"],
            MemoryType.EMOTION: ["情感", "情绪"],
            MemoryType.KNOWLEDGE: ["知识", "信息"],
            MemoryType.SKILL: ["技能", "能力"],
            MemoryType.GOAL: ["目标", "计划"],
            MemoryType.EXPERIENCE: ["经验", "经历"],
        }

        tags = type_tags.get(memory.memory_type, [])
        for tag in tags:
            memory.add_tag(tag)

    def _update_extraction_stats(self, success_count: int, extraction_time: float):
        """更新提取统计"""
        self.extraction_stats["total_extractions"] += 1
        self.extraction_stats["successful_extractions"] += success_count
        self.extraction_stats["failed_extractions"] += max(0, 1 - success_count)

        # 更新平均置信度
        if self.extraction_stats["successful_extractions"] > 0:
            total_confidence = self.extraction_stats["average_confidence"] * (
                self.extraction_stats["successful_extractions"] - success_count
            )
            # 假设新记忆的平均置信度为0.8
            total_confidence += 0.8 * success_count
            self.extraction_stats["average_confidence"] = (
                total_confidence / self.extraction_stats["successful_extractions"]
            )

    def get_extraction_stats(self) -> dict[str, Any]:
        """获取提取统计信息"""
        return self.extraction_stats.copy()

    def reset_stats(self):
        """重置统计信息"""
        self.extraction_stats = {
            "total_extractions": 0,
            "successful_extractions": 0,
            "failed_extractions": 0,
            "average_confidence": 0.0,
        }
