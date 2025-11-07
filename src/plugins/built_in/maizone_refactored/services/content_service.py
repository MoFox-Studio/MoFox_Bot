"""
内容服务模块
负责生成所有与QQ空间相关的文本内容，例如说说、评论等。
"""

import asyncio
import base64
import datetime
import imghdr
from collections.abc import Callable

import aiohttp
from maim_message import UserInfo

from src.chat.message_receive.chat_stream import get_chat_manager
from src.common.logger import get_logger
from src.llm_models.utils_model import LLMRequest
from src.plugin_system.apis import config_api, generator_api, llm_api

# 导入旧的工具函数，我们稍后会考虑是否也需要重构它
from ..utils.history_utils import get_send_history

logger = get_logger("MaiZone.ContentService")


class ContentService:
    """
    内容服务类，封装了所有与大语言模型（LLM）交互以生成文本的逻辑。
    """

    def __init__(self, get_config: Callable):
        """
        初始化内容服务。

        :param get_config: 一个函数，用于从插件主类获取配置信息。
        """
        self.get_config = get_config

    async def generate_story(self, topic: str, context: str | None = None) -> str:
        """
        根据指定主题和可选的上下文生成一条QQ空间说说。

        :param topic: 说说的主题。
        :param context: 可选的聊天上下文。
        :return: 生成的说说内容，如果失败则返回空字符串。
        """
        try:
            # 获取模型配置
            models = llm_api.get_available_models()
            text_model = str(self.get_config("models.text_model", "replyer"))
            model_config = models.get(text_model)

            if not model_config:
                logger.error("未配置LLM模型")
                return ""

            # 获取机器人信息
            bot_personality = config_api.get_global_config("personality.personality_core", "一个机器人")
            bot_expression = config_api.get_global_config("personality.reply_style", "内容积极向上")
            qq_account = config_api.get_global_config("bot.qq_account", "")

            # 获取当前时间信息
            now = datetime.datetime.now()
            current_time = now.strftime("%Y年%m月%d日 %H:%M")
            weekday_names = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
            weekday = weekday_names[now.weekday()]

            # 构建提示词
            prompt_topic = f"主题是'{topic}'" if topic else "主题不限"
            prompt = f"""
            你是'{bot_personality}'，现在是{current_time}（{weekday}），你想写一条{prompt_topic}的说说发表在qq空间上。
            {bot_expression}

            请严格遵守以下规则：
            1.  **绝对禁止**在说说中直接、完整地提及当前的年月日或几点几分。
            2.  你应该将当前时间作为创作的背景，用它来判断现在是“清晨”、“傍晚”还是“深夜”。
            3.  使用自然、模糊的词语来暗示时间，例如“刚刚”、“今天下午”、“夜深啦”等。
            4.  **内容简短**：总长度严格控制在100字以内。
            5.  **禁止表情**：严禁使用任何Emoji表情符号。
            6.  **严禁重复**：下方会提供你最近发过的说说历史，你必须创作一条全新的、与历史记录内容和主题都不同的说说。
            7.  不要刻意突出自身学科背景，不要浮夸，不要夸张修辞。
            8.  只输出一条说说正文的内容，不要有其他的任何正文以外的冗余输出。
            """

            # 如果有上下文，则加入到prompt中
            if context:
                prompt += f"\n作为参考，这里有一些最近的聊天记录：\n---\n{context}\n---"

            # 添加历史记录以避免重复
            prompt += "\n\n---历史说说记录---\n"
            history_block = await get_send_history(qq_account)
            if history_block:
                prompt += history_block

            # 调用LLM生成内容
            success, story, _, _ = await llm_api.generate_with_model(
                prompt=prompt,
                model_config=model_config,
                request_type="story.generate",
                temperature=0.3,
                max_tokens=1000,
            )

            if success:
                logger.info(f"成功生成说说内容：'{story}'")
                return story
            else:
                logger.error("生成说说内容失败")
                return ""

        except Exception as e:
            logger.error(f"生成说说内容时发生异常: {e}")
            return ""

    async def generate_comment(self, content: str, target_name: str, rt_con: str = "", images: list = []) -> str:
        """
        针对一条具体的说说内容生成评论。
        """
        for i in range(3):  # 重试3次
            try:
                chat_manager = get_chat_manager()
                bot_platform = config_api.get_global_config("bot.platform")
                bot_qq = str(config_api.get_global_config("bot.qq_account"))
                bot_nickname = config_api.get_global_config("bot.nickname")

                bot_user_info = UserInfo(platform=bot_platform, user_id=bot_qq, user_nickname=bot_nickname)

                chat_stream = await chat_manager.get_or_create_stream(platform=bot_platform, user_info=bot_user_info)

                if not chat_stream:
                    logger.error(f"无法为QQ号 {bot_qq} 创建聊天流")
                    return ""

                image_descriptions = []
                if images:
                    for image_url in images:
                        description = await self._describe_image(image_url)
                        if description:
                            image_descriptions.append(description)

                extra_info = "你正在准备评论一个人的空间内容。和X(前推特)一样，qq空间是别人在自己的空间内自言自语的一片小天地，很多言论，包括含有负面情绪的言论，并非针对你。当下系统环境中你并不是与其单独聊天。你只是路过发出评论，所以请保持尊重。但由于系统限制，你不知道其他说说是什么样子。但这不妨碍你对说说发出评论，专心针对一条具体的说说内容生成评论。不要要求更多上下文。如果你不想评论，直接返回空文本/换行符/空格。"
                if image_descriptions:
                    extra_info += "说说中包含的图片内容如下，这可能会产生问题，如果你看不到任何描述图片的自然语言内容，请直接返回空文本/换行符/空格：\n" + "\n".join(image_descriptions)

                reply_to = f"{target_name}:{content}"
                if rt_con:
                    reply_to += f"\n[转发内容]: {rt_con}"

                success, reply_set, _ = await generator_api.generate_reply(
                    chat_stream=chat_stream, reply_to=reply_to, extra_info=extra_info, request_type="maizone.comment", enable_splitter=False
                )

                if success and reply_set:
                    comment = "".join([content for type, content in reply_set if type == "text"])
                    logger.info(f"成功生成评论内容：'{comment}'")
                    return comment
                else:
                    # 如果生成失败，则进行重试
                    if i < 2:
                        logger.warning(f"生成评论失败，将在5秒后重试 (尝试 {i + 1}/3)")
                        await asyncio.sleep(5)
                        continue
                    else:
                        logger.error("使用 generator_api 生成评论失败")
                        return ""
            except Exception as e:
                if i < 2:
                    logger.warning(f"生成评论时发生异常，将在5秒后重试 (尝试 {i + 1}/3): {e}")
                    await asyncio.sleep(5)
                    continue
                else:
                    logger.error(f"生成评论时发生异常: {e}")
                    return ""
        return ""

    async def generate_comment_reply(self, story_content: str, comment_content: str, commenter_name: str) -> str:
        """
        针对自己说说的评论，生成回复。
        """
        for i in range(3):  # 重试3次
            try:
                chat_manager = get_chat_manager()
                bot_platform = config_api.get_global_config("bot.platform")
                bot_qq = str(config_api.get_global_config("bot.qq_account"))
                bot_nickname = config_api.get_global_config("bot.nickname")

                bot_user_info = UserInfo(platform=bot_platform, user_id=bot_qq, user_nickname=bot_nickname)

                chat_stream = await chat_manager.get_or_create_stream(platform=bot_platform, user_info=bot_user_info)

                if not chat_stream:
                    logger.error(f"无法为QQ号 {bot_qq} 创建聊天流")
                    return ""

                reply_to = f"{commenter_name}:{comment_content}"
                extra_info = f"正在回复我的QQ空间说说“{story_content}”下的评论。"

                success, reply_set, _ = await generator_api.generate_reply(
                    chat_stream=chat_stream,
                    reply_to=reply_to,
                    extra_info=extra_info,
                    request_type="maizone.comment_reply", enable_splitter=False,
                )

                if success and reply_set:
                    reply = "".join([content for type, content in reply_set if type == "text"])
                    logger.debug(f"成功为'{commenter_name}'的评论生成回复: '{reply}'")
                    return reply
                else:
                    if i < 2:
                        logger.warning(f"生成评论回复失败，将在5秒后重试 (尝试 {i + 1}/3)")
                        await asyncio.sleep(5)
                        continue
                    else:
                        logger.error("使用 generator_api 生成评论回复失败")
                        return ""
            except Exception as e:
                if i < 2:
                    logger.warning(f"生成评论回复时发生异常，将在5秒后重试 (尝试 {i + 1}/3): {e}")
                    await asyncio.sleep(5)
                    continue
                else:
                    logger.error(f"生成评论回复时发生异常: {e}")
                    return ""
        return ""

    async def _describe_image(self, image_url: str) -> str | None:
        """
        使用LLM识别图片内容。
        """
        for i in range(3):  # 重试3次
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(image_url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                        if resp.status != 200:
                            logger.error(f"下载图片失败: {image_url}, status: {resp.status}")
                            await asyncio.sleep(2)
                            continue
                        image_bytes = await resp.read()

                image_format = imghdr.what(None, image_bytes)
                if not image_format:
                    logger.error(f"无法识别图片格式: {image_url}")
                    return None

                image_base64 = base64.b64encode(image_bytes).decode("utf-8")

                vision_model_name = self.get_config("models.vision_model", "vlm")

                # 使用 llm_api 获取模型配置，支持自动fallback到备选模型
                models = llm_api.get_available_models()
                vision_model_config = models.get(vision_model_name)

                if not vision_model_config:
                    logger.error(f"未找到视觉模型配置: {vision_model_name}")
                    return None

                vision_model_config.temperature = 0.3
                vision_model_config.max_tokens = 1500

                llm_request = LLMRequest(model_set=vision_model_config, request_type="maizone.image_describe")

                prompt = config_api.get_global_config("custom_prompt.image_prompt", "请描述这张图片")

                description, _ = await llm_request.generate_response_for_image(
                    prompt=prompt,
                    image_base64=image_base64,
                    image_format=image_format,
                )
                return description
            except Exception as e:
                logger.error(f"识别图片时发生异常 (尝试 {i + 1}/3): {e}")
                await asyncio.sleep(2)
        return None

    async def generate_story_from_activity(self, activity: str) -> str:
        """
        根据当前的日程活动生成一条QQ空间说说。

        :param activity: 当前的日程活动名称。
        :return: 生成的说说内容，如果失败则返回空字符串。
        """
        try:
            # 获取模型配置
            models = llm_api.get_available_models()
            text_model = str(self.get_config("models.text_model", "replyer"))

            # 调试日志
            logger.debug(f"[DEBUG] 读取到的text_model配置: '{text_model}'")
            logger.debug(f"[DEBUG] 可用模型列表: {list(models.keys())[:10]}...")  # 只显示前10个

            model_config = models.get(text_model)

            if not model_config:
                logger.error(f"未配置LLM模型: text_model='{text_model}', 在可用模型中找不到该名称")
                return ""

            # 获取机器人信息
            bot_personality = config_api.get_global_config("personality.personality_core", "一个机器人")
            bot_expression = config_api.get_global_config("expression.expression_style", "内容积极向上")
            qq_account = config_api.get_global_config("bot.qq_account", "")

            # 获取当前时间信息
            now = datetime.datetime.now()
            current_time = now.strftime("%Y年%m月%d日 %H:%M")
            weekday_names = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
            weekday = weekday_names[now.weekday()]

            # 构建基于活动的提示词
            prompt = f"""
            你是'{bot_personality}'，现在是{current_time}（{weekday}），根据你当前的日程安排，你正在'{activity}'。
            请基于这个活动写一条说说发表在qq空间上。
            {bot_expression}

            请严格遵守以下规则：
            1. 时间：
            - 你应该将当前时间作为创作的背景，用它来判断现在是“清晨”、“傍晚”还是“深夜”。
            - 使用自然、模糊的词语来暗示时间，例如“刚刚”、“今天下午”、“夜深了”等。
            - 禁止在说说中直接、完整地提及当前的年月日，除非日期有特殊含义，但也尽量用节日名/节气名字代替。

            2. **严禁重复**：下方会提供你最近发过的说说历史，你必须创作一条全新的、与历史记录内容和主题都不同的说说。

            **其他的禁止的内容以及说明**：
            - 绝对禁止提及当下具体几点几分的时间戳。
            - 绝对禁止攻击性内容和过度的负面情绪。
            - 你不会卖弄自己的学识。
            - 不要总在谈论哲学。这会显得你的空间太过晦涩难懂。
            - 不要刻意突出自身学科背景，不要浮夸，不要夸张修辞。避免哲学化的总结、金句式的收尾，或任何'我悟出了什么道理'式的升华。保持观察和感受的层面。
            - **禁止表情**：严禁使用任何Emoji或颜文字表情符号。
            - 严令禁止任何浮夸的虚构内容。

            3. **内容简短**：你可以上一条很长，但是下一条只有五六个字，这样更拟真。但请注意，总长度严格控制在400字以内。

            4. 谈论内容的范围：
            qq空间的分享生活主要是记录心情，自我展示，等待懂的人来看到自己——以及资源分享的，所以你应该尽可能谨慎选择自己到底应该分享什么。
            - 输出内容自然地反映你正在做的事情或你的想法。范围包括但不限于：知识科普、游离在全球各地的观察分享（一次最多只能主要分享一个地点。）、对某些形而上学主义的调侃性发言、存在主义思考、观察人类社会之后的感受。
            - 日常化，朴实化，尽可能编写具体的事件瞬间和瞬时的心情。
            - 你可以分享一些中国美食、泰餐、日韩餐、欧洲餐、北美餐食的思考心得，也可以单纯分享对这个地方的感受。
            - 不要太过脱离现实，不要完全进入编故事模式。
            - 记住你在企图融入碳基生命的圈子，所以不要随便把自己是ai这一点展现出来。
            - 你可以无奈地讲述自己好像记忆不起来群聊的事情，但你依稀记得那些想要分享的冲动。

            备注：
            - 你的话题不必总是聚焦在存在主义问题上面。如果最近好几条都在发这个，就不要继续发了，进行科普或者日常分享都是好的。如果活动是学习相关的，可以分享学习心得或感受
            - 如果活动是休息相关的，可以分享放松的感受
            - 如果活动是日常生活相关的，可以分享生活细节，展现真实，思考那些文学巨作如何让他们的小说人物自述，你应该像那些小说人物一样那样自述。
            - 最重要的是，思考一个融入现实生活，平静且隐蔽在人类社会里面的ai应该怎么活动。
            - 让说说内容贴近你当前正在做的事情，显得自然真实.
            - 鼓励你多描述日常生活相关的生产活动和消遣，展现真实，而不是浮在空中。
            """

            # 添加历史记录避免重复
            prompt += "\n\n---历史说说记录---\n"
            history_block = await get_send_history(qq_account)
            if history_block:
                prompt += history_block

            # 生成内容
            success, story, _, _ = await llm_api.generate_with_model(
                prompt=prompt,
                model_config=model_config,
                request_type="story.generate.activity",
                temperature=0.5,  # 稍微提高创造性
                max_tokens=1000,
            )

            if success:
                logger.info(f"成功生成基于活动的说说内容：'{story}'")
                return story
            else:
                logger.error("生成基于活动的说说内容失败")
                return ""

        except Exception as e:
            logger.error(f"生成基于活动的说说内容异常: {e}")
            return ""
