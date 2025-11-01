"""
海马体双峰分布采样器
基于旧版海马体的采样策略，适配新版记忆系统
实现低消耗、高效率的记忆采样模式
"""

import asyncio
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import numpy as np

from src.chat.utils.chat_message_builder import (
    build_readable_messages,
    get_raw_msg_by_timestamp,
    get_raw_msg_by_timestamp_with_chat,
)
from src.chat.utils.utils import translate_timestamp_to_human_readable
from src.common.logger import get_logger
from src.config.config import global_config
from src.llm_models.utils_model import LLMRequest

logger = get_logger(__name__)

# 全局背景任务集合
_background_tasks = set()


@dataclass
class HippocampusSampleConfig:
    """海马体采样配置"""

    # 双峰分布参数
    recent_mean_hours: float = 12.0  # 近期分布均值（小时）
    recent_std_hours: float = 8.0  # 近期分布标准差（小时）
    recent_weight: float = 0.7  # 近期分布权重

    distant_mean_hours: float = 48.0  # 远期分布均值（小时）
    distant_std_hours: float = 24.0  # 远期分布标准差（小时）
    distant_weight: float = 0.3  # 远期分布权重

    # 采样参数
    total_samples: int = 50  # 总采样数
    sample_interval: int = 1800  # 采样间隔（秒）
    max_sample_length: int = 30  # 每次采样的最大消息数量
    batch_size: int = 5  # 批处理大小

    @classmethod
    def from_global_config(cls) -> "HippocampusSampleConfig":
        """从全局配置创建海马体采样配置"""
        config = global_config.memory.hippocampus_distribution_config
        return cls(
            recent_mean_hours=config[0],
            recent_std_hours=config[1],
            recent_weight=config[2],
            distant_mean_hours=config[3],
            distant_std_hours=config[4],
            distant_weight=config[5],
            total_samples=global_config.memory.hippocampus_sample_size,
            sample_interval=global_config.memory.hippocampus_sample_interval,
            max_sample_length=global_config.memory.hippocampus_batch_size,
            batch_size=global_config.memory.hippocampus_batch_size,
        )


class HippocampusSampler:
    """海马体双峰分布采样器"""

    def __init__(self, memory_system=None):
        self.memory_system = memory_system
        self.config = HippocampusSampleConfig.from_global_config()
        self.last_sample_time = 0
        self.is_running = False

        # 记忆构建模型
        self.memory_builder_model: LLMRequest | None = None

        # 统计信息
        self.sample_count = 0
        self.success_count = 0
        self.last_sample_results: list[dict[str, Any]] = []

    async def initialize(self):
        """初始化采样器"""
        try:
            # 初始化LLM模型
            from src.config.config import model_config

            task_config = getattr(model_config.model_task_config, "utils", None)
            if task_config:
                self.memory_builder_model = LLMRequest(model_set=task_config, request_type="memory.hippocampus_build")
                task = asyncio.create_task(self.start_background_sampling())
                _background_tasks.add(task)
                task.add_done_callback(_background_tasks.discard)
                logger.info("✅ 海马体采样器初始化成功")
            else:
                raise RuntimeError("未找到记忆构建模型配置")

        except Exception as e:
            logger.error(f"❌ 海马体采样器初始化失败: {e}")
            raise

    def generate_time_samples(self) -> list[datetime]:
        """生成双峰分布的时间采样点"""
        # 计算每个分布的样本数
        recent_samples = max(1, int(self.config.total_samples * self.config.recent_weight))
        distant_samples = max(1, self.config.total_samples - recent_samples)

        # 生成两个正态分布的小时偏移
        recent_offsets = np.random.normal(
            loc=self.config.recent_mean_hours, scale=self.config.recent_std_hours, size=recent_samples
        )
        distant_offsets = np.random.normal(
            loc=self.config.distant_mean_hours, scale=self.config.distant_std_hours, size=distant_samples
        )

        # 合并两个分布的偏移
        all_offsets = np.concatenate([recent_offsets, distant_offsets])

        # 转换为时间戳（使用绝对值确保时间点在过去）
        base_time = datetime.now()
        timestamps = [base_time - timedelta(hours=abs(offset)) for offset in all_offsets]

        # 按时间排序（从最早到最近）
        return sorted(timestamps)

    async def collect_message_samples(self, target_timestamp: float) -> list[dict[str, Any]] | None:
        """收集指定时间戳附近的消息样本"""
        try:
            # 随机时间窗口：5-30分钟
            time_window_seconds = random.randint(300, 1800)

            # 尝试3次获取消息
            for attempt in range(3):
                timestamp_start = target_timestamp
                timestamp_end = target_timestamp + time_window_seconds

                # 获取单条消息作为锚点
                anchor_messages = await get_raw_msg_by_timestamp(
                    timestamp_start=timestamp_start,
                    timestamp_end=timestamp_end,
                    limit=1,
                    limit_mode="earliest",
                )

                if not anchor_messages:
                    target_timestamp -= 120  # 向前调整2分钟
                    continue

                anchor_message = anchor_messages[0]
                chat_id = anchor_message.get("chat_id")

                if not chat_id:
                    continue

                # 获取同聊天的多条消息
                messages = await get_raw_msg_by_timestamp_with_chat(
                    timestamp_start=timestamp_start,
                    timestamp_end=timestamp_end,
                    limit=self.config.max_sample_length,
                    limit_mode="earliest",
                    chat_id=chat_id,
                )

                if messages and len(messages) >= 2:  # 至少需要2条消息
                    # 过滤掉已经记忆过的消息
                    filtered_messages = [
                        msg
                        for msg in messages
                        if msg.get("memorized_times", 0) < 2  # 最多记忆2次
                    ]

                    if filtered_messages:
                        logger.debug(f"成功收集 {len(filtered_messages)} 条消息样本")
                        return filtered_messages

                target_timestamp -= 120  # 向前调整再试

            logger.debug(f"时间戳 {target_timestamp} 附近未找到有效消息样本")
            return None

        except Exception as e:
            logger.error(f"收集消息样本失败: {e}")
            return None

    async def build_memory_from_samples(self, messages: list[dict[str, Any]], target_timestamp: float) -> str | None:
        """从消息样本构建记忆"""
        if not messages or not self.memory_system or not self.memory_builder_model:
            return None

        try:
            # 构建可读消息文本
            readable_text = await build_readable_messages(
                messages,
                merge_messages=True,
                timestamp_mode="normal_no_YMD",
                replace_bot_name=False,
            )

            if not readable_text:
                logger.warning("无法从消息样本生成可读文本")
                return None

            # 直接使用对话文本，不添加系统标识符
            input_text = readable_text

            logger.debug(f"开始构建记忆，文本长度: {len(input_text)}")

            # 构建上下文
            context = {
                "user_id": "hippocampus_sampler",
                "timestamp": time.time(),
                "source": "hippocampus_sampling",
                "message_count": len(messages),
                "sample_mode": "bimodal_distribution",
                "is_hippocampus_sample": True,  # 标识为海马体样本
                "bypass_value_threshold": True,  # 绕过价值阈值检查
                "hippocampus_sample_time": target_timestamp,  # 记录样本时间
            }

            # 使用记忆系统构建记忆（绕过构建间隔检查）
            memories = await self.memory_system.build_memory_from_conversation(
                conversation_text=input_text,
                context=context,
                timestamp=time.time(),
                bypass_interval=True,  # 海马体采样器绕过构建间隔限制
            )

            if memories:
                memory_count = len(memories)
                self.success_count += 1

                # 记录采样结果
                result = {
                    "timestamp": time.time(),
                    "memory_count": memory_count,
                    "message_count": len(messages),
                    "text_preview": readable_text[:100] + "..." if len(readable_text) > 100 else readable_text,
                    "memory_types": [m.memory_type.value for m in memories],
                }
                self.last_sample_results.append(result)

                # 限制结果历史长度
                if len(self.last_sample_results) > 10:
                    self.last_sample_results.pop(0)

                logger.info(f"✅ 海马体采样成功构建 {memory_count} 条记忆")
                return f"构建{memory_count}条记忆"
            else:
                logger.debug("海马体采样未生成有效记忆")
                return None

        except Exception as e:
            logger.error(f"海马体采样构建记忆失败: {e}")
            return None

    async def perform_sampling_cycle(self) -> dict[str, Any]:
        """执行一次完整的采样周期（优化版：批量融合构建）"""
        if not self.should_sample():
            return {"status": "skipped", "reason": "interval_not_met"}

        start_time = time.time()
        self.sample_count += 1

        try:
            # 生成时间采样点
            time_samples = self.generate_time_samples()
            logger.debug(f"生成 {len(time_samples)} 个时间采样点")

            # 记录时间采样点（调试用）
            readable_timestamps = [
                translate_timestamp_to_human_readable(int(ts.timestamp()), mode="normal")
                for ts in time_samples[:5]  # 只显示前5个
            ]
            logger.debug(f"时间采样点示例: {readable_timestamps}")

            # 第一步：批量收集所有消息样本
            logger.debug("开始批量收集消息样本...")
            collected_messages = await self._collect_all_message_samples(time_samples)

            if not collected_messages:
                logger.info("未收集到有效消息样本，跳过本次采样")
                self.last_sample_time = time.time()
                return {
                    "status": "success",
                    "sample_count": self.sample_count,
                    "success_count": self.success_count,
                    "processed_samples": len(time_samples),
                    "successful_builds": 0,
                    "duration": time.time() - start_time,
                    "samples_generated": len(time_samples),
                    "message": "未收集到有效消息样本",
                }

            logger.info(f"收集到 {len(collected_messages)} 组消息样本")

            # 第二步：融合和去重消息
            logger.debug("开始融合和去重消息...")
            fused_messages = await self._fuse_and_deduplicate_messages(collected_messages)

            if not fused_messages:
                logger.info("消息融合后为空，跳过记忆构建")
                self.last_sample_time = time.time()
                return {
                    "status": "success",
                    "sample_count": self.sample_count,
                    "success_count": self.success_count,
                    "processed_samples": len(time_samples),
                    "successful_builds": 0,
                    "duration": time.time() - start_time,
                    "samples_generated": len(time_samples),
                    "message": "消息融合后为空",
                }

            logger.info(f"融合后得到 {len(fused_messages)} 组有效消息")

            # 第三步：一次性构建记忆
            logger.debug("开始批量构建记忆...")
            build_result = await self._build_batch_memory(fused_messages, time_samples)

            # 更新最后采样时间
            self.last_sample_time = time.time()

            duration = time.time() - start_time
            result = {
                "status": "success",
                "sample_count": self.sample_count,
                "success_count": self.success_count,
                "processed_samples": len(time_samples),
                "successful_builds": build_result.get("memory_count", 0),
                "duration": duration,
                "samples_generated": len(time_samples),
                "messages_collected": len(collected_messages),
                "messages_fused": len(fused_messages),
                "optimization_mode": "batch_fusion",
            }

            logger.info(
                f"✅ 海马体采样周期完成（批量融合模式） | "
                f"采样点: {len(time_samples)} | "
                f"收集消息: {len(collected_messages)} | "
                f"融合消息: {len(fused_messages)} | "
                f"构建记忆: {build_result.get('memory_count', 0)} | "
                f"耗时: {duration:.2f}s"
            )

            return result

        except Exception as e:
            logger.error(f"❌ 海马体采样周期失败: {e}")
            return {
                "status": "error",
                "error": str(e),
                "sample_count": self.sample_count,
                "duration": time.time() - start_time,
            }

    async def _collect_all_message_samples(self, time_samples: list[datetime]) -> list[list[dict[str, Any]]]:
        """批量收集所有时间点的消息样本"""
        collected_messages = []
        max_concurrent = min(5, len(time_samples))  # 提高并发数到5

        for i in range(0, len(time_samples), max_concurrent):
            batch = time_samples[i : i + max_concurrent]
            tasks = []

            # 创建并发收集任务
            for timestamp in batch:
                target_ts = timestamp.timestamp()
                task = self.collect_message_samples(target_ts)
                tasks.append(task)

            # 执行并发收集
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # 处理收集结果
            for result in results:
                if isinstance(result, list) and result:
                    collected_messages.append(result)
                elif isinstance(result, Exception):
                    logger.debug(f"消息收集异常: {result}")

            # 批次间短暂延迟
            if i + max_concurrent < len(time_samples):
                await asyncio.sleep(0.5)

        return collected_messages

    async def _fuse_and_deduplicate_messages(
        self, collected_messages: list[list[dict[str, Any]]]
    ) -> list[list[dict[str, Any]]]:
        """融合和去重消息样本"""
        if not collected_messages:
            return []

        try:
            # 展平所有消息
            all_messages = []
            for message_group in collected_messages:
                all_messages.extend(message_group)

            logger.debug(f"展开后总消息数: {len(all_messages)}")

            # 去重逻辑：基于消息内容和时间戳
            unique_messages = []
            seen_hashes = set()

            for message in all_messages:
                # 创建消息哈希用于去重
                content = message.get("processed_plain_text", "") or message.get("display_message", "")
                timestamp = message.get("time", 0)
                chat_id = message.get("chat_id", "")

                # 简单哈希：内容前50字符 + 时间戳(精确到分钟) + 聊天ID
                hash_key = f"{content[:50]}_{int(timestamp // 60)}_{chat_id}"

                if hash_key not in seen_hashes and len(content.strip()) > 10:
                    seen_hashes.add(hash_key)
                    unique_messages.append(message)

            logger.debug(f"去重后消息数: {len(unique_messages)}")

            # 按时间排序
            unique_messages.sort(key=lambda x: x.get("time", 0))

            # 按聊天ID分组重新组织
            chat_groups = {}
            for message in unique_messages:
                chat_id = message.get("chat_id", "unknown")
                if chat_id not in chat_groups:
                    chat_groups[chat_id] = []
                chat_groups[chat_id].append(message)

            # 合并相邻时间范围内的消息
            fused_groups = []
            for chat_id, messages in chat_groups.items():
                fused_groups.extend(self._merge_adjacent_messages(messages))

            logger.debug(f"融合后消息组数: {len(fused_groups)}")
            return fused_groups

        except Exception as e:
            logger.error(f"消息融合失败: {e}")
            # 返回原始消息组作为备选
            return collected_messages[:5]  # 限制返回数量

    def _merge_adjacent_messages(
        self, messages: list[dict[str, Any]], time_gap: int = 1800
    ) -> list[list[dict[str, Any]]]:
        """合并时间间隔内的消息"""
        if not messages:
            return []

        merged_groups = []
        current_group = [messages[0]]

        for i in range(1, len(messages)):
            current_time = messages[i].get("time", 0)
            prev_time = current_group[-1].get("time", 0)

            # 如果时间间隔小于阈值，合并到当前组
            if current_time - prev_time <= time_gap:
                current_group.append(messages[i])
            else:
                # 否则开始新组
                merged_groups.append(current_group)
                current_group = [messages[i]]

        # 添加最后一组
        merged_groups.append(current_group)

        # 过滤掉只有一条消息的组（除非内容较长）
        result_groups = [
            group for group in merged_groups
            if len(group) > 1 or any(len(msg.get("processed_plain_text", "")) > 100 for msg in group)
        ]

        return result_groups

    async def _build_batch_memory(
        self, fused_messages: list[list[dict[str, Any]]], time_samples: list[datetime]
    ) -> dict[str, Any]:
        """批量构建记忆"""
        if not fused_messages:
            return {"memory_count": 0, "memories": []}

        try:
            total_memories = []
            total_memory_count = 0

            # 构建融合后的文本
            batch_input_text = await self._build_fused_conversation_text(fused_messages)

            if not batch_input_text:
                logger.warning("无法构建融合文本，尝试单独构建")
                # 备选方案：分别构建
                return await self._fallback_individual_build(fused_messages)

            # 创建批量上下文
            batch_context = {
                "user_id": "hippocampus_batch_sampler",
                "timestamp": time.time(),
                "source": "hippocampus_batch_sampling",
                "message_groups_count": len(fused_messages),
                "total_messages": sum(len(group) for group in fused_messages),
                "sample_count": len(time_samples),
                "is_hippocampus_sample": True,
                "bypass_value_threshold": True,
                "optimization_mode": "batch_fusion",
            }

            logger.debug(f"批量构建记忆，文本长度: {len(batch_input_text)}")

            # 一次性构建记忆
            memories = await self.memory_system.build_memory_from_conversation(
                conversation_text=batch_input_text, context=batch_context, timestamp=time.time(), bypass_interval=True
            )

            if memories:
                memory_count = len(memories)
                self.success_count += 1
                total_memory_count += memory_count
                total_memories.extend(memories)

                logger.info(f"✅ 批量海马体采样成功构建 {memory_count} 条记忆")
            else:
                logger.debug("批量海马体采样未生成有效记忆")

            # 记录采样结果
            result = {
                "timestamp": time.time(),
                "memory_count": total_memory_count,
                "message_groups_count": len(fused_messages),
                "total_messages": sum(len(group) for group in fused_messages),
                "text_preview": batch_input_text[:200] + "..." if len(batch_input_text) > 200 else batch_input_text,
                "memory_types": [m.memory_type.value for m in total_memories],
            }

            self.last_sample_results.append(result)

            # 限制结果历史长度
            if len(self.last_sample_results) > 10:
                self.last_sample_results.pop(0)

            return {"memory_count": total_memory_count, "memories": total_memories, "result": result}

        except Exception as e:
            logger.error(f"批量构建记忆失败: {e}")
            return {"memory_count": 0, "error": str(e)}

    async def _build_fused_conversation_text(self, fused_messages: list[list[dict[str, Any]]]) -> str:
        """构建融合后的对话文本"""
        try:
            conversation_parts = []

            for group_idx, message_group in enumerate(fused_messages):
                if not message_group:
                    continue

                # 为每个消息组添加分隔符
                group_header = f"\n=== 对话片段 {group_idx + 1} ==="
                conversation_parts.append(group_header)

                # 构建可读消息
                group_text = await build_readable_messages(
                    message_group,
                    merge_messages=True,
                    timestamp_mode="normal_no_YMD",
                    replace_bot_name=False,
                )

                if group_text and len(group_text.strip()) > 10:
                    conversation_parts.append(group_text.strip())

            return "\n".join(conversation_parts)

        except Exception as e:
            logger.error(f"构建融合文本失败: {e}")
            return ""

    async def _fallback_individual_build(self, fused_messages: list[list[dict[str, Any]]]) -> dict[str, Any]:
        """备选方案：单独构建每个消息组"""
        total_memories = []
        total_count = 0

        for group in fused_messages[:5]:  # 限制最多5组
            try:
                memories = await self.build_memory_from_samples(group, time.time())
                if memories:
                    total_memories.extend(memories)
                    total_count += len(memories)
            except Exception as e:
                logger.debug(f"单独构建失败: {e}")

        return {"memory_count": total_count, "memories": total_memories, "fallback_mode": True}

    async def process_sample_timestamp(self, target_timestamp: float) -> str | None:
        """处理单个时间戳采样（保留作为备选方法）"""
        try:
            # 收集消息样本
            messages = await self.collect_message_samples(target_timestamp)
            if not messages:
                return None

            # 构建记忆
            result = await self.build_memory_from_samples(messages, target_timestamp)
            return result

        except Exception as e:
            logger.debug(f"处理时间戳采样失败 {target_timestamp}: {e}")
            return None

    def should_sample(self) -> bool:
        """检查是否应该进行采样"""
        current_time = time.time()

        # 检查时间间隔
        if current_time - self.last_sample_time < self.config.sample_interval:
            return False

        # 检查是否已初始化
        if not self.memory_builder_model:
            logger.warning("海马体采样器未初始化")
            return False

        return True

    async def start_background_sampling(self):
        """启动后台采样"""
        if self.is_running:
            logger.warning("海马体后台采样已在运行")
            return

        self.is_running = True
        logger.info("🚀 启动海马体后台采样任务")

        try:
            while self.is_running:
                try:
                    # 执行采样周期
                    result = await self.perform_sampling_cycle()

                    # 如果是跳过状态，短暂睡眠
                    if result.get("status") == "skipped":
                        await asyncio.sleep(60)  # 1分钟后重试
                    else:
                        # 正常等待下一个采样间隔
                        await asyncio.sleep(self.config.sample_interval)

                except Exception as e:
                    logger.error(f"海马体后台采样异常: {e}")
                    await asyncio.sleep(300)  # 异常时等待5分钟

        except asyncio.CancelledError:
            logger.info("海马体后台采样任务被取消")
        finally:
            self.is_running = False

    def stop_background_sampling(self):
        """停止后台采样"""
        self.is_running = False
        logger.info("🛑 停止海马体后台采样任务")

    def get_sampling_stats(self) -> dict[str, Any]:
        """获取采样统计信息"""
        success_rate = (self.success_count / self.sample_count * 100) if self.sample_count > 0 else 0

        # 计算最近的平均数据
        recent_avg_messages = 0
        recent_avg_memory_count = 0
        if self.last_sample_results:
            recent_results = self.last_sample_results[-5:]  # 最近5次
            recent_avg_messages = sum(r.get("total_messages", 0) for r in recent_results) / len(recent_results)
            recent_avg_memory_count = sum(r.get("memory_count", 0) for r in recent_results) / len(recent_results)

        return {
            "is_running": self.is_running,
            "sample_count": self.sample_count,
            "success_count": self.success_count,
            "success_rate": f"{success_rate:.1f}%",
            "last_sample_time": self.last_sample_time,
            "optimization_mode": "batch_fusion",  # 显示优化模式
            "performance_metrics": {
                "avg_messages_per_sample": f"{recent_avg_messages:.1f}",
                "avg_memories_per_sample": f"{recent_avg_memory_count:.1f}",
                "fusion_efficiency": f"{(recent_avg_messages / max(recent_avg_memory_count, 1)):.1f}x"
                if recent_avg_messages > 0
                else "N/A",
            },
            "config": {
                "sample_interval": self.config.sample_interval,
                "total_samples": self.config.total_samples,
                "recent_weight": f"{self.config.recent_weight:.1%}",
                "distant_weight": f"{self.config.distant_weight:.1%}",
                "max_concurrent": 5,  # 批量模式并发数
                "fusion_time_gap": "30分钟",  # 消息融合时间间隔
            },
            "recent_results": self.last_sample_results[-5:],  # 最近5次结果
        }


# 全局海马体采样器实例
_hippocampus_sampler: HippocampusSampler | None = None


def get_hippocampus_sampler(memory_system=None) -> HippocampusSampler:
    """获取全局海马体采样器实例"""
    global _hippocampus_sampler
    if _hippocampus_sampler is None:
        _hippocampus_sampler = HippocampusSampler(memory_system)
    return _hippocampus_sampler


async def initialize_hippocampus_sampler(memory_system=None) -> HippocampusSampler:
    """初始化全局海马体采样器"""
    sampler = get_hippocampus_sampler(memory_system)
    await sampler.initialize()
    return sampler
