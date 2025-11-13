"""
回复器API模块

提供回复器相关功能，采用标准Python包设计模式
使用方式：
    from src.plugin_system.apis import generator_api
    replyer = await generator_api.get_replyer(chat_stream)
    success, reply_set, _ = await generator_api.generate_reply(chat_stream, action_data, reasoning)
"""

import traceback
from typing import TYPE_CHECKING, Any

from rich.traceback import install

from src.chat.message_receive.chat_stream import ChatStream
from src.chat.utils.utils import process_llm_response
from src.common.data_models.database_data_model import DatabaseMessages
from src.common.logger import get_logger
from src.plugin_system.base.component_types import ActionInfo

if TYPE_CHECKING:
    from chat.replyer.default_generator import DefaultReplyer

install(extra_lines=3)

# 日志记录器
logger = get_logger("generator_api")


# =============================================================================
# 回复器获取API函数
# =============================================================================


async def get_replyer(
    chat_stream: ChatStream | None = None,
    chat_id: str | None = None,
    request_type: str = "replyer",
) -> "DefaultReplyer | None":
    """获取回复器对象

    优先使用chat_stream，如果没有则使用chat_id直接查找。
    使用 ReplyerManager 来管理实例，避免重复创建。

    Args:
        chat_stream: 聊天流对象（优先）
        chat_id: 聊天ID（实际上就是stream_id）
        request_type: 请求类型

    Returns:
        Optional[DefaultReplyer]: 回复器对象，如果获取失败则返回None

    Raises:
        ValueError: chat_stream 和 chat_id 均为空
    """
    if not chat_id and not chat_stream:
        raise ValueError("chat_stream 和 chat_id 不可均为空")
    try:
        logger.debug(f"[GeneratorAPI] 正在获取回复器，chat_id: {chat_id}, chat_stream: {'有' if chat_stream else '无'}")
        # 动态导入避免循环依赖
        from src.chat.replyer.replyer_manager import replyer_manager

        return await replyer_manager.get_replyer(
            chat_stream=chat_stream,
            chat_id=chat_id,
            request_type=request_type,
        )
    except Exception as e:
        logger.error(f"[GeneratorAPI] 获取回复器时发生意外错误: {e}", exc_info=True)
        traceback.print_exc()
        return None


# =============================================================================
# 回复生成API函数
# =============================================================================


async def generate_reply(
    chat_stream: ChatStream | None = None,
    chat_id: str | None = None,
    action_data: dict[str, Any] | None = None,
    reply_to: str = "",
    reply_message: DatabaseMessages | None = None,
    extra_info: str = "",
    available_actions: dict[str, ActionInfo] | None = None,
    enable_tool: bool = False,
    enable_splitter: bool = True,
    enable_chinese_typo: bool = True,
    return_prompt: bool = False,
    request_type: str = "generator_api",
    from_plugin: bool = True,
    read_mark: float = 0.0,
) -> tuple[bool, list[tuple[str, Any]], str | None]:
    """生成回复

    Args:
        chat_stream: 聊天流对象（优先）
        chat_id: 聊天ID（备用）
        action_data: 动作数据（向下兼容，包含reply_to和extra_info）
        reply_to: 回复对象，格式为 "发送者:消息内容"
        reply_message: 回复的原始消息
        extra_info: 额外信息，用于补充上下文
        available_actions: 可用动作
        enable_tool: 是否启用工具调用
        enable_splitter: 是否启用消息分割器
        enable_chinese_typo: 是否启用错字生成器
        return_prompt: 是否返回提示词
        model_set_with_weight: 模型配置列表，每个元素为 (TaskConfig, weight) 元组
        request_type: 请求类型（可选，记录LLM使用）
        from_plugin: 是否来自插件
    Returns:
        Tuple[bool, List[Tuple[str, Any]], Optional[str]]: (是否成功, 回复集合, 提示词)
    """
    try:
        # 获取回复器
        replyer = await get_replyer(chat_stream, chat_id, request_type=request_type)
        if not replyer:
            logger.error("[GeneratorAPI] 无法获取回复器")
            return False, [], None

        logger.debug("[GeneratorAPI] 开始生成回复")

        # 向下兼容，从action_data中获取reply_to和extra_info
        if not reply_to and action_data:
            reply_to = action_data.get("reply_to", "")
        if not extra_info and action_data:
            extra_info = action_data.get("extra_info", "")

        # 从action_data中提取prompt_mode
        prompt_mode = "s4u"  # 默认使用s4u模式
        if action_data and "prompt_mode" in action_data:
            prompt_mode = action_data.get("prompt_mode", "s4u")

        # 将prompt_mode添加到available_actions中（作为特殊键）
        # 注意：这里我们需要暂时使用类型忽略，因为available_actions的类型定义不支持非ActionInfo值
        if available_actions is None:
            available_actions = {}
        available_actions = available_actions.copy()  # 避免修改原字典
        available_actions["_prompt_mode"] = prompt_mode  # type: ignore  # 特殊键，用于传递prompt_mode

        # 如果action_data中有thinking，添加到extra_info中
        if action_data and (thinking := action_data.get("thinking")):
            if extra_info:
                extra_info += f"\n\n思考过程：{thinking}"
            else:
                extra_info = f"思考过程：{thinking}"

        # 调用回复器生成回复
        success, llm_response_dict, prompt = await replyer.generate_reply_with_context(
            reply_to=reply_to,
            extra_info=extra_info,
            available_actions=available_actions,
            enable_tool=enable_tool,
            from_plugin=from_plugin,
            stream_id=chat_stream.stream_id if chat_stream else chat_id,
            reply_message=reply_message,
        )
        if not success:
            logger.warning("[GeneratorAPI] 回复生成失败")
            return False, [], None
        assert llm_response_dict is not None, "llm_response_dict不应为None"  # 虽然说不会出现llm_response为空的情况
        if content := llm_response_dict.get("content", ""):
            # 处理为拟人化文本
            from src.chat.utils.utils import filter_system_format_content
            content = filter_system_format_content(content)
            reply_set = process_human_text(content, enable_splitter, enable_chinese_typo)
        else:
            reply_set = []
        logger.debug(f"[GeneratorAPI] 回复生成成功，生成了 {len(reply_set)} 个回复项")

        if return_prompt:
            return success, reply_set, prompt
        else:
            return success, reply_set, None

    except ValueError as ve:
        raise ve

    except UserWarning as uw:
        logger.warning(f"[GeneratorAPI] 中断了生成: {uw}")
        return False, [], None

    except Exception as e:
        logger.error(f"[GeneratorAPI] 生成回复时出错: {e}")
        logger.error(traceback.format_exc())
        return False, [], None


async def rewrite_reply(
    chat_stream: ChatStream | None = None,
    reply_data: dict[str, Any] | None = None,
    chat_id: str | None = None,
    enable_splitter: bool = True,
    enable_chinese_typo: bool = True,
    raw_reply: str = "",
    reason: str = "",
    reply_to: str = "",
    return_prompt: bool = False,
    request_type: str = "generator_api",
) -> tuple[bool, list[tuple[str, Any]], str | None]:
    """重写回复

    Args:
        chat_stream: 聊天流对象（优先）
        reply_data: 回复数据字典（向下兼容备用，当其他参数缺失时从此获取）
        chat_id: 聊天ID（备用）
        enable_splitter: 是否启用消息分割器
        enable_chinese_typo: 是否启用错字生成器
        raw_reply: 原始回复内容
        reason: 回复原因
        reply_to: 回复对象
        return_prompt: 是否返回提示词

    Returns:
        Tuple[bool, List[Tuple[str, Any]]]: (是否成功, 回复集合)
    """
    try:
        # 获取回复器
        replyer = await get_replyer(chat_stream, chat_id, request_type=request_type)
        if not replyer:
            logger.error("[GeneratorAPI] 无法获取回复器")
            return False, [], None

        logger.info("[GeneratorAPI] 开始重写回复")

        # 如果参数缺失，从reply_data中获取
        if reply_data:
            raw_reply = raw_reply or reply_data.get("raw_reply", "")
            reason = reason or reply_data.get("reason", "")
            reply_to = reply_to or reply_data.get("reply_to", "")

        # 调用回复器重写回复
        success, content, prompt = await replyer.rewrite_reply_with_context(
            raw_reply=raw_reply,
            reason=reason,
            reply_to=reply_to,
            return_prompt=return_prompt,
        )
        reply_set = []
        if content:
            # 处理为拟人化文本
            reply_set = process_human_text(content, enable_splitter, enable_chinese_typo)

        if success:
            logger.info(f"[GeneratorAPI] 重写回复成功，生成了 {len(reply_set)} 个回复项")
        else:
            logger.warning("[GeneratorAPI] 重写回复失败")

        return success, reply_set, prompt if return_prompt else None

    except ValueError as ve:
        raise ve

    except Exception as e:
        logger.error(f"[GeneratorAPI] 重写回复时出错: {e}")
        return False, [], None


def process_human_text(content: str, enable_splitter: bool, enable_chinese_typo: bool) -> list[tuple[str, Any]]:
    """将文本处理为更拟人化的文本

    Args:
        content: 文本内容
        enable_splitter: 是否启用消息分割器
        enable_chinese_typo: 是否启用错字生成器
    """
    if isinstance(content, list):
        content = "".join(map(str, content))
    if not isinstance(content, str):
        raise ValueError("content 必须是字符串类型")
    try:
        # 处理LLM响应
        processed_response = process_llm_response(content, enable_splitter, enable_chinese_typo)

        reply_set = []
        for text in processed_response:
            reply_seg = ("text", text)
            reply_set.append(reply_seg)

        return reply_set

    except Exception as e:
        logger.error(f"[GeneratorAPI] 处理人形文本时出错: {e}")
        return []


async def generate_response_custom(
    chat_stream: ChatStream | None = None,
    chat_id: str | None = None,
    request_type: str = "generator_api",
    prompt: str = "",
) -> str | None:
    """
    使用自定义提示生成回复

    Args:
        chat_stream: 聊天流对象
        chat_id: 聊天ID
        request_type: 请求类型
        prompt: 自定义提示

    Returns:
        Optional[str]: 生成的回复内容
    """
    replyer = await get_replyer(chat_stream, chat_id, request_type=request_type)
    if not replyer:
        logger.error("[GeneratorAPI] 无法获取回复器")
        return None

    try:
        logger.debug("[GeneratorAPI] 开始生成自定义回复")
        response, _, _, _ = await replyer.llm_generate_content(prompt)
        if response:
            logger.debug("[GeneratorAPI] 自定义回复生成成功")
            return response
        else:
            logger.warning("[GeneratorAPI] 自定义回复生成失败")
            return None
    except Exception as e:
        logger.error(f"[GeneratorAPI] 生成自定义回复时出错: {e}")
        return None
