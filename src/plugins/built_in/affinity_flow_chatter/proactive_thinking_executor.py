"""
主动思考执行器
当定时任务触发时，负责搜集信息、调用LLM决策、并根据决策生成回复
"""

import json
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import select

from src.chat.express.expression_selector import expression_selector
from src.chat.utils.prompt import Prompt
from src.common.database.sqlalchemy_database_api import get_db_session
from src.common.database.sqlalchemy_models import ChatStreams
from src.common.logger import get_logger
from src.config.config import global_config, model_config
from src.individuality.individuality import Individuality
from src.llm_models.utils_model import LLMRequest
from src.plugin_system.apis import message_api, send_api

logger = get_logger("proactive_thinking_executor")

# ==================================================================================================
# == Prompt Templates
# ==================================================================================================

# 决策 Prompt
decision_prompt_template = Prompt(
    """你的人设是：
{bot_personality}

现在是 {current_time}，你正在考虑是否要在与 "{stream_name}" 的对话中主动说些什么。

【你当前的心情】
{current_mood}

【聊天环境信息】
- 整体印象: {stream_impression}
- 聊天风格: {chat_style}
- 常见话题: {topic_keywords}
- 你的兴趣程度: {interest_score:.2f}/1.0
{last_decision_text}

【最近的聊天记录】
{recent_chat_history}

请根据以上信息，决定你现在应该做什么：

**选项1：什么都不做 (do_nothing)**
- 适用场景：气氛不适合说话、最近对话很活跃、没什么特别想说的、或者此时说话会显得突兀。
- 心情影响：如果心情不好（如生气、难过），可能更倾向于保持沉默。

**选项2：简单冒个泡 (simple_bubble)**
- 适用场景：对话有些冷清，你想缓和气氛或开启新的互动。
- 方式：说一句轻松随意的话，旨在建立或维持连接。
- 心情影响：心情会影响你冒泡的方式和内容。

**选项3：发起一次有目的的互动 (throw_topic)**
- 适用场景：你想延续对话、表达关心、或深入讨论某个具体话题。
- **【互动类型1：延续约定或提醒】(最高优先级)**：检查最近的聊天记录，是否存在可以延续的互动。例如，如果昨晚的最后一条消息是“晚安”，现在是早上，一个“早安”的回应是绝佳的选择。如果之前提到过某个约定（如“待会聊”），现在可以主动跟进。
- **【互动类型2：展现真诚的关心】(次高优先级)**：如果不存在可延续的约定，请仔细阅读聊天记录，寻找对方提及的个人状况（如天气、出行、身体、情绪、工作学习等），并主动表达关心。
- **【互动类型3：开启新话题】**：当以上两点都不适用时，可以考虑开启一个你感兴趣的新话题。
- 心情影响：心情会影响你想发起互动的方式和内容。

请以JSON格式回复你的决策：
{{
    "action": "do_nothing" | "simple_bubble" | "throw_topic",
    "reasoning": "你的决策理由（请结合你的心情、聊天环境和对话历史进行分析）",
    "topic": "(仅当action=throw_topic时填写)你的互动意图（如：回应晚安并说早安、关心对方的考试情况、讨论新游戏）"
}}

注意：
1. 兴趣度较低(<0.4)时或者最近聊天很活跃（不到1小时），倾向于 `do_nothing` 或 `simple_bubble`。
2. 你的心情会影响你的行动倾向和表达方式。
3. 参考上次决策，避免重复，并可根据上次的互动效果调整策略。
4. 只有在真的有感而发时才选择 `throw_topic`。
5. 保持你的人设，确保行为一致性。
""",
    name="proactive_thinking_decision",
)

# 冒泡回复 Prompt
simple_bubble_reply_prompt_template = Prompt(
    """你的人设是：
{bot_personality}

距离上次对话已经有一段时间了，你决定主动说些什么，轻松地开启新的互动。

【你当前的心情】
{current_mood}

【聊天环境】
- 整体印象: {stream_impression}
- 聊天风格: {chat_style}

【最近的聊天记录】
{recent_chat_history}
{expression_habits}
请生成一条简短的消息，用于水群。
【要求】
1. 风格简短随意（5-20字）
2. 不要提出明确的话题或问题，可以是问候、表达心情或一句随口的话。
3. 符合你的人设和当前聊天风格。
4. **你的心情应该影响消息的内容和语气**。
5. 如果有表达方式参考，在合适时自然使用。
6. 合理参考历史记录。
直接输出消息内容，不要解释：""",
    name="proactive_thinking_simple_bubble",
)

# 抛出话题回复 Prompt
throw_topic_reply_prompt_template = Prompt(
    """你的人设是：
{bot_personality}

现在是 {current_time}，你决定在与 "{stream_name}" 的对话中主动发起一次互动。

【你当前的心情】
{current_mood}

【聊天环境】
- 整体印象: {stream_impression}
- 聊天风格: {chat_style}
- 常见话题: {topic_keywords}

【最近的聊天记录】
{recent_chat_history}

【你的互动意图】
{topic}
{expression_habits}
【构思指南】
请根据你的互动意图，生成一条有温度的消息。
- 如果意图是**延续约定**（如回应“晚安”），请直接生成对应的问候。
- 如果意图是**表达关心**（如跟进对方提到的事），请生成自然、真诚的关心话语。
- 如果意图是**开启新话题**，请自然地引入话题。

请根据这个意图，生成一条消息，要求：
1. 自然地引入话题或表达关心。
2. 长度适中（20-50字）。
3. 可以结合最近的聊天记录，使对话更连贯。
4. 符合你的人设和当前聊天风格。
5. **你的心情会影响你的表达方式**。
6. 如果有表达方式参考，在合适时自然使用。

直接输出消息内容，不要解释：""",
    name="proactive_thinking_throw_topic",
)


class ProactiveThinkingPlanner:
    """主动思考规划器

    负责：
    1. 搜集信息（聊天流印象、话题关键词、历史聊天记录）
    2. 调用LLM决策：什么都不做/简单冒泡/抛出话题
    3. 根据决策生成回复内容
    """

    def __init__(self):
        """初始化规划器"""
        try:
            self.decision_llm = LLMRequest(
                model_set=model_config.model_task_config.utils, request_type="proactive_thinking_decision"
            )
            self.reply_llm = LLMRequest(
                model_set=model_config.model_task_config.replyer, request_type="proactive_thinking_reply"
            )
        except Exception as e:
            logger.error(f"初始化LLM失败: {e}")
            self.decision_llm = None
            self.reply_llm = None

    async def gather_context(self, stream_id: str) -> dict[str, Any] | None:
        """搜集聊天流的上下文信息

        Args:
            stream_id: 聊天流ID

        Returns:
            dict: 包含所有上下文信息的字典，失败返回None
        """
        try:
            # 1. 获取聊天流印象数据
            stream_data = await self._get_stream_impression(stream_id)
            if not stream_data:
                logger.warning(f"无法获取聊天流 {stream_id} 的印象数据")
                return None

            # 2. 获取最近的聊天记录
            recent_messages = await message_api.get_recent_messages(
                chat_id=stream_id,
                limit=40,
                limit_mode="latest",
                hours=24
            )

            recent_chat_history = ""
            if recent_messages:
                recent_chat_history = await message_api.build_readable_messages_to_str(recent_messages)

            # 3. 获取bot人设
            individuality = Individuality()
            bot_personality = await individuality.get_personality_block()

            # 4. 获取当前心情
            current_mood = "感觉很平静"  # 默认心情
            try:
                from src.mood.mood_manager import mood_manager

                mood_obj = mood_manager.get_mood_by_chat_id(stream_id)
                if mood_obj:
                    await mood_obj._initialize()  # 确保已初始化
                    current_mood = mood_obj.mood_state
                    logger.debug(f"获取到聊天流 {stream_id} 的心情: {current_mood}")
            except Exception as e:
                logger.warning(f"获取心情失败，使用默认值: {e}")

            # 5. 获取上次决策
            last_decision = None
            try:
                from src.plugins.built_in.affinity_flow_chatter.proactive_thinking_scheduler import (
                    proactive_thinking_scheduler,
                )

                last_decision = proactive_thinking_scheduler.get_last_decision(stream_id)
                if last_decision:
                    logger.debug(f"获取到聊天流 {stream_id} 的上次决策: {last_decision.get('action')}")
            except Exception as e:
                logger.warning(f"获取上次决策失败: {e}")

            # 6. 构建上下文
            context = {
                "stream_id": stream_id,
                "stream_name": stream_data.get("stream_name", "未知"),
                "stream_impression": stream_data.get("stream_impression_text", "暂无印象"),
                "chat_style": stream_data.get("stream_chat_style", "未知"),
                "topic_keywords": stream_data.get("stream_topic_keywords", ""),
                "interest_score": stream_data.get("stream_interest_score", 0.5),
                "recent_chat_history": recent_chat_history or "暂无最近聊天记录",
                "bot_personality": bot_personality,
                "current_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "current_mood": current_mood,
                "last_decision": last_decision,
            }

            logger.debug(f"成功搜集聊天流 {stream_id} 的上下文信息")
            return context

        except Exception as e:
            logger.error(f"搜集上下文信息失败: {e}", exc_info=True)
            return None

    async def _get_stream_impression(self, stream_id: str) -> dict[str, Any] | None:
        """从数据库获取聊天流印象数据"""
        try:
            async with get_db_session() as session:
                stmt = select(ChatStreams).where(ChatStreams.stream_id == stream_id)
                result = await session.execute(stmt)
                stream = result.scalar_one_or_none()

                if not stream:
                    return None

                return {
                    "stream_name": stream.group_name or "私聊",
                    "stream_impression_text": stream.stream_impression_text or "",
                    "stream_chat_style": stream.stream_chat_style or "",
                    "stream_topic_keywords": stream.stream_topic_keywords or "",
                    "stream_interest_score": float(stream.stream_interest_score)
                    if stream.stream_interest_score
                    else 0.5,
                }

        except Exception as e:
            logger.error(f"获取聊天流印象失败: {e}")
            return None

    async def make_decision(self, context: dict[str, Any]) -> dict[str, Any] | None:
        """使用LLM进行决策

        Args:
            context: 上下文信息

        Returns:
            dict: 决策结果，包含：
                - action: "do_nothing" | "simple_bubble" | "throw_topic"
                - reasoning: 决策理由
                - topic: (可选) 如果是throw_topic，包含话题内容
        """
        if not self.decision_llm:
            logger.error("决策LLM未初始化")
            return None

        response = None
        try:
            # 构建上次决策信息
            last_decision_text = ""
            if context.get("last_decision"):
                last_dec = context["last_decision"]
                last_action = last_dec.get("action", "未知")
                last_reasoning = last_dec.get("reasoning", "无")
                last_topic = last_dec.get("topic")
                last_time = last_dec.get("timestamp", "未知")

                last_decision_text = f"""
【上次主动思考的决策】
- 时间: {last_time}
- 决策: {last_action}
- 理由: {last_reasoning}"""
                if last_topic:
                    last_decision_text += f"\n- 话题: {last_topic}"

            decision_prompt = decision_prompt_template.format(
                bot_personality=context["bot_personality"],
                current_time=context["current_time"],
                stream_name=context["stream_name"],
                current_mood=context.get("current_mood", "感觉很平静"),
                stream_impression=context["stream_impression"],
                chat_style=context["chat_style"],
                topic_keywords=context["topic_keywords"] or "暂无",
                interest_score=context["interest_score"],
                last_decision_text=last_decision_text,
                recent_chat_history=context["recent_chat_history"],
            )

            if global_config.debug.show_prompt:
                logger.info(f"决策提示词:\n{decision_prompt}")

            response, _ = await self.decision_llm.generate_response_async(prompt=decision_prompt)

            if not response:
                logger.warning("LLM未返回有效响应")
                return None

            # 清理并解析JSON响应
            cleaned_response = self._clean_json_response(response)
            decision = json.loads(cleaned_response)

            logger.info(f"决策结果: {decision.get('action', 'unknown')} - {decision.get('reasoning', '无理由')}")

            return decision

        except json.JSONDecodeError as e:
            logger.error(f"解析决策JSON失败: {e}")
            if response:
                logger.debug(f"原始响应: {response}")
            return None
        except Exception as e:
            logger.error(f"决策过程失败: {e}", exc_info=True)
            return None

    async def generate_reply(
        self, context: dict[str, Any], action: Literal["simple_bubble", "throw_topic"], topic: str | None = None
    ) -> str | None:
        """生成回复内容

        Args:
            context: 上下文信息
            action: 动作类型
            topic: (可选) 话题内容，当action=throw_topic时必须提供

        Returns:
            str: 生成的回复文本，失败返回None
        """
        if not self.reply_llm:
            logger.error("回复LLM未初始化")
            return None

        try:
            # 获取表达方式参考
            expression_habits = await self._get_expression_habits(
                stream_id=context.get("stream_id", ""), chat_history=context.get("recent_chat_history", "")
            )

            if action == "simple_bubble":
                reply_prompt = simple_bubble_reply_prompt_template.format(
                    bot_personality=context["bot_personality"],
                    current_mood=context.get("current_mood", "感觉很平静"),
                    stream_impression=context["stream_impression"],
                    chat_style=context["chat_style"],
                    recent_chat_history=context["recent_chat_history"],
                    expression_habits=expression_habits,
                )
            else:  # throw_topic
                reply_prompt = throw_topic_reply_prompt_template.format(
                    bot_personality=context["bot_personality"],
                    current_time=context["current_time"],
                    stream_name=context["stream_name"],
                    current_mood=context.get("current_mood", "感觉很平静"),
                    stream_impression=context["stream_impression"],
                    chat_style=context["chat_style"],
                    topic_keywords=context["topic_keywords"] or "暂无",
                    recent_chat_history=context["recent_chat_history"],
                    topic=topic,
                    expression_habits=expression_habits,
                )


            if global_config.debug.show_prompt:
                logger.info(f"回复提示词:\n{reply_prompt}")

            response, _ = await self.reply_llm.generate_response_async(prompt=reply_prompt)

            if not response:
                logger.warning("LLM未返回有效回复")
                return None

            logger.info(f"生成回复成功: {response[:50]}...")
            return response.strip()

        except Exception as e:
            logger.error(f"生成回复失败: {e}", exc_info=True)
            return None

    async def _get_expression_habits(self, stream_id: str, chat_history: str) -> str:
        """获取表达方式参考

        Args:
            stream_id: 聊天流ID
            chat_history: 聊天历史

        Returns:
            str: 格式化的表达方式参考文本
        """
        try:
            # 使用表达方式选择器获取合适的表达方式
            selected_expressions = await expression_selector.select_suitable_expressions(
                chat_id=stream_id,
                chat_history=chat_history,
                target_message=None,  # 主动思考没有target message
                max_num=6,  # 主动思考时使用较少的表达方式
                min_num=2,
            )

            if not selected_expressions:
                return ""

            style_habits = []
            grammar_habits = []

            for expr in selected_expressions:
                if isinstance(expr, dict) and "situation" in expr and "style" in expr:
                    expr_type = expr.get("type", "style")
                    if expr_type == "grammar":
                        grammar_habits.append(f"当{expr['situation']}时，使用 {expr['style']}")
                    else:
                        style_habits.append(f"当{expr['situation']}时，使用 {expr['style']}")

            expression_block = ""
            if style_habits or grammar_habits:
                expression_block = "\n【表达方式参考】\n"
                if style_habits:
                    expression_block += "语言习惯：\n" + "\n".join(style_habits) + "\n"
                if grammar_habits:
                    expression_block += "句法特点：\n" + "\n".join(grammar_habits) + "\n"
                expression_block += "注意：仅在情景合适时自然地使用这些表达，不要生硬套用。\n"

            return expression_block

        except Exception as e:
            logger.warning(f"获取表达方式失败: {e}")
            return ""

    def _clean_json_response(self, response: str) -> str:
        """清理LLM响应中的JSON格式标记"""
        import re

        cleaned = response.strip()
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.MULTILINE | re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned, flags=re.MULTILINE)

        json_start = cleaned.find("{")
        json_end = cleaned.rfind("}")

        if json_start != -1 and json_end != -1 and json_end > json_start:
            cleaned = cleaned[json_start : json_end + 1]

        return cleaned.strip()


# 全局规划器实例
_planner = ProactiveThinkingPlanner()

# 统计数据
_statistics: dict[str, dict[str, Any]] = {}


def _update_statistics(stream_id: str, action: str):
    """更新统计数据

    Args:
        stream_id: 聊天流ID
        action: 执行的动作
    """
    if stream_id not in _statistics:
        _statistics[stream_id] = {
            "total_executions": 0,
            "do_nothing_count": 0,
            "simple_bubble_count": 0,
            "throw_topic_count": 0,
            "last_execution_time": None,
        }

    _statistics[stream_id]["total_executions"] += 1
    _statistics[stream_id][f"{action}_count"] += 1
    _statistics[stream_id]["last_execution_time"] = datetime.now().isoformat()


def get_statistics(stream_id: str | None = None) -> dict[str, Any]:
    """获取统计数据

    Args:
        stream_id: 聊天流ID，None表示获取所有统计

    Returns:
        统计数据字典
    """
    if stream_id:
        return _statistics.get(stream_id, {})
    return _statistics


async def execute_proactive_thinking(stream_id: str):
    """执行主动思考（被调度器调用的回调函数）

    Args:
        stream_id: 聊天流ID
    """
    from src.config.config import global_config
    from src.plugins.built_in.affinity_flow_chatter.proactive_thinking_scheduler import (
        proactive_thinking_scheduler,
    )

    config = global_config.proactive_thinking

    logger.debug(f"🤔 开始主动思考 {stream_id}")

    try:
        # 0. 前置检查
        if proactive_thinking_scheduler._is_in_quiet_hours():
            logger.debug("安静时段，跳过")
            return

        if not proactive_thinking_scheduler._check_daily_limit(stream_id):
            logger.debug("今日发言达上限")
            return

        # 1. 搜集信息
        logger.debug("步骤1: 搜集上下文")
        context = await _planner.gather_context(stream_id)
        if not context:
            logger.warning("无法搜集上下文，跳过")
            return

        # 检查兴趣分数阈值
        interest_score = context.get("interest_score", 0.5)
        if not proactive_thinking_scheduler._check_interest_score_threshold(interest_score):
            logger.debug("兴趣分数不在阈值范围内")
            return

        # 2. 进行决策
        logger.debug("步骤2: LLM决策")
        decision = await _planner.make_decision(context)
        if not decision:
            logger.warning("决策失败，跳过")
            return

        action = decision.get("action", "do_nothing")
        reasoning = decision.get("reasoning", "无")

        # 记录决策日志
        if config.log_decisions:
            logger.debug(f"决策: action={action}, reasoning={reasoning}")

        # 3. 根据决策执行相应动作
        if action == "do_nothing":
            logger.debug(f"决策：什么都不做。理由：{reasoning}")
            proactive_thinking_scheduler.record_decision(stream_id, action, reasoning, None)
            return

        elif action == "simple_bubble":
            logger.info(f"💬 决策：冒个泡。理由：{reasoning}")

            proactive_thinking_scheduler.record_decision(stream_id, action, reasoning, None)

            # 生成简单的消息
            logger.debug("步骤3: 生成冒泡回复")
            reply = await _planner.generate_reply(context, "simple_bubble")
            if reply:
                await send_api.text_to_stream(
                    stream_id=stream_id,
                    text=reply,
                )
                logger.info("✅ 已发送冒泡消息")

                # 增加每日计数
                proactive_thinking_scheduler._increment_daily_count(stream_id)

                # 更新统计
                if config.enable_statistics:
                    _update_statistics(stream_id, action)

                # 冒泡后暂停主动思考，等待用户回复
                # 使用与 topic_throw 相同的冷却时间配置
                if config.topic_throw_cooldown > 0:
                    logger.info("[主动思考] 步骤5：暂停任务")
                    await proactive_thinking_scheduler.pause_proactive_thinking(stream_id, reason="已冒泡")
                    logger.info(f"[主动思考] 已暂停聊天流 {stream_id} 的主动思考，等待用户回复")

            logger.info("[主动思考] simple_bubble 执行完成")

        elif action == "throw_topic":
            topic = decision.get("topic", "")
            logger.info(f"[主动思考] 决策：抛出话题。理由：{reasoning}，话题：{topic}")

            # 记录决策
            proactive_thinking_scheduler.record_decision(stream_id, action, reasoning, topic)

            if not topic:
                logger.warning("[主动思考] 选择了抛出话题但未提供话题内容，降级为冒泡")
                logger.info("[主动思考] 步骤3：生成降级冒泡回复")
                reply = await _planner.generate_reply(context, "simple_bubble")
            else:
                # 生成基于话题的消息
                logger.info("[主动思考] 步骤3：生成话题回复")
                reply = await _planner.generate_reply(context, "throw_topic", topic)

            if reply:
                logger.info("[主动思考] 步骤4：发送消息")
                await send_api.text_to_stream(
                    stream_id=stream_id,
                    text=reply,
                )
                logger.info(f"[主动思考] 已发送话题消息到 {stream_id}")

                # 增加每日计数
                proactive_thinking_scheduler._increment_daily_count(stream_id)

                # 更新统计
                if config.enable_statistics:
                    _update_statistics(stream_id, action)

                # 抛出话题后暂停主动思考（如果配置了冷却时间）
                if config.topic_throw_cooldown > 0:
                    logger.info("[主动思考] 步骤5：暂停任务")
                    await proactive_thinking_scheduler.pause_proactive_thinking(stream_id, reason="已抛出话题")
                    logger.info(f"[主动思考] 已暂停聊天流 {stream_id} 的主动思考，等待用户回复")

            logger.info("[主动思考] throw_topic 执行完成")

        logger.info(f"[主动思考] 聊天流 {stream_id} 的主动思考执行完成")

    except Exception as e:
        logger.error(f"[主动思考] 执行主动思考失败: {e}", exc_info=True)
