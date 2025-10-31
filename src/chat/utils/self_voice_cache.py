"""
自我语音缓存模块

用于在机器人发送TTS语音后，临时存储其原始文本，
以便在接收到该语音消息时，能够直接获取文本内容，
避免不必要的自我语音识别。
"""
import hashlib

# 一个简单的内存缓存，用于将机器人自己发送的语音消息映射到其原始文本。
# 键是语音base64内容的SHA256哈希值。
_self_voice_cache: dict[str, str] = {}

def get_voice_key(base64_content: str) -> str:
    """为语音内容生成一个一致的键。"""
    return hashlib.sha256(base64_content.encode("utf-8")).hexdigest()

def register_self_voice(base64_content: str, text: str):
    """
    为机器人自己发送的语音消息注册其原始文本。

    Args:
        base64_content (str): 语音的base64编码内容。
        text (str): 原始文本。
    """
    key = get_voice_key(base64_content)
    _self_voice_cache[key] = text

def consume_self_voice_text(base64_content: str) -> str | None:
    """
    获取并移除机器人自己发送的语音消息的原始文本。
    这是一个一次性操作，获取后即从缓存中删除。

    Args:
        base64_content (str): 语音的base64编码内容。

    Returns:
        str | None: 如果找到，则返回原始文本，否则返回None。
    """
    key = get_voice_key(base64_content)
    return _self_voice_cache.pop(key, None)
