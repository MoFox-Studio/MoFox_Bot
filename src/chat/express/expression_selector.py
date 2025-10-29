import asyncio
import hashlib
import random
import time
from typing import Any

import orjson
from json_repair import repair_json
from sqlalchemy import select

from src.chat.utils.prompt import Prompt, global_prompt_manager
from src.common.database.sqlalchemy_database_api import get_db_session
from src.common.database.sqlalchemy_models import Expression
from src.common.logger import get_logger
from src.config.config import global_config, model_config
from src.llm_models.utils_model import LLMRequest

# 导入StyleLearner管理器
from .style_learner import style_learner_manager

logger = get_logger("expression_selector")


def init_prompt():
    expression_evaluation_prompt = """
以下是正在进行的聊天内容：
{chat_observe_info}

你的名字是{bot_name}{target_message}

以下是可选的表达情境：
{all_situations}

请你分析聊天内容的语境、情绪、话题类型，从上述情境中选择最适合当前聊天情境的{min_num}-{max_num}个情境。
考虑因素包括：
1. 聊天的情绪氛围（轻松、严肃、幽默等）
2. 话题类型（日常、技术、游戏、情感等）
3. 情境与当前语境的匹配度
{target_message_extra_block}

请以JSON格式输出，只需要输出选中的情境编号：
例如：
{{
    "selected_situations": [2, 3, 5, 7, 19, 22, 25, 38, 39, 45, 48, 64]
}}

请严格按照JSON格式输出，不要包含其他内容：
"""
    Prompt(expression_evaluation_prompt, "expression_evaluation_prompt")


def weighted_sample(population: list[dict], weights: list[float], k: int) -> list[dict]:
    """按权重随机抽样"""
    if not population or not weights or k <= 0:
        return []

    if len(population) <= k:
        return population.copy()

    # 使用累积权重的方法进行加权抽样
    selected = []
    population_copy = population.copy()
    weights_copy = weights.copy()

    for _ in range(k):
        if not population_copy:
            break

        # 选择一个元素
        chosen_idx = random.choices(range(len(population_copy)), weights=weights_copy)[0]
        selected.append(population_copy.pop(chosen_idx))
        weights_copy.pop(chosen_idx)

    return selected


class ExpressionSelector:
    def __init__(self, chat_id: str = ""):
        self.chat_id = chat_id
        self.llm_model = LLMRequest(
            model_set=model_config.model_task_config.utils_small, request_type="expression.selector"
        )

    @staticmethod
    def can_use_expression_for_chat(chat_id: str) -> bool:
        """
        检查指定聊天流是否允许使用表达

        Args:
            chat_id: 聊天流ID

        Returns:
            bool: 是否允许使用表达
        """
        try:
            use_expression, _, _ = global_config.expression.get_expression_config_for_chat(chat_id)
            return use_expression
        except Exception as e:
            logger.error(f"检查表达使用权限失败: {e}")
            return False

    @staticmethod
    def _parse_stream_config_to_chat_id(stream_config_str: str) -> str | None:
        """解析'platform:id:type'为chat_id（与get_stream_id一致）"""
        try:
            parts = stream_config_str.split(":")
            if len(parts) != 3:
                return None
            platform = parts[0]
            id_str = parts[1]
            stream_type = parts[2]
            is_group = stream_type == "group"
            if is_group:
                components = [platform, str(id_str)]
            else:
                components = [platform, str(id_str), "private"]
            key = "_".join(components)
            return hashlib.md5(key.encode()).hexdigest()
        except Exception:
            return None

    def get_related_chat_ids(self, chat_id: str) -> list[str]:
        """根据expression.rules配置，获取与当前chat_id相关的所有chat_id（包括自身）"""
        rules = global_config.expression.rules
        current_group = None

        # 找到当前chat_id所在的组
        for rule in rules:
            if rule.chat_stream_id and self._parse_stream_config_to_chat_id(rule.chat_stream_id) == chat_id:
                current_group = rule.group
                break

        if not current_group:
            return [chat_id]

        # 找出同一组的所有chat_id
        related_chat_ids = []
        for rule in rules:
            if rule.group == current_group and rule.chat_stream_id:
                if chat_id_candidate := self._parse_stream_config_to_chat_id(rule.chat_stream_id):
                    related_chat_ids.append(chat_id_candidate)

        return related_chat_ids if related_chat_ids else [chat_id]

    async def get_random_expressions(
        self, chat_id: str, total_num: int, style_percentage: float, grammar_percentage: float
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        # sourcery skip: extract-duplicate-method, move-assign
        # 支持多chat_id合并抽选
        related_chat_ids = self.get_related_chat_ids(chat_id)
        async with get_db_session() as session:
            # 优化：一次性查询所有相关chat_id的表达方式
            style_query = await session.execute(
                select(Expression).where((Expression.chat_id.in_(related_chat_ids)) & (Expression.type == "style"))
            )
            grammar_query = await session.execute(
                select(Expression).where((Expression.chat_id.in_(related_chat_ids)) & (Expression.type == "grammar"))
            )

            style_exprs = [
                {
                    "situation": expr.situation,
                    "style": expr.style,
                    "count": expr.count,
                    "last_active_time": expr.last_active_time,
                    "source_id": expr.chat_id,
                    "type": "style",
                    "create_date": expr.create_date if expr.create_date is not None else expr.last_active_time,
                }
                for expr in style_query.scalars()
            ]

            grammar_exprs = [
                {
                    "situation": expr.situation,
                    "style": expr.style,
                    "count": expr.count,
                    "last_active_time": expr.last_active_time,
                    "source_id": expr.chat_id,
                    "type": "grammar",
                    "create_date": expr.create_date if expr.create_date is not None else expr.last_active_time,
                }
                for expr in grammar_query.scalars()
            ]

            style_num = int(total_num * style_percentage)
            grammar_num = int(total_num * grammar_percentage)
            # 按权重抽样（使用count作为权重）
            if style_exprs:
                style_weights = [expr.get("count", 1) for expr in style_exprs]
                selected_style = weighted_sample(style_exprs, style_weights, style_num)
            else:
                selected_style = []
            if grammar_exprs:
                grammar_weights = [expr.get("count", 1) for expr in grammar_exprs]
                selected_grammar = weighted_sample(grammar_exprs, grammar_weights, grammar_num)
            else:
                selected_grammar = []

            return selected_style, selected_grammar

    @staticmethod
    async def update_expressions_count_batch(expressions_to_update: list[dict[str, Any]], increment: float = 0.1):
        """对一批表达方式更新count值，按chat_id+type分组后一次性写入数据库"""
        if not expressions_to_update:
            return
        updates_by_key = {}
        for expr in expressions_to_update:
            source_id: str = expr.get("source_id")  # type: ignore
            expr_type: str = expr.get("type", "style")
            situation: str = expr.get("situation")  # type: ignore
            style: str = expr.get("style")  # type: ignore
            if not source_id or not situation or not style:
                logger.warning(f"表达方式缺少必要字段，无法更新: {expr}")
                continue
            key = (source_id, expr_type, situation, style)
            if key not in updates_by_key:
                updates_by_key[key] = expr
        for chat_id, expr_type, situation, style in updates_by_key:
            async with get_db_session() as session:
                query = await session.execute(
                    select(Expression).where(
                        (Expression.chat_id == chat_id)
                        & (Expression.type == expr_type)
                        & (Expression.situation == situation)
                        & (Expression.style == style)
                    )
                )
                query = query.scalar()
                if query:
                    expr_obj = query
                    current_count = expr_obj.count
                    new_count = min(current_count + increment, 5.0)
                    expr_obj.count = new_count
                    expr_obj.last_active_time = time.time()

                    logger.debug(
                        f"表达方式激活: 原count={current_count:.3f}, 增量={increment}, 新count={new_count:.3f} in db"
                    )
                await session.commit()

    async def select_suitable_expressions(
        self,
        chat_id: str,
        chat_history: list | str,
        target_message: str | None = None,
        max_num: int = 10,
        min_num: int = 5,
    ) -> list[dict[str, Any]]:
        """
        统一的表达方式选择入口，根据配置自动选择模式
        
        Args:
            chat_id: 聊天ID
            chat_history: 聊天历史（列表或字符串）
            target_message: 目标消息
            max_num: 最多返回数量
            min_num: 最少返回数量
            
        Returns:
            选中的表达方式列表
        """
        # 转换chat_history为字符串
        if isinstance(chat_history, list):
            chat_info = "\n".join([f"{msg.get('sender', 'Unknown')}: {msg.get('content', '')}" for msg in chat_history])
        else:
            chat_info = chat_history
        
        # 根据配置选择模式
        mode = global_config.expression.mode
        logger.debug(f"[ExpressionSelector] 使用模式: {mode}")
        
        if mode == "exp_model":
            return await self._select_expressions_model_only(
                chat_id=chat_id,
                chat_info=chat_info,
                target_message=target_message,
                max_num=max_num,
                min_num=min_num
            )
        else:  # classic mode
            return await self._select_expressions_classic(
                chat_id=chat_id,
                chat_info=chat_info,
                target_message=target_message,
                max_num=max_num,
                min_num=min_num
            )
    
    async def _select_expressions_classic(
        self,
        chat_id: str,
        chat_info: str,
        target_message: str | None = None,
        max_num: int = 10,
        min_num: int = 5,
    ) -> list[dict[str, Any]]:
        """经典模式：随机抽样 + LLM评估"""
        logger.debug(f"[Classic模式] 使用LLM评估表达方式")
        return await self.select_suitable_expressions_llm(
            chat_id=chat_id,
            chat_info=chat_info,
            max_num=max_num,
            min_num=min_num,
            target_message=target_message
        )
    
    async def _select_expressions_model_only(
        self,
        chat_id: str,
        chat_info: str,
        target_message: str | None = None,
        max_num: int = 10,
        min_num: int = 5,
    ) -> list[dict[str, Any]]:
        """模型预测模式：使用StyleLearner预测最合适的表达风格"""
        logger.debug(f"[Exp_model模式] 使用StyleLearner预测表达方式")
        
        # 检查是否允许在此聊天流中使用表达
        if not self.can_use_expression_for_chat(chat_id):
            logger.debug(f"聊天流 {chat_id} 不允许使用表达，返回空列表")
            return []
        
        # 获取或创建StyleLearner实例
        learner = style_learner_manager.get_learner(chat_id)
        
        # 使用StyleLearner预测最合适的风格
        best_style, all_scores = learner.predict_style(chat_info, top_k=max_num)
        
        if not best_style or not all_scores:
            logger.warning(f"StyleLearner未返回预测结果（可能模型未训练），回退到经典模式")
            return await self._select_expressions_classic(
                chat_id=chat_id,
                chat_info=chat_info,
                target_message=target_message,
                max_num=max_num,
                min_num=min_num
            )
        
        # 将分数字典转换为列表格式 [(style, score), ...]
        predicted_styles = sorted(all_scores.items(), key=lambda x: x[1], reverse=True)
        
        # 根据预测的风格从数据库获取表达方式
        expressions = await self.get_model_predicted_expressions(
            chat_id=chat_id,
            predicted_styles=predicted_styles,
            max_num=max_num
        )
        
        if not expressions:
            logger.warning(f"未找到匹配预测风格的表达方式，回退到经典模式")
            return await self._select_expressions_classic(
                chat_id=chat_id,
                chat_info=chat_info,
                target_message=target_message,
                max_num=max_num,
                min_num=min_num
            )
        
        logger.debug(f"[Exp_model模式] 成功返回 {len(expressions)} 个表达方式")
        return expressions
    
    async def get_model_predicted_expressions(
        self,
        chat_id: str,
        predicted_styles: list[tuple[str, float]],
        max_num: int = 10
    ) -> list[dict[str, Any]]:
        """
        根据StyleLearner预测的风格获取表达方式
        
        Args:
            chat_id: 聊天ID
            predicted_styles: 预测的风格列表，格式: [(style, score), ...]
            max_num: 最多返回数量
            
        Returns:
            表达方式列表
        """
        if not predicted_styles:
            return []
        
        # 提取风格名称（前3个最佳匹配）
        style_names = [style for style, _ in predicted_styles[:min(3, len(predicted_styles))]]
        logger.debug(f"预测最佳风格: {style_names[0] if style_names else 'None'}, Top3分数: {predicted_styles[:3]}")
        
        async with get_db_session() as session:
            # 查询匹配这些风格的表达方式
            stmt = (
                select(Expression)
                .where(Expression.chat_id == chat_id)
                .where(Expression.style.in_(style_names))
                .order_by(Expression.count.desc())
                .limit(max_num)
            )
            result = await session.execute(stmt)
            expressions_objs = result.scalars().all()
            
            if not expressions_objs:
                logger.debug(f"数据库中没有找到风格 {style_names} 的表达方式")
                return []
            
            # 转换为字典格式
            expressions = []
            for expr in expressions_objs:
                expressions.append({
                    "situation": expr.situation or "",
                    "style": expr.style or "",
                    "type": expr.type or "style",
                    "count": float(expr.count) if expr.count else 0.0,
                    "last_active_time": expr.last_active_time or 0.0
                })
            
            logger.debug(f"从数据库获取了 {len(expressions)} 个表达方式")
            return expressions

    async def select_suitable_expressions_llm(
        self,
        chat_id: str,
        chat_info: str,
        max_num: int = 10,
        min_num: int = 5,
        target_message: str | None = None,
    ) -> list[dict[str, Any]]:
        # sourcery skip: inline-variable, list-comprehension
        """使用LLM选择适合的表达方式"""

        # 检查是否允许在此聊天流中使用表达
        if not self.can_use_expression_for_chat(chat_id):
            logger.debug(f"聊天流 {chat_id} 不允许使用表达，返回空列表")
            return []

        # 1. 获取35个随机表达方式（现在按权重抽取）
        style_exprs, grammar_exprs = await self.get_random_expressions(chat_id, 30, 0.5, 0.5)

        # 2. 构建所有表达方式的索引和情境列表
        all_expressions = []
        all_situations = []

        # 添加style表达方式
        for expr in style_exprs:
            if isinstance(expr, dict) and "situation" in expr and "style" in expr:
                expr_with_type = expr.copy()
                expr_with_type["type"] = "style"
                all_expressions.append(expr_with_type)
                all_situations.append(f"{len(all_expressions)}.{expr['situation']}")

        # 添加grammar表达方式
        for expr in grammar_exprs:
            if isinstance(expr, dict) and "situation" in expr and "style" in expr:
                expr_with_type = expr.copy()
                expr_with_type["type"] = "grammar"
                all_expressions.append(expr_with_type)
                all_situations.append(f"{len(all_expressions)}.{expr['situation']}")

        if not all_expressions:
            logger.warning("没有找到可用的表达方式")
            return []

        all_situations_str = "\n".join(all_situations)

        if target_message:
            target_message_str = f"，现在你想要回复消息：{target_message}"
            target_message_extra_block = "4.考虑你要回复的目标消息"
        else:
            target_message_str = ""
            target_message_extra_block = ""

        # 3. 构建prompt（只包含情境，不包含完整的表达方式）
        prompt = (await global_prompt_manager.get_prompt_async("expression_evaluation_prompt")).format(
            bot_name=global_config.bot.nickname,
            chat_observe_info=chat_info,
            all_situations=all_situations_str,
            min_num=min_num,
            max_num=max_num,
            target_message=target_message_str,
            target_message_extra_block=target_message_extra_block,
        )

        # print(prompt)

        # 4. 调用LLM
        try:
            # start_time = time.time()
            content, (reasoning_content, model_name, _) = await self.llm_model.generate_response_async(prompt=prompt)

            if not content:
                logger.warning("LLM返回空结果")
                return []

            # 5. 解析结果
            result = repair_json(content)
            if isinstance(result, str):
                result = orjson.loads(result)

            if not isinstance(result, dict) or "selected_situations" not in result:
                logger.error("LLM返回格式错误")
                logger.info(f"LLM返回结果: \n{content}")
                return []

            selected_indices = result["selected_situations"]

            # 根据索引获取完整的表达方式
            valid_expressions = []
            for idx in selected_indices:
                if isinstance(idx, int) and 1 <= idx <= len(all_expressions):
                    expression = all_expressions[idx - 1]  # 索引从1开始
                    valid_expressions.append(expression)

            # 对选中的所有表达方式，一次性更新count数
            if valid_expressions:
                asyncio.create_task(self.update_expressions_count_batch(valid_expressions, 0.006))

            # logger.info(f"LLM从{len(all_expressions)}个情境中选择了{len(valid_expressions)}个")
            return valid_expressions

        except Exception as e:
            logger.error(f"LLM处理表达方式选择时出错: {e}")
            return []


init_prompt()

try:
    expression_selector = ExpressionSelector()
except Exception as e:
    print(f"ExpressionSelector初始化失败: {e}")
