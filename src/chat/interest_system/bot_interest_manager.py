"""
机器人兴趣标签管理系统
基于人设生成兴趣标签，并使用embedding计算匹配度
"""

import traceback
from datetime import datetime
from typing import Any

import numpy as np
from sqlalchemy import select

from src.common.config_helpers import resolve_embedding_dimension
from src.common.data_models.bot_interest_data_model import BotInterestTag, BotPersonalityInterests, InterestMatchResult
from src.common.logger import get_logger
from src.config.config import global_config
from src.utils.json_parser import extract_and_parse_json

logger = get_logger("bot_interest_manager")


class BotInterestManager:
    """机器人兴趣标签管理器"""

    def __init__(self):
        self.current_interests: BotPersonalityInterests | None = None
        self.embedding_cache: dict[str, list[float]] = {}  # embedding缓存
        self.expanded_tag_cache: dict[str, str] = {}  # 扩展标签缓存
        self.expanded_embedding_cache: dict[str, list[float]] = {}  # 扩展标签的embedding缓存
        self._initialized = False

        # Embedding客户端配置
        self.embedding_request = None
        self.embedding_config = None
        configured_dim = resolve_embedding_dimension()
        self.embedding_dimension = int(configured_dim) if configured_dim else 0
        self._detected_embedding_dimension: int | None = None

    @property
    def is_initialized(self) -> bool:
        """检查兴趣系统是否已初始化"""
        return self._initialized

    async def initialize(self, personality_description: str, personality_id: str = "default"):
        """初始化兴趣标签系统"""
        try:
            logger.info("机器人兴趣系统开始初始化...")
            logger.info(f"人设ID: {personality_id}, 描述长度: {len(personality_description)}")

            # 初始化embedding模型
            await self._initialize_embedding_model()

            # 检查embedding客户端是否成功初始化
            if not self.embedding_request:
                raise RuntimeError("Embedding客户端初始化失败")

            # 生成或加载兴趣标签
            await self._load_or_generate_interests(personality_description, personality_id)

            self._initialized = True

            # 检查是否成功获取兴趣标签
            if self.current_interests and len(self.current_interests.get_active_tags()) > 0:
                active_tags_count = len(self.current_interests.get_active_tags())
                logger.info("机器人兴趣系统初始化完成！")
                logger.info(f"当前已激活 {active_tags_count} 个兴趣标签, Embedding缓存 {len(self.embedding_cache)} 个")
            else:
                raise RuntimeError("未能成功加载或生成兴趣标签")

        except Exception as e:
            logger.error(f"机器人兴趣系统初始化失败: {e}")
            traceback.print_exc()
            raise  # 重新抛出异常，不允许降级初始化

    async def _initialize_embedding_model(self):
        """初始化embedding模型"""
        logger.info("🔧 正在配置embedding客户端...")

        # 使用项目配置的embedding模型
        from src.config.config import model_config
        from src.llm_models.utils_model import LLMRequest

        logger.debug("✅ 成功导入embedding相关模块")

        # 检查embedding配置是否存在
        if not hasattr(model_config.model_task_config, "embedding"):
            raise RuntimeError("❌ 未找到embedding模型配置")

        logger.info("📋 找到embedding模型配置")
        self.embedding_config = model_config.model_task_config.embedding

        if self.embedding_dimension:
            logger.info(f"📐 配置的embedding维度: {self.embedding_dimension}")
        else:
            logger.info("📐 未在配置中检测到embedding维度，将根据首次返回的向量自动识别")

        # 创建LLMRequest实例用于embedding
        self.embedding_request = LLMRequest(model_set=self.embedding_config, request_type="interest_embedding")
        logger.info("✅ Embedding请求客户端初始化成功")
        logger.info(f"🔗 客户端类型: {type(self.embedding_request).__name__}")

        # 获取第一个embedding模型的ModelInfo
        if hasattr(self.embedding_config, "model_list") and self.embedding_config.model_list:
            first_model_name = self.embedding_config.model_list[0]
            logger.info(f"🎯 使用embedding模型: {first_model_name}")
        else:
            logger.warning("⚠️  未找到embedding模型列表")

        logger.info("✅ Embedding模型初始化完成")

    async def _load_or_generate_interests(self, personality_description: str, personality_id: str):
        """加载或生成兴趣标签"""
        logger.info(f"📚 正在为 '{personality_id}' 加载或生成兴趣标签...")

        # 首先尝试从数据库加载
        logger.info("尝试从数据库加载兴趣标签...")
        loaded_interests = await self._load_interests_from_database(personality_id)

        if loaded_interests:
            self.current_interests = loaded_interests
            active_count = len(loaded_interests.get_active_tags())
            logger.info(f"成功从数据库加载 {active_count} 个兴趣标签 (版本: {loaded_interests.version})")
            tags_info = [f"  - '{tag.tag_name}' (权重: {tag.weight:.2f})" for tag in loaded_interests.get_active_tags()]
            tags_str = "\n".join(tags_info)
            logger.info(f"当前兴趣标签:\n{tags_str}")

            # 为加载的标签生成embedding（数据库不存储embedding，启动时动态生成）
            logger.info("🧠 为加载的标签生成embedding向量...")
            await self._generate_embeddings_for_tags(loaded_interests)
        else:
            # 生成新的兴趣标签
            logger.info("数据库中未找到兴趣标签，开始生成...")
            generated_interests = await self._generate_interests_from_personality(
                personality_description, personality_id
            )

            if generated_interests:
                self.current_interests = generated_interests
                active_count = len(generated_interests.get_active_tags())
                logger.info(f"成功生成 {active_count} 个新兴趣标签。")
                tags_info = [
                    f"  - '{tag.tag_name}' (权重: {tag.weight:.2f})" for tag in generated_interests.get_active_tags()
                ]
                tags_str = "\n".join(tags_info)
                logger.info(f"当前兴趣标签:\n{tags_str}")

                # 保存到数据库
                logger.info("正在保存至数据库...")
                await self._save_interests_to_database(generated_interests)
            else:
                raise RuntimeError("❌ 兴趣标签生成失败")

    async def _generate_interests_from_personality(
        self, personality_description: str, personality_id: str
    ) -> BotPersonalityInterests | None:
        """根据人设生成兴趣标签"""
        try:
            logger.info("🎨 开始根据人设生成兴趣标签...")
            logger.info(f"📝 人设长度: {len(personality_description)} 字符")

            # 检查embedding客户端是否可用
            if not hasattr(self, "embedding_request"):
                raise RuntimeError("❌ Embedding客户端未初始化，无法生成兴趣标签")

            # 构建提示词
            logger.info("📝 构建LLM提示词...")
            prompt = f"""
基于以下机器人人设描述，生成一套合适的兴趣标签：

人设描述：
{personality_description}

请生成一系列兴趣关键词标签，要求：
1. 标签应该符合人设特点和性格
2. 每个标签都有权重（0.1-1.0），表示对该兴趣的喜好程度
3. 生成15-25个不等的标签
4. 每个标签包含两个部分：
   - name: 简短的标签名（2-6个字符），用于显示和管理，如"Python"、"追番"、"撸猫"
   - expanded: 完整的描述性文本（20-50个字符），用于语义匹配，描述这个兴趣的具体内容和场景
5. expanded 扩展描述要求：
   - 必须是完整的句子或短语，包含丰富的语义信息
   - 描述具体的对话场景、活动内容、相关话题
   - 避免过于抽象，要有明确的语境
   - 示例：
     * "Python" -> "讨论Python编程语言、写Python代码、Python脚本开发、Python技术问题"
     * "追番" -> "讨论正在播出的动漫番剧、追番进度、动漫剧情、番剧推荐、动漫角色"
     * "撸猫" -> "讨论猫咪宠物、晒猫分享、萌宠日常、可爱猫猫、养猫心得"
     * "社恐" -> "表达社交焦虑、不想见人、想躲起来、害怕社交的心情"
     * "深夜码代码" -> "深夜写代码、熬夜编程、夜猫子程序员、深夜调试bug"

请以JSON格式返回，格式如下：
{{
    "interests": [
        {{
            "name": "Python",
            "expanded": "讨论Python编程语言、写Python代码、Python脚本开发、Python技术问题",
            "weight": 0.9
        }},
        {{
            "name": "追番",
            "expanded": "讨论正在播出的动漫番剧、追番进度、动漫剧情、番剧推荐、动漫角色",
            "weight": 0.85
        }},
        {{
            "name": "撸猫",
            "expanded": "讨论猫咪宠物、晒猫分享、萌宠日常、可爱猫猫、养猫心得",
            "weight": 0.95
        }}
    ]
}}

注意：
- name: 简短标签名，2-6个字符，方便显示
- expanded: 完整描述，20-50个字符，用于精准的语义匹配
- weight: 权重范围0.1-1.0，权重越高表示越感兴趣
- 根据人设生成个性化、具体的标签和描述
- expanded 描述要有具体场景，避免泛化
"""

            # 调用LLM生成兴趣标签
            logger.info("🤖 正在调用LLM生成兴趣标签...")
            response = await self._call_llm_for_interest_generation(prompt)

            if not response:
                raise RuntimeError("❌ LLM未返回有效响应")

            logger.info("✅ LLM响应成功，开始解析兴趣标签...")
            # 使用统一的 JSON 解析工具
            interests_data = extract_and_parse_json(response, strict=False)
            if not interests_data or not isinstance(interests_data, dict):
                raise RuntimeError("❌ 解析LLM响应失败，未获取到有效的JSON数据")

            bot_interests = BotPersonalityInterests(
                personality_id=personality_id, personality_description=personality_description
            )

            # 解析生成的兴趣标签
            interests_list = interests_data.get("interests", [])
            logger.info(f"📋 解析到 {len(interests_list)} 个兴趣标签")

            for i, tag_data in enumerate(interests_list):
                tag_name = tag_data.get("name", f"标签_{i}")
                weight = tag_data.get("weight", 0.5)
                expanded = tag_data.get("expanded")  # 获取扩展描述

                # 检查标签长度，如果过长则截断
                if len(tag_name) > 10:
                    logger.warning(f"⚠️ 标签 '{tag_name}' 过长，将截断为10个字符")
                    tag_name = tag_name[:10]

                # 验证扩展描述
                if expanded:
                    logger.debug(f"   🏷️  {tag_name} (权重: {weight:.2f})")
                    logger.debug(f"      📝 扩展: {expanded}")
                else:
                    logger.warning(f"   ⚠️ 标签 '{tag_name}' 缺少扩展描述，将使用回退方案")

                tag = BotInterestTag(tag_name=tag_name, weight=weight, expanded=expanded)
                bot_interests.interest_tags.append(tag)

            # 为所有标签生成embedding
            logger.info("🧠 开始为兴趣标签生成embedding向量...")
            await self._generate_embeddings_for_tags(bot_interests)

            logger.info("✅ 兴趣标签生成完成")
            return bot_interests

        except Exception as e:
            logger.error(f"❌ 根据人设生成兴趣标签失败: {e}")
            traceback.print_exc()
            raise

    async def _call_llm_for_interest_generation(self, prompt: str) -> str | None:
        """调用LLM生成兴趣标签"""
        try:
            logger.info("🔧 配置LLM客户端...")

            # 使用llm_api来处理请求
            from src.config.config import model_config
            from src.plugin_system.apis import llm_api

            # 构建完整的提示词，明确要求只返回纯JSON
            full_prompt = f"""你是一个专业的机器人人设分析师，擅长根据人设描述生成合适的兴趣标签。

{prompt}

请确保返回格式为有效的JSON，不要包含任何额外的文本、解释或代码块标记。只返回JSON对象本身。"""

            # 使用replyer模型配置
            replyer_config = model_config.model_task_config.replyer

            # 调用LLM API
            logger.info("🚀 正在通过LLM API发送请求...")
            success, response, reasoning_content, model_name = await llm_api.generate_with_model(
                prompt=full_prompt,
                model_config=replyer_config,
                request_type="interest_generation",
                temperature=0.7,
                max_tokens=2000,
            )

            if success and response:
                logger.info(f"✅ LLM响应成功，模型: {model_name}, 响应长度: {len(response)} 字符")
                logger.debug(
                    f"📄 LLM响应内容: {response[:200]}..." if len(response) > 200 else f"📄 LLM响应内容: {response}"
                )
                if reasoning_content:
                    logger.debug(f"🧠 推理内容: {reasoning_content[:100]}...")

                # 直接返回原始响应，后续使用统一的 JSON 解析工具
                return response
            else:
                logger.warning("⚠️ LLM返回空响应或调用失败")
                return None

        except Exception as e:
            logger.error(f"❌ 调用LLM生成兴趣标签失败: {e}")
            logger.error("🔍 错误详情:")
            traceback.print_exc()
            return None

    async def _generate_embeddings_for_tags(self, interests: BotPersonalityInterests):
        """为所有兴趣标签生成embedding（缓存在内存和文件中）"""
        if not hasattr(self, "embedding_request"):
            raise RuntimeError("❌ Embedding客户端未初始化，无法生成embedding")

        total_tags = len(interests.interest_tags)

        # 尝试从文件加载缓存
        file_cache = await self._load_embedding_cache_from_file(interests.personality_id)
        if file_cache:
            logger.info(f"📂 从文件加载 {len(file_cache)} 个embedding缓存")
            self.embedding_cache.update(file_cache)

        logger.info(f"🧠 开始为 {total_tags} 个兴趣标签生成embedding向量...")

        memory_cached_count = 0
        file_cached_count = 0
        generated_count = 0
        failed_count = 0

        for i, tag in enumerate(interests.interest_tags, 1):
            if tag.tag_name in self.embedding_cache:
                # 使用缓存的embedding（可能来自内存或文件）
                tag.embedding = self.embedding_cache[tag.tag_name]
                if file_cache and tag.tag_name in file_cache:
                    file_cached_count += 1
                    logger.debug(f"   [{i}/{total_tags}] 📂 '{tag.tag_name}' - 使用文件缓存")
                else:
                    memory_cached_count += 1
                    logger.debug(f"   [{i}/{total_tags}] 💾 '{tag.tag_name}' - 使用内存缓存")
            else:
                # 动态生成新的embedding
                embedding_text = tag.tag_name

                logger.debug(f"   [{i}/{total_tags}] 🔄 正在为 '{tag.tag_name}' 动态生成embedding...")
                embedding = await self._get_embedding(embedding_text)

                if embedding:
                    tag.embedding = embedding  # 设置到 tag 对象（内存中）
                    self.embedding_cache[tag.tag_name] = embedding  # 同时缓存到内存
                    generated_count += 1
                    logger.debug(f"   ✅ '{tag.tag_name}' embedding动态生成成功")
                else:
                    failed_count += 1
                    logger.warning(f"   ❌ '{tag.tag_name}' embedding生成失败")

        if failed_count > 0:
            raise RuntimeError(f"❌ 有 {failed_count} 个兴趣标签embedding生成失败")

        # 如果有新生成的embedding，保存到文件
        if generated_count > 0:
            await self._save_embedding_cache_to_file(interests.personality_id)
            logger.info(f"💾 已将 {generated_count} 个新生成的embedding保存到缓存文件")

        interests.last_updated = datetime.now()
        logger.info("=" * 50)
        logger.info("✅ Embedding生成完成!")
        logger.info(f"📊 总标签数: {total_tags}")
        logger.info(f"� 文件缓存命中: {file_cached_count}")
        logger.info(f"�💾 内存缓存命中: {memory_cached_count}")
        logger.info(f"🆕 新生成: {generated_count}")
        logger.info(f"❌ 失败: {failed_count}")
        logger.info(f"🗃️  总缓存大小: {len(self.embedding_cache)}")
        logger.info("=" * 50)

    async def _get_embedding(self, text: str) -> list[float]:
        """获取文本的embedding向量"""
        if not hasattr(self, "embedding_request"):
            raise RuntimeError("❌ Embedding请求客户端未初始化")

        # 检查缓存
        if text in self.embedding_cache:
            logger.debug(f"💾 使用缓存的embedding: '{text[:30]}...'")
            return self.embedding_cache[text]

        # 使用LLMRequest获取embedding
        logger.debug(f"🔄 正在获取embedding: '{text[:30]}...'")
        if not self.embedding_request:
            raise RuntimeError("❌ Embedding客户端未初始化")
        embedding, model_name = await self.embedding_request.get_embedding(text)

        if embedding and len(embedding) > 0:
            self.embedding_cache[text] = embedding

            current_dim = len(embedding)
            if self._detected_embedding_dimension is None:
                self._detected_embedding_dimension = current_dim
                if self.embedding_dimension and self.embedding_dimension != current_dim:
                    logger.warning(
                        "⚠️ 实际embedding维度(%d)与配置值(%d)不一致，请在 model_config.model_task_config.embedding.embedding_dimension 中同步更新",
                        current_dim,
                        self.embedding_dimension,
                    )
                else:
                    self.embedding_dimension = current_dim
                logger.info(f"📏 检测到embedding维度: {current_dim}")
            elif current_dim != self.embedding_dimension:
                logger.warning(
                    "⚠️ 收到的embedding维度发生变化: 之前=%d, 当前=%d。请确认模型配置是否正确。",
                    self.embedding_dimension,
                    current_dim,
                )

            logger.debug(f"✅ Embedding获取成功，维度: {current_dim}, 模型: {model_name}")
            return embedding
        else:
            raise RuntimeError(f"❌ 返回的embedding为空: {embedding}")

    async def _generate_message_embedding(self, message_text: str, keywords: list[str]) -> list[float]:
        """为消息生成embedding向量"""
        # 组合消息文本和关键词作为embedding输入
        if keywords:
            combined_text = f"{message_text} {' '.join(keywords)}"
        else:
            combined_text = message_text

        logger.debug(f"🔄 正在为消息生成embedding，输入长度: {len(combined_text)}")

        # 生成embedding
        embedding = await self._get_embedding(combined_text)
        logger.debug(f"✅ 消息embedding生成成功，维度: {len(embedding)}")
        return embedding

    async def _calculate_similarity_scores(
        self, result: InterestMatchResult, message_embedding: list[float], keywords: list[str]
    ):
        """计算消息与兴趣标签的相似度分数"""
        try:
            if not self.current_interests:
                return

            active_tags = self.current_interests.get_active_tags()
            if not active_tags:
                return

            logger.debug(f"🔍 开始计算与 {len(active_tags)} 个兴趣标签的相似度")

            for tag in active_tags:
                if tag.embedding:
                    # 计算余弦相似度
                    similarity = self._calculate_cosine_similarity(message_embedding, tag.embedding)
                    weighted_score = similarity * tag.weight

                    # 设置相似度阈值为0.3
                    if similarity > 0.3:
                        result.add_match(tag.tag_name, weighted_score, keywords)
                        logger.debug(
                            f"   🏷️  '{tag.tag_name}': 相似度={similarity:.3f}, 权重={tag.weight:.2f}, 加权分数={weighted_score:.3f}"
                        )

        except Exception as e:
            logger.error(f"❌ 计算相似度分数失败: {e}")

    async def calculate_interest_match(
        self, message_text: str, keywords: list[str] | None = None
    ) -> InterestMatchResult:
        """计算消息与机器人兴趣的匹配度（优化版 - 标签扩展策略）

        核心优化：将短标签扩展为完整的描述性句子，解决语义粒度不匹配问题

        原问题：
        - 消息: "今天天气不错" (完整句子)
        - 标签: "蹭人治愈" (2-4字短语)
        - 结果: 误匹配，因为短标签的 embedding 过于抽象

        解决方案：
        - 标签扩展: "蹭人治愈" -> "表达亲近、寻求安慰、撒娇的内容"
        - 现在是: 句子 vs 句子，匹配更准确
        """
        if not self.current_interests or not self._initialized:
            raise RuntimeError("❌ 兴趣标签系统未初始化")

        logger.debug(f"开始计算兴趣匹配度: 消息长度={len(message_text)}, 关键词数={len(keywords) if keywords else 0}")

        message_id = f"msg_{datetime.now().timestamp()}"
        result = InterestMatchResult(message_id=message_id)

        # 获取活跃的兴趣标签
        active_tags = self.current_interests.get_active_tags()
        if not active_tags:
            raise RuntimeError("没有检测到活跃的兴趣标签")

        logger.debug(f"正在与 {len(active_tags)} 个兴趣标签进行匹配...")

        # 生成消息的embedding
        logger.debug("正在生成消息 embedding...")
        message_embedding = await self._get_embedding(message_text)
        logger.debug(f"消息 embedding 生成成功, 维度: {len(message_embedding)}")

        # 计算与每个兴趣标签的相似度（使用扩展标签）
        match_count = 0
        high_similarity_count = 0
        medium_similarity_count = 0
        low_similarity_count = 0

        # 分级相似度阈值 - 优化后可以提高阈值，因为匹配更准确了
        affinity_config = global_config.affinity_flow
        high_threshold = affinity_config.high_match_interest_threshold
        medium_threshold = affinity_config.medium_match_interest_threshold
        low_threshold = affinity_config.low_match_interest_threshold

        logger.debug(f"🔍 使用分级相似度阈值: 高={high_threshold}, 中={medium_threshold}, 低={low_threshold}")

        for tag in active_tags:
            if tag.embedding:
                # 🔧 优化：获取扩展标签的 embedding（带缓存）
                expanded_embedding = await self._get_expanded_tag_embedding(tag.tag_name)

                if expanded_embedding:
                    # 使用扩展标签的 embedding 进行匹配
                    similarity = self._calculate_cosine_similarity(message_embedding, expanded_embedding)

                    # 同时计算原始标签的相似度作为参考
                    original_similarity = self._calculate_cosine_similarity(message_embedding, tag.embedding)

                    # 混合策略：扩展标签权重更高（70%），原始标签作为补充（30%）
                    # 这样可以兼顾准确性（扩展）和灵活性（原始）
                    final_similarity = similarity * 0.7 + original_similarity * 0.3

                    logger.debug(f"标签'{tag.tag_name}': 原始={original_similarity:.3f}, 扩展={similarity:.3f}, 最终={final_similarity:.3f}")
                else:
                    # 如果扩展 embedding 获取失败，使用原始 embedding
                    final_similarity = self._calculate_cosine_similarity(message_embedding, tag.embedding)
                    logger.debug(f"标签'{tag.tag_name}': 使用原始相似度={final_similarity:.3f}")

                # 基础加权分数
                weighted_score = final_similarity * tag.weight

                # 根据相似度等级应用不同的加成
                if final_similarity > high_threshold:
                    # 高相似度：强加成
                    enhanced_score = weighted_score * affinity_config.high_match_keyword_multiplier
                    match_count += 1
                    high_similarity_count += 1
                    result.add_match(tag.tag_name, enhanced_score, [tag.tag_name])

                elif final_similarity > medium_threshold:
                    # 中相似度：中等加成
                    enhanced_score = weighted_score * affinity_config.medium_match_keyword_multiplier
                    match_count += 1
                    medium_similarity_count += 1
                    result.add_match(tag.tag_name, enhanced_score, [tag.tag_name])

                elif final_similarity > low_threshold:
                    # 低相似度：轻微加成
                    enhanced_score = weighted_score * affinity_config.low_match_keyword_multiplier
                    match_count += 1
                    low_similarity_count += 1
                    result.add_match(tag.tag_name, enhanced_score, [tag.tag_name])

        logger.debug(
            f"匹配统计: {match_count}/{len(active_tags)} 个标签命中 | "
            f"高(>{high_threshold}): {high_similarity_count}, "
            f"中(>{medium_threshold}): {medium_similarity_count}, "
            f"低(>{low_threshold}): {low_similarity_count}"
        )

        # 添加直接关键词匹配奖励
        keyword_bonus = self._calculate_keyword_match_bonus(keywords or [], result.matched_tags)
        logger.debug(f"🎯 关键词直接匹配奖励: {keyword_bonus}")

        # 应用关键词奖励到匹配分数
        for tag_name in result.matched_tags:
            if tag_name in keyword_bonus:
                original_score = result.match_scores[tag_name]
                bonus = keyword_bonus[tag_name]
                result.match_scores[tag_name] = original_score + bonus
                logger.debug(
                    f"   🏷️  '{tag_name}': 原始分数={original_score:.3f}, 奖励={bonus:.3f}, 最终分数={result.match_scores[tag_name]:.3f}"
                )

        # 计算总体分数
        result.calculate_overall_score()

        # 确定最佳匹配标签
        if result.matched_tags:
            top_tag_name = max(result.match_scores.items(), key=lambda x: x[1])[0]
            result.top_tag = top_tag_name
            logger.debug(f"最佳匹配: '{top_tag_name}' (分数: {result.match_scores[top_tag_name]:.3f})")

        logger.debug(
            f"最终结果: 总分={result.overall_score:.3f}, 置信度={result.confidence:.3f}, 匹配标签数={len(result.matched_tags)}"
        )

        # 如果有新生成的扩展embedding，保存到缓存文件
        if hasattr(self, "_new_expanded_embeddings_generated") and self._new_expanded_embeddings_generated:
            await self._save_embedding_cache_to_file(self.current_interests.personality_id)
            self._new_expanded_embeddings_generated = False
            logger.debug("💾 已保存新生成的扩展embedding到缓存文件")

        return result

    async def _get_expanded_tag_embedding(self, tag_name: str) -> list[float] | None:
        """获取扩展标签的 embedding（带缓存）

        优先使用缓存，如果没有则生成并缓存
        """
        # 检查缓存
        if tag_name in self.expanded_embedding_cache:
            return self.expanded_embedding_cache[tag_name]

        # 扩展标签
        expanded_tag = self._expand_tag_for_matching(tag_name)

        # 生成 embedding
        try:
            embedding = await self._get_embedding(expanded_tag)
            if embedding:
                # 缓存结果
                self.expanded_tag_cache[tag_name] = expanded_tag
                self.expanded_embedding_cache[tag_name] = embedding
                self._new_expanded_embeddings_generated = True  # 标记有新生成的embedding
                logger.debug(f"✅ 为标签'{tag_name}'生成并缓存扩展embedding: {expanded_tag[:50]}...")
                return embedding
        except Exception as e:
            logger.warning(f"为标签'{tag_name}'生成扩展embedding失败: {e}")

        return None

    def _expand_tag_for_matching(self, tag_name: str) -> str:
        """将短标签扩展为完整的描述性句子

        这是解决"标签太短导致误匹配"的核心方法

        策略：
        1. 优先使用 LLM 生成的 expanded 字段（最准确）
        2. 如果没有，使用基于规则的回退方案
        3. 最后使用通用模板

        示例：
        - "Python" + expanded -> "讨论Python编程语言、写Python代码、Python脚本开发、Python技术问题"
        - "蹭人治愈" + expanded -> "想要获得安慰、寻求温暖关怀、撒娇卖萌、表达亲昵、求抱抱求陪伴的对话"
        """
        # 使用缓存
        if tag_name in self.expanded_tag_cache:
            return self.expanded_tag_cache[tag_name]

        # 🎯 优先策略：使用 LLM 生成的 expanded 字段
        if self.current_interests:
            for tag in self.current_interests.interest_tags:
                if tag.tag_name == tag_name and tag.expanded:
                    logger.debug(f"✅ 使用LLM生成的扩展描述: {tag_name} -> {tag.expanded[:50]}...")
                    self.expanded_tag_cache[tag_name] = tag.expanded
                    return tag.expanded

        # 🔧 回退策略：基于规则的扩展（用于兼容旧数据或LLM未生成扩展的情况）
        logger.debug(f"⚠️ 标签'{tag_name}'没有LLM扩展描述，使用规则回退方案")
        tag_lower = tag_name.lower()

        # 技术编程类标签（具体化描述）
        if any(word in tag_lower for word in ["python", "java", "code", "代码", "编程", "脚本", "算法", "开发"]):
            if "python" in tag_lower:
                return "讨论Python编程语言、写Python代码、Python脚本开发、Python技术问题"
            elif "算法" in tag_lower:
                return "讨论算法题目、数据结构、编程竞赛、刷LeetCode题目、代码优化"
            elif "代码" in tag_lower or "被窝" in tag_lower:
                return "讨论写代码、编程开发、代码实现、技术方案、编程技巧"
            else:
                return "讨论编程开发、软件技术、代码编写、技术实现"

        # 情感表达类标签（具体化为真实对话场景）
        elif any(word in tag_lower for word in ["治愈", "撒娇", "安慰", "呼噜", "蹭", "卖萌"]):
            return "想要获得安慰、寻求温暖关怀、撒娇卖萌、表达亲昵、求抱抱求陪伴的对话"

        # 游戏娱乐类标签（具体游戏场景）
        elif any(word in tag_lower for word in ["游戏", "网游", "mmo", "游", "玩"]):
            return "讨论网络游戏、MMO游戏、游戏玩法、组队打副本、游戏攻略心得"

        # 动漫影视类标签（具体观看行为）
        elif any(word in tag_lower for word in ["番", "动漫", "视频", "b站", "弹幕", "追番", "云新番"]):
            # 特别处理"云新番" - 它的意思是在网上看新动漫，不是泛泛的"新东西"
            if "云" in tag_lower or "新番" in tag_lower:
                return "讨论正在播出的新动漫、新番剧集、动漫剧情、追番心得、动漫角色"
            else:
                return "讨论动漫番剧内容、B站视频、弹幕文化、追番体验"

        # 社交平台类标签（具体平台行为）
        elif any(word in tag_lower for word in ["小红书", "贴吧", "论坛", "社区", "吃瓜", "八卦"]):
            if "吃瓜" in tag_lower:
                return "聊八卦爆料、吃瓜看热闹、网络热点事件、社交平台热议话题"
            else:
                return "讨论社交平台内容、网络社区话题、论坛讨论、分享生活"

        # 生活日常类标签（具体萌宠场景）
        elif any(word in tag_lower for word in ["猫", "宠物", "尾巴", "耳朵", "毛绒"]):
            return "讨论猫咪宠物、晒猫分享、萌宠日常、可爱猫猫、养猫心得"

        # 状态心情类标签（具体情绪状态）
        elif any(word in tag_lower for word in ["社恐", "隐身", "流浪", "深夜", "被窝"]):
            if "社恐" in tag_lower:
                return "表达社交焦虑、不想见人、想躲起来、害怕社交的心情"
            elif "深夜" in tag_lower:
                return "深夜睡不着、熬夜、夜猫子、深夜思考人生的对话"
            else:
                return "表达当前心情状态、个人感受、生活状态"

        # 物品装备类标签（具体使用场景）
        elif any(word in tag_lower for word in ["键盘", "耳机", "装备", "设备"]):
            return "讨论键盘耳机装备、数码产品、使用体验、装备推荐评测"

        # 互动关系类标签
        elif any(word in tag_lower for word in ["拾风", "互怼", "互动"]):
            return "聊天互动、开玩笑、友好互怼、日常对话交流"

        # 默认：尽量具体化
        else:
            return f"明确讨论{tag_name}这个特定主题的具体内容和相关话题"

    def _calculate_keyword_match_bonus(self, keywords: list[str], matched_tags: list[str]) -> dict[str, float]:
        """计算关键词直接匹配奖励"""
        if not keywords or not matched_tags:
            return {}

        affinity_config = global_config.affinity_flow
        bonus_dict = {}

        for tag_name in matched_tags:
            bonus = 0.0

            # 检查关键词与标签的直接匹配
            for keyword in keywords:
                keyword_lower = keyword.lower().strip()
                tag_name_lower = tag_name.lower()

                # 完全匹配
                if keyword_lower == tag_name_lower:
                    bonus += affinity_config.high_match_interest_threshold * 0.6  # 使用高匹配阈值的60%作为完全匹配奖励
                    logger.debug(
                        f"   🎯 关键词完全匹配: '{keyword}' == '{tag_name}' (+{affinity_config.high_match_interest_threshold * 0.6:.3f})"
                    )

                # 包含匹配
                elif keyword_lower in tag_name_lower or tag_name_lower in keyword_lower:
                    bonus += (
                        affinity_config.medium_match_interest_threshold * 0.3
                    )  # 使用中匹配阈值的30%作为包含匹配奖励
                    logger.debug(
                        f"   🎯 关键词包含匹配: '{keyword}' ⊃ '{tag_name}' (+{affinity_config.medium_match_interest_threshold * 0.3:.3f})"
                    )

                # 部分匹配（编辑距离）
                elif self._calculate_partial_match(keyword_lower, tag_name_lower):
                    bonus += affinity_config.low_match_interest_threshold * 0.4  # 使用低匹配阈值的40%作为部分匹配奖励
                    logger.debug(
                        f"   🎯 关键词部分匹配: '{keyword}' ≈ '{tag_name}' (+{affinity_config.low_match_interest_threshold * 0.4:.3f})"
                    )

            if bonus > 0:
                bonus_dict[tag_name] = min(bonus, affinity_config.max_match_bonus)  # 使用配置的最大奖励限制

        return bonus_dict

    def _calculate_partial_match(self, text1: str, text2: str) -> bool:
        """计算部分匹配（基于编辑距离）"""
        try:
            # 简单的编辑距离计算
            max_len = max(len(text1), len(text2))
            if max_len == 0:
                return False

            # 计算编辑距离
            distance = self._levenshtein_distance(text1, text2)

            # 如果编辑距离小于较短字符串长度的一半，认为是部分匹配
            min_len = min(len(text1), len(text2))
            return distance <= min_len // 2

        except Exception:
            return False

    def _levenshtein_distance(self, s1: str, s2: str) -> int:
        """计算莱文斯坦距离"""
        if len(s1) < len(s2):
            return self._levenshtein_distance(s2, s1)

        if len(s2) == 0:
            return len(s1)

        previous_row = range(len(s2) + 1)
        for i, c1 in enumerate(s1):
            current_row = [i + 1]
            for j, c2 in enumerate(s2):
                insertions = previous_row[j + 1] + 1
                deletions = current_row[j] + 1
                substitutions = previous_row[j] + (c1 != c2)
                current_row.append(min(insertions, deletions, substitutions))
            previous_row = current_row

        return previous_row[-1]

    def _calculate_cosine_similarity(self, vec1: list[float], vec2: list[float]) -> float:
        """计算余弦相似度"""
        try:
            np_vec1 = np.array(vec1)
            np_vec2 = np.array(vec2)

            dot_product = np.dot(np_vec1, np_vec2)
            norm1 = np.linalg.norm(np_vec1)
            norm2 = np.linalg.norm(np_vec2)

            if norm1 == 0 or norm2 == 0:
                return 0.0

            similarity = dot_product / (norm1 * norm2)
            return float(similarity)

        except Exception as e:
            logger.error(f"计算余弦相似度失败: {e}")
            return 0.0

    async def _load_interests_from_database(self, personality_id: str) -> BotPersonalityInterests | None:
        """从数据库加载兴趣标签"""
        try:
            logger.debug(f"从数据库加载兴趣标签, personality_id: {personality_id}")

            # 导入SQLAlchemy相关模块
            import orjson

            from src.common.database.compatibility import get_db_session
            from src.common.database.core.models import BotPersonalityInterests as DBBotPersonalityInterests

            async with get_db_session() as session:
                # 查询最新的兴趣标签配置
                db_interests = (
                    (
                        await session.execute(
                            select(DBBotPersonalityInterests)
                            .where(DBBotPersonalityInterests.personality_id == personality_id)
                            .order_by(
                                DBBotPersonalityInterests.version.desc(), DBBotPersonalityInterests.last_updated.desc()
                            )
                        )
                    )
                    .scalars()
                    .first()
                )

                if db_interests:
                    logger.debug(f"在数据库中找到兴趣标签配置, 版本: {db_interests.version}")
                    logger.debug(f"📅 最后更新时间: {db_interests.last_updated}")
                    logger.debug(f"🧠 使用的embedding模型: {db_interests.embedding_model}")

                    # 解析JSON格式的兴趣标签
                    try:
                        tags_data = orjson.loads(db_interests.interest_tags)
                        logger.debug(f"🏷️  解析到 {len(tags_data)} 个兴趣标签")

                        # 创建BotPersonalityInterests对象
                        interests = BotPersonalityInterests(
                            personality_id=db_interests.personality_id,
                            personality_description=db_interests.personality_description,
                            embedding_model=db_interests.embedding_model,
                            version=db_interests.version,
                            last_updated=db_interests.last_updated,
                        )

                        # 解析兴趣标签（embedding 从数据库加载后会被忽略，因为我们不再存储它）
                        for tag_data in tags_data:
                            tag = BotInterestTag(
                                tag_name=tag_data.get("tag_name", ""),
                                weight=tag_data.get("weight", 0.5),
                                expanded=tag_data.get("expanded"),  # 加载扩展描述
                                created_at=datetime.fromisoformat(
                                    tag_data.get("created_at", datetime.now().isoformat())
                                ),
                                updated_at=datetime.fromisoformat(
                                    tag_data.get("updated_at", datetime.now().isoformat())
                                ),
                                is_active=tag_data.get("is_active", True),
                                embedding=None,  # 不再从数据库加载 embedding，改为动态生成
                            )
                            interests.interest_tags.append(tag)

                        logger.debug(f"成功解析 {len(interests.interest_tags)} 个兴趣标签（embedding 将在初始化时动态生成）")
                        return interests

                    except (orjson.JSONDecodeError, Exception) as e:
                        logger.error(f"❌ 解析兴趣标签JSON失败: {e}")
                        logger.debug(f"🔍 原始JSON数据: {db_interests.interest_tags[:200]}...")
                        return None
                else:
                    logger.info(f"ℹ️ 数据库中未找到personality_id为 '{personality_id}' 的兴趣标签配置")
                    return None

        except Exception as e:
            logger.error(f"❌ 从数据库加载兴趣标签失败: {e}")
            logger.error("🔍 错误详情:")
            traceback.print_exc()
            return None

    async def _save_interests_to_database(self, interests: BotPersonalityInterests):
        """保存兴趣标签到数据库"""
        try:
            logger.info("💾 正在保存兴趣标签到数据库...")
            logger.info(f"📋 personality_id: {interests.personality_id}")
            logger.info(f"🏷️  兴趣标签数量: {len(interests.interest_tags)}")
            logger.info(f"🔄 版本: {interests.version}")

            # 导入SQLAlchemy相关模块
            import orjson

            from src.common.database.compatibility import get_db_session
            from src.common.database.core.models import BotPersonalityInterests as DBBotPersonalityInterests

            # 将兴趣标签转换为JSON格式（不再保存embedding，启动时动态生成）
            tags_data = []
            for tag in interests.interest_tags:
                tag_dict = {
                    "tag_name": tag.tag_name,
                    "weight": tag.weight,
                    "expanded": tag.expanded,  # 保存扩展描述
                    "created_at": tag.created_at.isoformat(),
                    "updated_at": tag.updated_at.isoformat(),
                    "is_active": tag.is_active,
                    # embedding 不再存储到数据库，改为内存缓存
                }
                tags_data.append(tag_dict)

            # 序列化为JSON
            json_data = orjson.dumps(tags_data)

            async with get_db_session() as session:
                # 检查是否已存在相同personality_id的记录
                existing_record = (
                    (
                        await session.execute(
                            select(DBBotPersonalityInterests).where(
                                DBBotPersonalityInterests.personality_id == interests.personality_id
                            )
                        )
                    )
                    .scalars()
                    .first()
                )

                if existing_record:
                    # 更新现有记录
                    logger.info("🔄 更新现有的兴趣标签配置")
                    existing_record.interest_tags = json_data.decode("utf-8")
                    existing_record.personality_description = interests.personality_description
                    existing_record.embedding_model = interests.embedding_model
                    existing_record.version = interests.version
                    existing_record.last_updated = interests.last_updated

                    logger.info(f"✅ 成功更新兴趣标签配置，版本: {interests.version}")

                else:
                    # 创建新记录
                    logger.info("🆕 创建新的兴趣标签配置")
                    new_record = DBBotPersonalityInterests(
                        personality_id=interests.personality_id,
                        personality_description=interests.personality_description,
                        interest_tags=json_data.decode("utf-8"),
                        embedding_model=interests.embedding_model,
                        version=interests.version,
                        last_updated=interests.last_updated,
                    )
                    session.add(new_record)
                    await session.commit()
                    logger.info(f"✅ 成功创建兴趣标签配置，版本: {interests.version}")

            logger.info("✅ 兴趣标签已成功保存到数据库")

            # 验证保存是否成功
            async with get_db_session() as session:
                saved_record = (
                    (
                        await session.execute(
                            select(DBBotPersonalityInterests).where(
                                DBBotPersonalityInterests.personality_id == interests.personality_id
                            )
                        )
                    )
                    .scalars()
                    .first()
                )
                if saved_record:
                    logger.info(f"✅ 验证成功：数据库中存在personality_id为 {interests.personality_id} 的记录")
                    logger.info(f"   版本: {saved_record.version}")
                    logger.info(f"   最后更新: {saved_record.last_updated}")
                else:
                    logger.error(f"❌ 验证失败：数据库中未找到personality_id为 {interests.personality_id} 的记录")

        except Exception as e:
            logger.error(f"❌ 保存兴趣标签到数据库失败: {e}")
            logger.error("🔍 错误详情:")
            traceback.print_exc()

    async def _load_embedding_cache_from_file(self, personality_id: str) -> dict[str, list[float]] | None:
        """从文件加载embedding缓存"""
        try:
            from pathlib import Path

            import orjson

            cache_dir = Path("data/embedding")
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file = cache_dir / f"{personality_id}_embeddings.json"

            if not cache_file.exists():
                logger.debug(f"📂 Embedding缓存文件不存在: {cache_file}")
                return None

            # 读取缓存文件
            with open(cache_file, "rb") as f:
                cache_data = orjson.loads(f.read())

            # 验证缓存版本和embedding模型
            cache_version = cache_data.get("version", 1)
            cache_embedding_model = cache_data.get("embedding_model", "")
            current_embedding_model = self.embedding_config.model_list[0] if hasattr(self.embedding_config, "model_list") else ""

            if cache_embedding_model != current_embedding_model:
                logger.warning(f"⚠️ Embedding模型已变更 ({cache_embedding_model} → {current_embedding_model})，忽略旧缓存")
                return None

            embeddings = cache_data.get("embeddings", {})

            # 同时加载扩展标签的embedding缓存
            expanded_embeddings = cache_data.get("expanded_embeddings", {})
            if expanded_embeddings:
                self.expanded_embedding_cache.update(expanded_embeddings)
                logger.info(f"📂 加载 {len(expanded_embeddings)} 个扩展标签embedding缓存")

            logger.info(f"✅ 成功从文件加载 {len(embeddings)} 个标签embedding缓存 (版本: {cache_version}, 模型: {cache_embedding_model})")
            return embeddings

        except Exception as e:
            logger.warning(f"⚠️ 加载embedding缓存文件失败: {e}")
            return None

    async def _save_embedding_cache_to_file(self, personality_id: str):
        """保存embedding缓存到文件（包括扩展标签的embedding）"""
        try:
            from datetime import datetime
            from pathlib import Path

            import orjson

            cache_dir = Path("data/embedding")
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file = cache_dir / f"{personality_id}_embeddings.json"

            # 准备缓存数据
            current_embedding_model = self.embedding_config.model_list[0] if hasattr(self.embedding_config, "model_list") and self.embedding_config.model_list else ""
            cache_data = {
                "version": 1,
                "personality_id": personality_id,
                "embedding_model": current_embedding_model,
                "last_updated": datetime.now().isoformat(),
                "embeddings": self.embedding_cache,
                "expanded_embeddings": self.expanded_embedding_cache,  # 同时保存扩展标签的embedding
            }

            # 写入文件
            with open(cache_file, "wb") as f:
                f.write(orjson.dumps(cache_data, option=orjson.OPT_INDENT_2))

            logger.debug(f"💾 已保存 {len(self.embedding_cache)} 个标签embedding和 {len(self.expanded_embedding_cache)} 个扩展embedding到缓存文件: {cache_file}")

        except Exception as e:
            logger.warning(f"⚠️ 保存embedding缓存文件失败: {e}")

    def get_current_interests(self) -> BotPersonalityInterests | None:
        """获取当前的兴趣标签配置"""
        return self.current_interests

    def get_interest_stats(self) -> dict[str, Any]:
        """获取兴趣系统统计信息"""
        if not self.current_interests:
            return {"initialized": False}

        active_tags = self.current_interests.get_active_tags()

        return {
            "initialized": self._initialized,
            "total_tags": len(active_tags),
            "embedding_model": self.current_interests.embedding_model,
            "last_updated": self.current_interests.last_updated.isoformat(),
            "cache_size": len(self.embedding_cache),
        }

    async def update_interest_tags(self, new_personality_description: str | None = None):
        """更新兴趣标签"""
        try:
            if not self.current_interests:
                logger.warning("没有当前的兴趣标签配置，无法更新")
                return

            if new_personality_description:
                self.current_interests.personality_description = new_personality_description

            # 重新生成兴趣标签
            new_interests = await self._generate_interests_from_personality(
                self.current_interests.personality_description, self.current_interests.personality_id
            )

            if new_interests:
                new_interests.version = self.current_interests.version + 1
                self.current_interests = new_interests
                await self._save_interests_to_database(new_interests)
                logger.info(f"兴趣标签已更新，版本: {new_interests.version}")

        except Exception as e:
            logger.error(f"更新兴趣标签失败: {e}")
            traceback.print_exc()


# 创建全局实例（重新创建以包含新的属性）
bot_interest_manager = BotInterestManager()
