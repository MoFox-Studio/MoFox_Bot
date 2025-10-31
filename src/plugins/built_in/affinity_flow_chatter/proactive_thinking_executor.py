"""
主动思考执行器
当定时任务触发时，负责搜集信息、调用LLM决策、并根据决策生成回复
"""

import json
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import select

from src.chat.express.expression_selector import expression_selector
from src.common.database.sqlalchemy_database_api import get_db_session
from src.common.database.sqlalchemy_models import ChatStreams
from src.common.logger import get_logger
from src.config.config import global_config, model_config
from src.individuality.individuality import Individuality
from src.llm_models.utils_model import LLMRequest
from src.plugin_system.apis import message_api, send_api

logger = get_logger("proactive_thinking_executor")


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
                chat_id=stream_id, limit=20, limit_mode="latest", hours=24
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
            decision_prompt = self._build_decision_prompt(context)

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

    def _build_decision_prompt(self, context: dict[str, Any]) -> str:
        """构建决策提示词"""
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

        return f"""你是一个有着独特个性的AI助手。你的人设是：
{context["bot_personality"]}

现在是 {context["current_time"]}，你正在考虑是否要主动在 "{context["stream_name"]}" 中说些什么。

【你当前的心情】
{context.get("current_mood", "感觉很平静")}

【聊天环境信息】
- 整体印象: {context["stream_impression"]}
- 聊天风格: {context["chat_style"]}
- 常见话题: {context["topic_keywords"] or "暂无"}
- 你的兴趣程度: {context["interest_score"]:.2f}/1.0
{last_decision_text}

【最近的聊天记录】
{context["recent_chat_history"]}

请根据以上信息（包括你的心情和上次决策），决定你现在应该做什么：

**选项1：什么都不做 (do_nothing)**
- 适用场景：现在可能是休息时间、工作时间，或者气氛不适合说话
- 也可能是：最近聊天很活跃不需要你主动、没什么特别想说的、此时说话会显得突兀
- 心情影响：如果心情不好（如生气、难过），可能更倾向于保持沉默

**选项2：简单冒个泡 (simple_bubble)**  
- 适用场景：群里有点冷清，你想引起注意或活跃气氛
- 方式：简单问个好、发个表情、说句无关紧要的话，没有深意，就是刷个存在感
- 心情影响：心情好时可能更活跃；心情不好时也可能需要倾诉或找人陪伴

**选项3：抛出一个话题 (throw_topic)**
- 适用场景：历史消息中有未讨论完的话题、你有自己的想法、或者想深入聊某个主题
- 方式：明确提出一个话题，希望得到回应和讨论
- 心情影响：心情会影响你想聊的话题类型和语气

请以JSON格式回复你的决策：
{{
    "action": "do_nothing" | "simple_bubble" | "throw_topic",
    "reasoning": "你的决策理由，说明为什么选择这个行动（要结合你的心情和上次决策考虑）",
    "topic": "(仅当action=throw_topic时填写)你想抛出的具体话题"
}}

注意：
1. 如果最近聊天很活跃（不到1小时），倾向于选择 do_nothing
2. 如果你对这个环境兴趣不高(<0.4)，倾向于选择 do_nothing 或 simple_bubble
3. 考虑你的心情：心情会影响你的行动倾向和表达方式
4. 参考上次决策：避免重复相同的话题，也可以根据上次效果调整策略
3. 只有在真的有话题想聊时才选择 throw_topic
4. 符合你的人设，不要太过热情或冷淡
"""

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
            reply_prompt = await self._build_reply_prompt(context, action, topic)

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

    async def _build_reply_prompt(
        self, context: dict[str, Any], action: Literal["simple_bubble", "throw_topic"], topic: str | None
    ) -> str:
        """构建回复提示词"""
        # 获取表达方式参考
        expression_habits = await self._get_expression_habits(
            stream_id=context.get("stream_id", ""), chat_history=context.get("recent_chat_history", "")
        )

        if action == "simple_bubble":
            return f"""你是一个有着独特个性的AI助手。你的人设是：
{context["bot_personality"]}

现在是 {context["current_time"]}，你决定在 "{context["stream_name"]}" 中简单冒个泡。

【你当前的心情】
{context.get("current_mood", "感觉很平静")}

【聊天环境】
- 整体印象: {context["stream_impression"]}
- 聊天风格: {context["chat_style"]}

【最近的聊天记录】
{context["recent_chat_history"]}
{expression_habits}
请生成一条简短的消息，用于水群。要求：
1. 非常简短（5-15字）
2. 轻松随意，不要有明确的话题或问题
3. 可以是：问候、表达心情、随口一句话
4. 符合你的人设和当前聊天风格
5. **你的心情应该影响消息的内容和语气**（比如心情好时可能更活泼，心情不好时可能更低落）
6. 如果有表达方式参考，在合适时自然使用
7. 合理参考历史记录
直接输出消息内容，不要解释："""

        else:  # throw_topic
            return f"""你是一个有着独特个性的AI助手。你的人设是：
{context["bot_personality"]}

现在是 {context["current_time"]}，你决定在 "{context["stream_name"]}" 中抛出一个话题。

【你当前的心情】
{context.get("current_mood", "感觉很平静")}

【聊天环境】
- 整体印象: {context["stream_impression"]}
- 聊天风格: {context["chat_style"]}
- 常见话题: {context["topic_keywords"] or "暂无"}

【最近的聊天记录】
{context["recent_chat_history"]}

【你想抛出的话题】
{topic}
{expression_habits}
请根据这个话题生成一条消息，要求：
1. 明确提出话题，引导讨论
2. 长度适中（20-50字）
3. 自然地引入话题，不要生硬
4. 可以结合最近的聊天记录
5. 符合你的人设和当前聊天风格
6. **你的心情应该影响话题的选择和表达方式**（比如心情好时可能更积极，心情不好时可能需要倾诉或寻求安慰）
7. 如果有表达方式参考，在合适时自然使用

直接输出消息内容，不要解释："""

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
