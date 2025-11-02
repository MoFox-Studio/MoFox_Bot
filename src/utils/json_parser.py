"""
统一的 JSON 解析工具模块

提供统一的 LLM 响应 JSON 解析功能，使用 json_repair 库进行修复，
简化代码并提高解析成功率。
"""

import re
from typing import Any

import orjson
from json_repair import repair_json

from src.common.logger import get_logger

logger = get_logger(__name__)


def extract_and_parse_json(response: str, *, strict: bool = False) -> dict[str, Any] | list | None:
    """
    从 LLM 响应中提取并解析 JSON
    
    处理策略：
    1. 清理 Markdown 代码块标记（```json 和 ```）
    2. 提取 JSON 对象或数组
    3. 使用 json_repair 修复格式问题
    4. 解析为 Python 对象
    
    Args:
        response: LLM 响应字符串
        strict: 严格模式，如果为 True 则解析失败时返回 None，否则尝试容错处理
        
    Returns:
        解析后的 dict 或 list，失败时返回 None
        
    Examples:
        >>> extract_and_parse_json('```json\\n{"key": "value"}\\n```')
        {'key': 'value'}
        
        >>> extract_and_parse_json('Some text {"key": "value"} more text')
        {'key': 'value'}
        
        >>> extract_and_parse_json('[{"a": 1}, {"b": 2}]')
        [{'a': 1}, {'b': 2}]
    """
    if not response:
        logger.debug("空响应，无法解析 JSON")
        return None
    
    try:
        # 步骤 1: 清理响应
        cleaned = _clean_llm_response(response)
        
        if not cleaned:
            logger.warning("清理后的响应为空")
            return None
        
        # 步骤 2: 尝试直接解析
        try:
            result = orjson.loads(cleaned)
            logger.debug(f"✅ JSON 直接解析成功，类型: {type(result).__name__}")
            return result
        except Exception as direct_error:
            logger.debug(f"直接解析失败: {type(direct_error).__name__}: {direct_error}")
        
        # 步骤 3: 使用 json_repair 修复并解析
        try:
            repaired = repair_json(cleaned)
            
            # repair_json 可能返回字符串或已解析的对象
            if isinstance(repaired, str):
                result = orjson.loads(repaired)
                logger.debug(f"✅ JSON 修复后解析成功（字符串模式），类型: {type(result).__name__}")
            else:
                result = repaired
                logger.debug(f"✅ JSON 修复后解析成功（对象模式），类型: {type(result).__name__}")
            
            return result
            
        except Exception as repair_error:
            logger.warning(f"JSON 修复失败: {type(repair_error).__name__}: {repair_error}")
            
            if strict:
                logger.error(f"严格模式下解析失败，响应片段: {cleaned[:200]}")
                return None
            
            # 最后的容错尝试：返回空字典或空列表
            if cleaned.strip().startswith("["):
                logger.warning("返回空列表作为容错")
                return []
            else:
                logger.warning("返回空字典作为容错")
                return {}
                
    except Exception as e:
        logger.error(f"❌ JSON 解析过程出现异常: {type(e).__name__}: {e}")
        if strict:
            return None
        return {} if not response.strip().startswith("[") else []


def _clean_llm_response(response: str) -> str:
    """
    清理 LLM 响应，提取 JSON 部分
    
    处理步骤：
    1. 移除 Markdown 代码块标记（```json 和 ```）
    2. 提取第一个完整的 JSON 对象 {...} 或数组 [...]
    3. 清理多余的空格和换行
    
    Args:
        response: 原始 LLM 响应
        
    Returns:
        清理后的 JSON 字符串
    """
    if not response:
        return ""
    
    cleaned = response.strip()
    
    # 移除 Markdown 代码块标记
    # 匹配 ```json ... ``` 或 ``` ... ```
    code_block_patterns = [
        r"```json\s*(.*?)```",  # ```json ... ```
        r"```\s*(.*?)```",      # ``` ... ```
    ]
    
    for pattern in code_block_patterns:
        match = re.search(pattern, cleaned, re.IGNORECASE | re.DOTALL)
        if match:
            cleaned = match.group(1).strip()
            logger.debug(f"从 Markdown 代码块中提取内容，长度: {len(cleaned)}")
            break
    
    # 提取 JSON 对象或数组
    # 优先查找对象 {...}，其次查找数组 [...]
    for start_char, end_char in [("{", "}"), ("[", "]")]:
        start_idx = cleaned.find(start_char)
        if start_idx != -1:
            # 使用栈匹配找到对应的结束符
            extracted = _extract_balanced_json(cleaned, start_idx, start_char, end_char)
            if extracted:
                logger.debug(f"提取到 {start_char}...{end_char} 结构，长度: {len(extracted)}")
                return extracted
    
    # 如果没有找到明确的 JSON 结构，返回清理后的原始内容
    logger.debug("未找到明确的 JSON 结构，返回清理后的原始内容")
    return cleaned


def _extract_balanced_json(text: str, start_idx: int, start_char: str, end_char: str) -> str | None:
    """
    从指定位置提取平衡的 JSON 结构
    
    使用栈匹配算法找到对应的结束符，处理嵌套和字符串中的特殊字符
    
    Args:
        text: 源文本
        start_idx: 起始字符的索引
        start_char: 起始字符（{ 或 [）
        end_char: 结束字符（} 或 ]）
        
    Returns:
        提取的 JSON 字符串，失败时返回 None
    """
    depth = 0
    in_string = False
    escape_next = False
    
    for i in range(start_idx, len(text)):
        char = text[i]
        
        # 处理转义字符
        if escape_next:
            escape_next = False
            continue
        
        if char == "\\":
            escape_next = True
            continue
        
        # 处理字符串
        if char == '"':
            in_string = not in_string
            continue
        
        # 只在非字符串内处理括号
        if not in_string:
            if char == start_char:
                depth += 1
            elif char == end_char:
                depth -= 1
                if depth == 0:
                    # 找到匹配的结束符
                    return text[start_idx : i + 1].strip()
    
    # 没有找到匹配的结束符
    logger.debug(f"未找到匹配的 {end_char}，深度: {depth}")
    return None


def safe_parse_json(json_str: str, default: Any = None) -> Any:
    """
    安全解析 JSON，失败时返回默认值
    
    Args:
        json_str: JSON 字符串
        default: 解析失败时返回的默认值
        
    Returns:
        解析结果或默认值
    """
    try:
        result = extract_and_parse_json(json_str, strict=False)
        return result if result is not None else default
    except Exception as e:
        logger.warning(f"安全解析 JSON 失败: {e}")
        return default


def extract_json_field(response: str, field_name: str, default: Any = None) -> Any:
    """
    从 LLM 响应中提取特定字段的值
    
    Args:
        response: LLM 响应
        field_name: 字段名
        default: 字段不存在时的默认值
        
    Returns:
        字段值或默认值
    """
    parsed = extract_and_parse_json(response, strict=False)
    
    if isinstance(parsed, dict):
        return parsed.get(field_name, default)
    
    logger.warning(f"解析结果不是字典，无法提取字段 '{field_name}'")
    return default
