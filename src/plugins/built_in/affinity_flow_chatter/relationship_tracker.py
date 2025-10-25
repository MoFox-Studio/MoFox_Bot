"""
用户关系追踪器
负责追踪用户交互历史，并通过LLM分析更新用户关系分
支持数据库持久化存储和回复后自动关系更新
"""

import random
import time

from sqlalchemy import desc, select

from src.common.data_models.database_data_model import DatabaseMessages
from src.common.database.sqlalchemy_database_api import get_db_session
from src.common.database.sqlalchemy_models import Messages, UserRelationships
from src.common.logger import get_logger
from src.config.config import global_config, model_config
from src.llm_models.utils_model import LLMRequest

logger = get_logger("chatter_relationship_tracker")


class ChatterRelationshipTracker:
    """用户关系追踪器"""

    def __init__(self, interest_scoring_system=None):
        self.tracking_users: dict[str, dict] = {}  # user_id -> interaction_data
        self.max_tracking_users = 3
        self.update_interval_minutes = 30
        self.last_update_time = time.time()
        self.relationship_history: list[dict] = []

        # 兼容性：保留参数但不直接使用，转而使用统一API
        self.interest_scoring_system = None  # 废弃，不再使用

        # 用户关系缓存 (user_id -> {"relationship_text": str, "relationship_score": float, "last_tracked": float})
        self.user_relationship_cache: dict[str, dict] = {}
        self.cache_expiry_hours = 1  # 缓存过期时间(小时)

        # 关系更新LLM
        try:
            self.relationship_llm = LLMRequest(
                model_set=model_config.model_task_config.relationship_tracker, request_type="relationship_tracker"
            )
        except AttributeError:
            # 如果relationship_tracker配置不存在，尝试其他可用的模型配置
            available_models = [
                attr
                for attr in dir(model_config.model_task_config)
                if not attr.startswith("_") and attr != "model_dump"
            ]

            if available_models:
                # 使用第一个可用的模型配置
                fallback_model = available_models[0]
                logger.warning(f"relationship_tracker model configuration not found, using fallback: {fallback_model}")
                self.relationship_llm = LLMRequest(
                    model_set=getattr(model_config.model_task_config, fallback_model),
                    request_type="relationship_tracker",
                )
            else:
                # 如果没有任何模型配置，创建一个简单的LLMRequest
                logger.warning("No model configurations found, creating basic LLMRequest")
                self.relationship_llm = LLMRequest(
                    model_set="gpt-3.5-turbo",  # 默认模型
                    request_type="relationship_tracker",
                )

    def set_interest_scoring_system(self, interest_scoring_system):
        """设置兴趣度评分系统引用（已废弃，使用统一API）"""
        # 不再需要设置，直接使用统一API
        logger.info("set_interest_scoring_system 已废弃，现在使用统一评分API")

    def add_interaction(self, user_id: str, user_name: str, user_message: str, bot_reply: str, reply_timestamp: float):
        """添加用户交互记录"""
        if len(self.tracking_users) >= self.max_tracking_users:
            # 移除最旧的记录
            oldest_user = min(
                self.tracking_users.keys(), key=lambda k: self.tracking_users[k].get("reply_timestamp", 0)
            )
            del self.tracking_users[oldest_user]

        # 获取当前关系分 - 使用缓存数据
        current_relationship_score = global_config.affinity_flow.base_relationship_score  # 默认值
        if user_id in self.user_relationship_cache:
            current_relationship_score = self.user_relationship_cache[user_id].get("relationship_score", current_relationship_score)

        self.tracking_users[user_id] = {
            "user_id": user_id,
            "user_name": user_name,
            "user_message": user_message,
            "bot_reply": bot_reply,
            "reply_timestamp": reply_timestamp,
            "current_relationship_score": current_relationship_score,
        }

        logger.debug(f"添加用户交互追踪: {user_id}")

    async def check_and_update_relationships(self) -> list[dict]:
        """检查并更新用户关系"""
        current_time = time.time()
        if current_time - self.last_update_time < self.update_interval_minutes * 60:
            return []

        updates = []
        for user_id, interaction in list(self.tracking_users.items()):
            if current_time - interaction["reply_timestamp"] > 60 * 5:  # 5分钟
                update = await self._update_user_relationship(interaction)
                if update:
                    updates.append(update)
                    del self.tracking_users[user_id]

        self.last_update_time = current_time
        return updates

    async def _update_user_relationship(self, interaction: dict) -> dict | None:
        """更新单个用户的关系"""
        try:
            # 获取bot人设信息
            from src.individuality.individuality import Individuality

            individuality = Individuality()
            bot_personality = await individuality.get_personality_block()

            prompt = f"""
你现在是一个有着特定性格和身份的AI助手。你的人设是：{bot_personality}

请以你独特的性格视角，严格按现实逻辑分析以下用户交互，更新用户关系：

用户ID: {interaction["user_id"]}
用户名: {interaction["user_name"]}
用户消息: {interaction["user_message"]}
你的回复: {interaction["bot_reply"]}
当前关系分: {interaction["current_relationship_score"]}

【重要】关系分数档次定义：
- 0.0-0.2：陌生人/初次认识 - 仅礼貌性交流
- 0.2-0.4：普通网友 - 有基本互动但不熟悉
- 0.4-0.6：熟悉网友 - 经常交流，有一定了解
- 0.6-0.8：朋友 - 可以分享心情，互相关心
- 0.8-1.0：好朋友/知己 - 深度信任，亲密无间

【严格要求】：
1. 加分必须符合现实关系发展逻辑 - 不能因为对方态度好就盲目加分到不符合当前关系档次的分数
2. 关系提升需要足够的互动积累和时间验证
3. 即使是朋友关系，单次互动加分通常不超过0.05-0.1
4. 人物印象描述应该是泛化的、整体的理解，从你的视角对用户整体性格特质的描述：
   - 描述用户的整体性格特点（如：温柔、幽默、理性、感性等）
   - 用户给你的整体感觉和印象
   - 你们关系的整体状态和氛围
   - 避免描述具体事件或对话内容，而是基于这些事件形成的整体认知

根据你的人设性格，思考：
1. 从你的性格视角，这个用户给你什么样的整体印象？
2. 用户的性格特质和行为模式是否符合你的喜好？
3. 基于这次互动，你对用户的整体认知有什么变化？
4. 这个用户在你心中的整体形象是怎样的？

请以JSON格式返回更新结果：
{{
    "new_relationship_score": 0.0~1.0的数值（必须符合现实逻辑）,
    "reasoning": "从你的性格角度说明更新理由，重点说明是否符合现实关系发展逻辑",
    "interaction_summary": "基于你性格的用户整体印象描述，包含用户的整体性格特质、给你的整体感觉，避免具体事件描述"
}}
"""

            # 调用LLM进行分析 - 添加超时保护
            import asyncio
            try:
                llm_response, _ = await asyncio.wait_for(
                    self.relationship_llm.generate_response_async(prompt=prompt),
                    timeout=30.0  # 30秒超时
                )
            except asyncio.TimeoutError:
                logger.warning(f"初次见面LLM调用超时: user_id={user_id}, 跳过此次追踪")
                return
            except Exception as e:
                logger.error(f"初次见面LLM调用失败: user_id={user_id}, 错误: {e}")
                return

            if llm_response:
                import json

                try:
                    # 清理LLM响应，移除可能的格式标记
                    cleaned_response = self._clean_llm_json_response(llm_response)
                    response_data = json.loads(cleaned_response)
                    new_score = max(
                        0.0,
                        min(
                            1.0,
                            float(
                                response_data.get(
                                    "new_relationship_score", global_config.affinity_flow.base_relationship_score
                                )
                            ),
                        ),
                    )

                    # 使用统一API更新关系分
                    from src.plugin_system.apis.scoring_api import scoring_api
                    await scoring_api.update_user_relationship(
                        interaction["user_id"], new_score
                    )

                    return {
                        "user_id": interaction["user_id"],
                        "new_relationship_score": new_score,
                        "reasoning": response_data.get("reasoning", ""),
                        "interaction_summary": response_data.get("interaction_summary", ""),
                    }

                except json.JSONDecodeError as e:
                    logger.error(f"LLM响应JSON解析失败: {e}")
                    logger.debug(f"LLM原始响应: {llm_response}")
                except Exception as e:
                    logger.error(f"处理关系更新数据失败: {e}")

        except Exception as e:
            logger.error(f"更新用户关系时出错: {e}")

        return None

    def get_tracking_users(self) -> dict[str, dict]:
        """获取正在追踪的用户"""
        return self.tracking_users.copy()

    def get_user_interaction(self, user_id: str) -> dict | None:
        """获取特定用户的交互记录"""
        return self.tracking_users.get(user_id)

    def remove_user_tracking(self, user_id: str):
        """移除用户追踪"""
        if user_id in self.tracking_users:
            del self.tracking_users[user_id]
            logger.debug(f"移除用户追踪: {user_id}")

    def clear_all_tracking(self):
        """清空所有追踪"""
        self.tracking_users.clear()
        logger.info("清空所有用户追踪")

    def get_relationship_history(self) -> list[dict]:
        """获取关系历史记录"""
        return self.relationship_history.copy()

    def add_to_history(self, relationship_update: dict):
        """添加到关系历史"""
        self.relationship_history.append({**relationship_update, "update_time": time.time()})

        # 限制历史记录数量
        if len(self.relationship_history) > 100:
            self.relationship_history = self.relationship_history[-100:]

    def get_tracker_stats(self) -> dict:
        """获取追踪器统计"""
        return {
            "tracking_users": len(self.tracking_users),
            "max_tracking_users": self.max_tracking_users,
            "update_interval_minutes": self.update_interval_minutes,
            "relationship_history": len(self.relationship_history),
            "last_update_time": self.last_update_time,
        }

    def update_config(self, max_tracking_users: int | None = None, update_interval_minutes: int | None = None):
        """更新配置"""
        if max_tracking_users is not None:
            self.max_tracking_users = max_tracking_users
            logger.info(f"更新最大追踪用户数: {max_tracking_users}")

        if update_interval_minutes is not None:
            self.update_interval_minutes = update_interval_minutes
            logger.info(f"更新关系更新间隔: {update_interval_minutes} 分钟")

    async def force_update_relationship(self, user_id: str, new_score: float, reasoning: str = ""):
        """强制更新用户关系分"""
        if user_id in self.tracking_users:
            current_score = self.tracking_users[user_id]["current_relationship_score"]

            # 使用统一API更新关系分
            from src.plugin_system.apis.scoring_api import scoring_api
            await scoring_api.update_user_relationship(user_id, new_score)

            update_info = {
                "user_id": user_id,
                "new_relationship_score": new_score,
                "reasoning": reasoning or "手动更新",
                "interaction_summary": "手动更新关系分",
            }
            self.add_to_history(update_info)
            logger.info(f"强制更新用户关系: {user_id} -> {new_score:.2f}")

    def get_user_summary(self, user_id: str) -> dict:
        """获取用户交互总结"""
        if user_id not in self.tracking_users:
            return {}

        interaction = self.tracking_users[user_id]
        return {
            "user_id": user_id,
            "user_name": interaction["user_name"],
            "current_relationship_score": interaction["current_relationship_score"],
            "interaction_count": 1,  # 简化版本，每次追踪只记录一次交互
            "last_interaction": interaction["reply_timestamp"],
            "recent_message": interaction["user_message"][:100] + "..."
            if len(interaction["user_message"]) > 100
            else interaction["user_message"],
        }

    # ===== 数据库支持方法 =====

    async def get_user_relationship_score(self, user_id: str) -> float:
        """获取用户关系分"""
        # 先检查缓存
        if user_id in self.user_relationship_cache:
            cache_data = self.user_relationship_cache[user_id]
            # 检查缓存是否过期
            cache_time = cache_data.get("last_tracked", 0)
            if time.time() - cache_time < self.cache_expiry_hours * 3600:
                return cache_data.get("relationship_score", global_config.affinity_flow.base_relationship_score)

        # 缓存过期或不存在，从数据库获取
        relationship_data = await self._get_user_relationship_from_db(user_id)
        if relationship_data:
            # 更新缓存
            self.user_relationship_cache[user_id] = {
                "relationship_text": relationship_data.get("relationship_text", ""),
                "relationship_score": relationship_data.get(
                    "relationship_score", global_config.affinity_flow.base_relationship_score
                ),
                "last_tracked": time.time(),
            }
            return relationship_data.get("relationship_score", global_config.affinity_flow.base_relationship_score)

        # 数据库中也没有，返回默认值
        return global_config.affinity_flow.base_relationship_score

    async def _get_user_relationship_from_db(self, user_id: str) -> dict | None:
        """从数据库获取用户关系数据"""
        try:
            async with get_db_session() as session:
                # 查询用户关系表
                stmt = select(UserRelationships).where(UserRelationships.user_id == user_id)
                result = await session.execute(stmt)
                relationship = result.scalar_one_or_none()

                if relationship:
                    return {
                        "relationship_text": relationship.relationship_text or "",
                        "relationship_score": float(relationship.relationship_score)
                        if relationship.relationship_score is not None
                        else 0.3,
                        "last_updated": relationship.last_updated,
                    }
        except Exception as e:
            logger.error(f"从数据库获取用户关系失败: {e}")

        return None

    async def _update_user_relationship_in_db(self, user_id: str, relationship_text: str, relationship_score: float):
        """更新数据库中的用户关系"""
        try:
            current_time = time.time()

            async with get_db_session() as session:
                # 检查是否已存在关系记录
                stmt = select(UserRelationships).where(UserRelationships.user_id == user_id)
                result = await session.execute(stmt)
                existing = result.scalar_one_or_none()

                if existing:
                    # 更新现有记录
                    existing.relationship_text = relationship_text
                    existing.relationship_score = relationship_score
                    existing.last_updated = current_time
                    existing.user_name = existing.user_name or user_id  # 更新用户名如果为空
                else:
                    # 插入新记录
                    new_relationship = UserRelationships(
                        user_id=user_id,
                        user_name=user_id,
                        relationship_text=relationship_text,
                        relationship_score=relationship_score,
                        last_updated=current_time,
                    )
                    session.add(new_relationship)

                await session.commit()
                logger.info(f"已更新数据库中用户关系: {user_id} -> 分数: {relationship_score:.3f}")

        except Exception as e:
            logger.error(f"更新数据库用户关系失败: {e}")

    # ===== 回复后关系追踪方法 =====

    async def track_reply_relationship(
        self, user_id: str, user_name: str, bot_reply_content: str, reply_timestamp: float
    ):
        """回复后关系追踪 - 主要入口点"""
        try:
            # 首先检查是否启用关系追踪
            if not global_config.affinity_flow.enable_relationship_tracking:
                logger.debug(f"🚫 [RelationshipTracker] 关系追踪系统已禁用，跳过用户 {user_id}")
                return

            # 概率筛选 - 减少API调用压力
            tracking_probability = global_config.affinity_flow.relationship_tracking_probability
            if random.random() > tracking_probability:
                logger.debug(
                    f"🎲 [RelationshipTracker] 概率筛选未通过 ({tracking_probability:.2f})，跳过用户 {user_id} 的关系追踪"
                )
                return

            logger.info(f"🔄 [RelationshipTracker] 开始回复后关系追踪: {user_id} (概率通过: {tracking_probability:.2f})")

            # 检查上次追踪时间 - 使用配置的冷却时间
            last_tracked_time = await self._get_last_tracked_time(user_id)
            cooldown_hours = global_config.affinity_flow.relationship_tracking_cooldown_hours
            cooldown_seconds = cooldown_hours * 3600
            time_diff = reply_timestamp - last_tracked_time

            # 使用配置的最小间隔时间
            min_interval = global_config.affinity_flow.relationship_tracking_interval_min
            required_interval = max(min_interval, cooldown_seconds)

            if time_diff < required_interval:
                logger.debug(
                    f"⏱️ [RelationshipTracker] 用户 {user_id} 距离上次追踪时间不足 {required_interval/60:.1f} 分钟 "
                    f"(实际: {time_diff/60:.1f} 分钟)，跳过"
                )
                return

            # 获取上次bot回复该用户的消息
            last_bot_reply = await self._get_last_bot_reply_to_user(user_id)
            if not last_bot_reply:
                logger.info(f"👋 [RelationshipTracker] 未找到用户 {user_id} 的历史回复记录，启动'初次见面'逻辑")
                await self._handle_first_interaction(user_id, user_name, bot_reply_content)
                return

            # 获取用户后续的反应消息
            user_reactions = await self._get_user_reactions_after_reply(user_id, last_bot_reply.time)
            logger.debug(f"💬 [RelationshipTracker] 找到用户 {user_id} 在上次回复后的 {len(user_reactions)} 条反应消息")

            # 获取当前关系数据
            current_relationship = await self._get_user_relationship_from_db(user_id)
            current_score = (
                current_relationship.get("relationship_score", global_config.affinity_flow.base_relationship_score)
                if current_relationship
                else global_config.affinity_flow.base_relationship_score
            )
            current_text = current_relationship.get("relationship_text", "新用户") if current_relationship else "新用户"

            # 使用LLM分析并更新关系
            logger.debug(f"🧠 [RelationshipTracker] 开始为用户 {user_id} 分析并更新关系")
            await self._analyze_and_update_relationship(
                user_id, user_name, last_bot_reply, user_reactions, current_text, current_score, bot_reply_content
            )

        except Exception as e:
            logger.error(f"回复后关系追踪失败: {e}")
            logger.debug("错误详情:", exc_info=True)

    async def _get_last_tracked_time(self, user_id: str) -> float:
        """获取上次追踪时间"""
        # 先检查缓存
        if user_id in self.user_relationship_cache:
            return self.user_relationship_cache[user_id].get("last_tracked", 0)

        # 从数据库获取
        relationship_data = await self._get_user_relationship_from_db(user_id)
        if relationship_data:
            return relationship_data.get("last_updated", 0)

        return 0

    async def _get_last_bot_reply_to_user(self, user_id: str) -> DatabaseMessages | None:
        """获取上次bot回复该用户的消息"""
        try:
            async with get_db_session() as session:
                # 查询bot回复给该用户的最新消息
                stmt = (
                    select(Messages)
                    .where(Messages.user_id == user_id)
                    .where(Messages.reply_to.isnot(None))
                    .order_by(desc(Messages.time))
                    .limit(1)
                )

                result = await session.execute(stmt)
                message = result.scalar_one_or_none()
                if message:
                    # 将SQLAlchemy模型转换为DatabaseMessages对象
                    return self._sqlalchemy_to_database_messages(message)

        except Exception as e:
            logger.error(f"获取上次回复消息失败: {e}")

        return None

    async def _get_user_reactions_after_reply(self, user_id: str, reply_time: float) -> list[DatabaseMessages]:
        """获取用户在bot回复后的反应消息"""
        try:
            async with get_db_session() as session:
                # 查询用户在回复时间之后的5分钟内的消息
                end_time = reply_time + 5 * 60  # 5分钟

                stmt = (
                    select(Messages)
                    .where(Messages.user_id == user_id)
                    .where(Messages.time > reply_time)
                    .where(Messages.time <= end_time)
                    .order_by(Messages.time)
                )

                result = await session.execute(stmt)
                messages = result.scalars().all()
                if messages:
                    return [self._sqlalchemy_to_database_messages(message) for message in messages]

        except Exception as e:
            logger.error(f"获取用户反应消息失败: {e}")

        return []

    def _sqlalchemy_to_database_messages(self, sqlalchemy_message) -> DatabaseMessages:
        """将SQLAlchemy消息模型转换为DatabaseMessages对象"""
        try:
            return DatabaseMessages(
                message_id=sqlalchemy_message.message_id or "",
                time=float(sqlalchemy_message.time) if sqlalchemy_message.time is not None else 0.0,
                chat_id=sqlalchemy_message.chat_id or "",
                reply_to=sqlalchemy_message.reply_to,
                processed_plain_text=sqlalchemy_message.processed_plain_text or "",
                user_id=sqlalchemy_message.user_id or "",
                user_nickname=sqlalchemy_message.user_nickname or "",
                user_platform=sqlalchemy_message.user_platform or "",
            )
        except Exception as e:
            logger.error(f"SQLAlchemy消息转换失败: {e}")
            # 返回一个基本的消息对象
            return DatabaseMessages(
                message_id="",
                time=0.0,
                chat_id="",
                processed_plain_text="",
                user_id="",
                user_nickname="",
                user_platform="",
            )

    async def _analyze_and_update_relationship(
        self,
        user_id: str,
        user_name: str,
        last_bot_reply: DatabaseMessages,
        user_reactions: list[DatabaseMessages],
        current_text: str,
        current_score: float,
        current_reply: str,
    ):
        """使用LLM分析并更新用户关系"""
        try:
            # 构建分析提示
            user_reactions_text = "\n".join([f"- {msg.processed_plain_text}" for msg in user_reactions])

            # 获取bot人设信息
            from src.individuality.individuality import Individuality

            individuality = Individuality()
            bot_personality = await individuality.get_personality_block()

            prompt = f"""
你现在是一个有着特定性格和身份的AI助手。你的人设是：{bot_personality}

请以你独特的性格视角，严格按现实逻辑分析以下用户交互，更新用户关系印象和分数：

用户信息:
- 用户ID: {user_id}
- 用户名: {user_name}

你上次的回复: {last_bot_reply.processed_plain_text}

用户反应消息:
{user_reactions_text}

你当前的回复: {current_reply}

当前关系印象: {current_text}
当前关系分数: {current_score:.3f}

【重要】关系分数档次定义：
- 0.0-0.2：陌生人/初次认识 - 仅礼貌性交流
- 0.2-0.4：普通网友 - 有基本互动但不熟悉
- 0.4-0.6：熟悉网友 - 经常交流，有一定了解
- 0.6-0.8：朋友 - 可以分享心情，互相关心
- 0.8-1.0：好朋友/知己 - 深度信任，亲密无间

【严格要求】：
1. 加分必须符合现实关系发展逻辑 - 不能因为用户反应好就盲目加分
2. 关系提升需要足够的互动积累和时间验证，单次互动加分通常不超过0.05-0.1
3. 必须考虑当前关系档次，不能跳跃式提升（比如从0.3直接到0.7）
4. 人物印象描述应该是泛化的、整体的理解（100-200字），从你的视角对用户整体性格特质的描述：
   - 描述用户的整体性格特点和行为模式（如：温柔体贴、幽默风趣、理性稳重等）
   - 用户给你的整体感觉和印象氛围
   - 你们关系的整体状态和发展阶段
   - 基于所有互动形成的用户整体形象认知
   - 避免提及具体事件或对话内容，而是总结形成的整体印象
5. 在撰写人物印象时，请根据已有信息自然地融入用户的性别。如果性别不确定，请使用中性描述。

性格视角深度分析:
1. 从你的性格视角，基于这次互动，你对用户的整体印象有什么新的认识？
2. 用户的整体性格特质和行为模式符合你的喜好吗？
3. 从现实角度看，这次互动是否足以让关系提升到下一个档次？为什么？
4. 基于你们的互动历史，用户在你心中的整体形象是怎样的？
5. 这个用户给你带来的整体感受和情绪体验是怎样的？

请以JSON格式返回更新结果:
{{
    "relationship_text": "泛化的用户整体印象描述(100-200字)，其中自然地体现用户的性别，包含用户的整体性格特质、给你的整体感觉和印象氛围，避免具体事件描述",
    "relationship_score": 0.0~1.0的新分数（必须严格符合现实逻辑）,
    "analysis_reasoning": "从你性格角度的深度分析，重点说明分数调整的现实合理性",
    "interaction_quality": "high/medium/low"
}}
"""

            # 调用LLM进行分析 - 添加超时保护
            import asyncio
            try:
                llm_response, _ = await asyncio.wait_for(
                    self.relationship_llm.generate_response_async(prompt=prompt),
                    timeout=30.0  # 30秒超时
                )
            except asyncio.TimeoutError:
                logger.warning(f"关系追踪LLM调用超时: user_id={user_id}, 跳过此次追踪")
                return
            except Exception as e:
                logger.error(f"关系追踪LLM调用失败: user_id={user_id}, 错误: {e}")
                return

            if llm_response:
                import json

                try:
                    # 清理LLM响应，移除可能的格式标记
                    cleaned_response = self._clean_llm_json_response(llm_response)
                    response_data = json.loads(cleaned_response)

                    new_text = response_data.get("relationship_text", current_text)
                    new_score = max(0.0, min(1.0, float(response_data.get("relationship_score", current_score))))
                    reasoning = response_data.get("analysis_reasoning", "")
                    quality = response_data.get("interaction_quality", "medium")

                    # 更新数据库
                    await self._update_user_relationship_in_db(user_id, new_text, new_score)

                    # 更新缓存
                    self.user_relationship_cache[user_id] = {
                        "relationship_text": new_text,
                        "relationship_score": new_score,
                        "last_tracked": time.time(),
                    }

                    # 使用统一API更新关系分（内存缓存已通过数据库更新自动处理）
                    # 数据库更新后，缓存会在下次访问时自动同步

                    # 记录分析历史
                    analysis_record = {
                        "user_id": user_id,
                        "timestamp": time.time(),
                        "old_score": current_score,
                        "new_score": new_score,
                        "old_text": current_text,
                        "new_text": new_text,
                        "reasoning": reasoning,
                        "quality": quality,
                        "user_reactions_count": len(user_reactions),
                    }
                    self.relationship_history.append(analysis_record)

                    # 限制历史记录数量
                    if len(self.relationship_history) > 100:
                        self.relationship_history = self.relationship_history[-100:]

                    logger.info(f"✅ 关系分析完成: {user_id}")
                    logger.info(f"   📝 印象: '{current_text}' -> '{new_text}'")
                    logger.info(f"   💝 分数: {current_score:.3f} -> {new_score:.3f}")
                    logger.info(f"   🎯 质量: {quality}")

                except json.JSONDecodeError as e:
                    logger.error(f"LLM响应JSON解析失败: {e}")
                    logger.debug(f"LLM原始响应: {llm_response}")
            else:
                logger.warning("LLM未返回有效响应")

        except Exception as e:
            logger.error(f"关系分析失败: {e}")
            logger.debug("错误详情:", exc_info=True)

    async def _handle_first_interaction(self, user_id: str, user_name: str, bot_reply_content: str):
        """处理与用户的初次交互"""
        try:
            # 初次交互也进行概率检查，但使用更高的通过率
            first_interaction_probability = min(1.0, global_config.affinity_flow.relationship_tracking_probability * 1.5)
            if random.random() > first_interaction_probability:
                logger.debug(
                    f"🎲 [RelationshipTracker] 初次交互概率筛选未通过 ({first_interaction_probability:.2f})，跳过用户 {user_id}"
                )
                return

            logger.info(f"✨ [RelationshipTracker] 正在处理与用户 {user_id} 的初次交互 (概率通过: {first_interaction_probability:.2f})")

            # 获取bot人设信息
            from src.individuality.individuality import Individuality

            individuality = Individuality()
            bot_personality = await individuality.get_personality_block()

            prompt = f"""
你现在是：{bot_personality}

你正在与一个新用户进行初次有效互动。请根据你对TA的第一印象，建立初始关系档案。

用户信息:
- 用户ID: {user_id}
- 用户名: {user_name}

你的首次回复: {bot_reply_content}

【严格要求】：
1. 建立一个初始关系分数，通常在0.2-0.4之间（普通网友）。
2. 初始关系印象描述要简洁地记录你对用户的整体初步看法（50-100字）。请在描述中自然地融入你对用户性别的初步判断（例如“他似乎是...”或“感觉她...”），如果完全无法判断，则使用中性描述。
   - 基于用户名和初次互动，用户给你的整体感觉
   - 你感受到的用户整体性格特质倾向
   - 你对与这个用户建立关系的整体期待和感觉
   - 避免描述具体的事件细节，而是整体的直觉印象

请以JSON格式返回结果:
{{
    "relationship_text": "简洁的用户整体初始印象描述(50-100字)，其中自然地体现对用户性别的初步判断",
    "relationship_score": 0.2~0.4的新分数,
    "analysis_reasoning": "从你性格角度说明建立此初始印象的理由"
}}
"""
            # 调用LLM进行分析
            llm_response, _ = await self.relationship_llm.generate_response_async(prompt=prompt)
            if not llm_response:
                logger.warning(f"初次交互分析时LLM未返回有效响应: {user_id}")
                return

            import json

            cleaned_response = self._clean_llm_json_response(llm_response)
            response_data = json.loads(cleaned_response)

            new_text = response_data.get("relationship_text", "初次见面")
            new_score = max(
                0.0,
                min(
                    1.0,
                    float(response_data.get("relationship_score", global_config.affinity_flow.base_relationship_score)),
                ),
            )

            # 更新数据库和缓存
            await self._update_user_relationship_in_db(user_id, new_text, new_score)
            self.user_relationship_cache[user_id] = {
                "relationship_text": new_text,
                "relationship_score": new_score,
                "last_tracked": time.time(),
            }

            logger.info(f"✅ [RelationshipTracker] 已成功为新用户 {user_id} 建立初始关系档案，分数为 {new_score:.3f}")

        except Exception as e:
            logger.error(f"处理初次交互失败: {user_id}, 错误: {e}")
            logger.debug("错误详情:", exc_info=True)

    def _clean_llm_json_response(self, response: str) -> str:
        """
        清理LLM响应，移除可能的JSON格式标记

        Args:
            response: LLM原始响应

        Returns:
            清理后的JSON字符串
        """
        try:
            import re

            # 移除常见的JSON格式标记
            cleaned = response.strip()

            # 移除 ```json 或 ``` 等标记
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.MULTILINE | re.IGNORECASE)
            cleaned = re.sub(r"\s*```$", "", cleaned, flags=re.MULTILINE)

            # 移除可能的Markdown代码块标记
            cleaned = re.sub(r"^`|`$", "", cleaned, flags=re.MULTILINE)

            # 尝试找到JSON对象的开始和结束
            json_start = cleaned.find("{")
            json_end = cleaned.rfind("}")

            if json_start != -1 and json_end != -1 and json_end > json_start:
                # 提取JSON部分
                cleaned = cleaned[json_start : json_end + 1]

            # 移除多余的空白字符
            cleaned = cleaned.strip()

            logger.debug(f"LLM响应清理: 原始长度={len(response)}, 清理后长度={len(cleaned)}")
            if cleaned != response:
                logger.debug(f"清理前: {response[:200]}...")
                logger.debug(f"清理后: {cleaned[:200]}...")

            return cleaned

        except Exception as e:
            logger.warning(f"清理LLM响应失败: {e}")
            return response  # 清理失败时返回原始响应
