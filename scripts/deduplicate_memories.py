"""
记忆去重工具

功能：
1. 扫描所有标记为"相似"关系的记忆边
2. 对相似记忆进行去重（保留重要性高的，删除另一个）
3. 支持干运行模式（预览不执行）
4. 提供详细的去重报告

使用方法：
    # 预览模式（不实际删除）
    python scripts/deduplicate_memories.py --dry-run
    
    # 执行去重
    python scripts/deduplicate_memories.py
    
    # 指定相似度阈值
    python scripts/deduplicate_memories.py --threshold 0.9
    
    # 指定数据目录
    python scripts/deduplicate_memories.py --data-dir data/memory_graph
"""
import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.common.logger import get_logger
from src.memory_graph.manager_singleton import get_memory_manager, initialize_memory_manager, shutdown_memory_manager

logger = get_logger(__name__)


class MemoryDeduplicator:
    """记忆去重器"""
    
    def __init__(self, data_dir: str = "data/memory_graph", dry_run: bool = False, threshold: float = 0.85):
        self.data_dir = data_dir
        self.dry_run = dry_run
        self.threshold = threshold
        self.manager = None
        
        # 统计信息
        self.stats = {
            "total_memories": 0,
            "similar_pairs": 0,
            "duplicates_found": 0,
            "duplicates_removed": 0,
            "errors": 0,
        }
        
    async def initialize(self):
        """初始化记忆管理器"""
        logger.info(f"正在初始化记忆管理器 (data_dir={self.data_dir})...")
        self.manager = await initialize_memory_manager(data_dir=self.data_dir)
        if not self.manager:
            raise RuntimeError("记忆管理器初始化失败")
        
        self.stats["total_memories"] = len(self.manager.graph_store.get_all_memories())
        logger.info(f"✅ 记忆管理器初始化成功，共 {self.stats['total_memories']} 条记忆")
    
    async def find_similar_pairs(self) -> List[Tuple[str, str, float]]:
        """
        查找所有相似的记忆对（通过向量相似度计算）
        
        Returns:
            [(memory_id_1, memory_id_2, similarity), ...]
        """
        logger.info("正在扫描相似记忆对...")
        similar_pairs = []
        seen_pairs = set()  # 避免重复
        
        # 获取所有记忆
        all_memories = self.manager.graph_store.get_all_memories()
        total_memories = len(all_memories)
        
        logger.info(f"开始计算 {total_memories} 条记忆的相似度...")
        
        # 两两比较记忆的相似度
        for i, memory_i in enumerate(all_memories):
            # 每处理10条记忆让出控制权
            if i % 10 == 0:
                await asyncio.sleep(0)
                if i > 0:
                    logger.info(f"进度: {i}/{total_memories} ({i*100//total_memories}%)")
            
            # 获取记忆i的向量（从主题节点）
            vector_i = None
            for node in memory_i.nodes:
                if node.embedding is not None:
                    vector_i = node.embedding
                    break
            
            if vector_i is None:
                continue
            
            # 与后续记忆比较
            for j in range(i + 1, total_memories):
                memory_j = all_memories[j]
                
                # 获取记忆j的向量
                vector_j = None
                for node in memory_j.nodes:
                    if node.embedding is not None:
                        vector_j = node.embedding
                        break
                
                if vector_j is None:
                    continue
                
                # 计算余弦相似度
                similarity = self._cosine_similarity(vector_i, vector_j)
                
                # 只保存满足阈值的相似对
                if similarity >= self.threshold:
                    pair_key = tuple(sorted([memory_i.id, memory_j.id]))
                    if pair_key not in seen_pairs:
                        seen_pairs.add(pair_key)
                        similar_pairs.append((memory_i.id, memory_j.id, similarity))
        
        self.stats["similar_pairs"] = len(similar_pairs)
        logger.info(f"找到 {len(similar_pairs)} 对相似记忆（阈值>={self.threshold}）")
        
        return similar_pairs
    
    def _cosine_similarity(self, vec1: np.ndarray, vec2: np.ndarray) -> float:
        """计算余弦相似度"""
        try:
            vec1_norm = np.linalg.norm(vec1)
            vec2_norm = np.linalg.norm(vec2)
            
            if vec1_norm == 0 or vec2_norm == 0:
                return 0.0
            
            similarity = np.dot(vec1, vec2) / (vec1_norm * vec2_norm)
            return float(similarity)
        except Exception as e:
            logger.error(f"计算余弦相似度失败: {e}")
            return 0.0
    
    def decide_which_to_keep(self, mem_id_1: str, mem_id_2: str) -> Tuple[Optional[str], Optional[str]]:
        """
        决定保留哪个记忆，删除哪个
        
        优先级：
        1. 重要性更高的
        2. 激活度更高的
        3. 创建时间更早的
        
        Returns:
            (keep_id, remove_id)
        """
        mem1 = self.manager.graph_store.get_memory_by_id(mem_id_1)
        mem2 = self.manager.graph_store.get_memory_by_id(mem_id_2)
        
        if not mem1 or not mem2:
            logger.warning(f"记忆不存在: {mem_id_1} or {mem_id_2}")
            return None, None
        
        # 比较重要性
        if mem1.importance > mem2.importance:
            return mem_id_1, mem_id_2
        elif mem1.importance < mem2.importance:
            return mem_id_2, mem_id_1
        
        # 重要性相同，比较激活度
        if mem1.activation > mem2.activation:
            return mem_id_1, mem_id_2
        elif mem1.activation < mem2.activation:
            return mem_id_2, mem_id_1
        
        # 激活度也相同，保留更早创建的
        if mem1.created_at < mem2.created_at:
            return mem_id_1, mem_id_2
        else:
            return mem_id_2, mem_id_1
    
    async def deduplicate_pair(self, mem_id_1: str, mem_id_2: str, similarity: float) -> bool:
        """
        去重一对相似记忆
        
        Returns:
            是否成功去重
        """
        keep_id, remove_id = self.decide_which_to_keep(mem_id_1, mem_id_2)
        
        if not keep_id or not remove_id:
            self.stats["errors"] += 1
            return False
        
        keep_mem = self.manager.graph_store.get_memory_by_id(keep_id)
        remove_mem = self.manager.graph_store.get_memory_by_id(remove_id)
        
        logger.info(f"")
        logger.info(f"{'[预览]' if self.dry_run else '[执行]'} 去重相似记忆对 (相似度={similarity:.3f}):")
        logger.info(f"  保留: {keep_id}")
        logger.info(f"    - 主题: {keep_mem.metadata.get('topic', 'N/A')}")
        logger.info(f"    - 重要性: {keep_mem.importance:.2f}")
        logger.info(f"    - 激活度: {keep_mem.activation:.2f}")
        logger.info(f"    - 创建时间: {keep_mem.created_at}")
        logger.info(f"  删除: {remove_id}")
        logger.info(f"    - 主题: {remove_mem.metadata.get('topic', 'N/A')}")
        logger.info(f"    - 重要性: {remove_mem.importance:.2f}")
        logger.info(f"    - 激活度: {remove_mem.activation:.2f}")
        logger.info(f"    - 创建时间: {remove_mem.created_at}")
        
        if self.dry_run:
            logger.info("  [预览模式] 不执行实际删除")
            self.stats["duplicates_found"] += 1
            return True
        
        try:
            # 增强保留记忆的属性
            keep_mem.importance = min(1.0, keep_mem.importance + 0.05)
            keep_mem.activation = min(1.0, keep_mem.activation + 0.05)
            
            # 累加访问次数
            if hasattr(keep_mem, 'access_count') and hasattr(remove_mem, 'access_count'):
                keep_mem.access_count += remove_mem.access_count
            
            # 删除相似记忆
            await self.manager.delete_memory(remove_id)
            
            self.stats["duplicates_removed"] += 1
            logger.info(f"  ✅ 删除成功")
            
            # 让出控制权
            await asyncio.sleep(0)
            
            return True
            
        except Exception as e:
            logger.error(f"  ❌ 删除失败: {e}", exc_info=True)
            self.stats["errors"] += 1
            return False
    
    async def run(self):
        """执行去重"""
        start_time = datetime.now()
        
        print("="*70)
        print("记忆去重工具")
        print("="*70)
        print(f"数据目录: {self.data_dir}")
        print(f"相似度阈值: {self.threshold}")
        print(f"模式: {'预览模式（不实际删除）' if self.dry_run else '执行模式（会实际删除）'}")
        print("="*70)
        print()
        
        # 初始化
        await self.initialize()
        
        # 查找相似对
        similar_pairs = await self.find_similar_pairs()
        
        if not similar_pairs:
            logger.info("未找到需要去重的相似记忆对")
            print()
            print("="*70)
            print("未找到需要去重的记忆")
            print("="*70)
            return
        
        # 去重处理
        logger.info(f"开始{'预览' if self.dry_run else '执行'}去重...")
        print()
        
        processed_pairs = set()  # 避免重复处理
        
        for mem_id_1, mem_id_2, similarity in similar_pairs:
            # 检查是否已处理（可能一个记忆已被删除）
            pair_key = tuple(sorted([mem_id_1, mem_id_2]))
            if pair_key in processed_pairs:
                continue
            
            # 检查记忆是否仍存在
            if not self.manager.graph_store.get_memory_by_id(mem_id_1):
                logger.debug(f"记忆 {mem_id_1} 已不存在，跳过")
                continue
            if not self.manager.graph_store.get_memory_by_id(mem_id_2):
                logger.debug(f"记忆 {mem_id_2} 已不存在，跳过")
                continue
            
            # 执行去重
            success = await self.deduplicate_pair(mem_id_1, mem_id_2, similarity)
            
            if success:
                processed_pairs.add(pair_key)
        
        # 保存数据（如果不是干运行）
        if not self.dry_run:
            logger.info("正在保存数据...")
            await self.manager.persistence.save_graph_store(self.manager.graph_store)
            logger.info("✅ 数据已保存")
        
        # 统计报告
        elapsed = (datetime.now() - start_time).total_seconds()
        
        print()
        print("="*70)
        print("去重报告")
        print("="*70)
        print(f"总记忆数: {self.stats['total_memories']}")
        print(f"相似记忆对: {self.stats['similar_pairs']}")
        print(f"发现重复: {self.stats['duplicates_found'] if self.dry_run else self.stats['duplicates_removed']}")
        print(f"{'预览通过' if self.dry_run else '成功删除'}: {self.stats['duplicates_found'] if self.dry_run else self.stats['duplicates_removed']}")
        print(f"错误数: {self.stats['errors']}")
        print(f"耗时: {elapsed:.2f}秒")
        
        if self.dry_run:
            print()
            print("⚠️ 这是预览模式，未实际删除任何记忆")
            print("💡 要执行实际删除，请运行: python scripts/deduplicate_memories.py")
        else:
            print()
            print("✅ 去重完成！")
            final_count = len(self.manager.graph_store.get_all_memories())
            print(f"📊 最终记忆数: {final_count} (减少 {self.stats['total_memories'] - final_count} 条)")
        
        print("="*70)
    
    async def cleanup(self):
        """清理资源"""
        if self.manager:
            await shutdown_memory_manager()


async def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="记忆去重工具 - 对标记为相似的记忆进行一键去重",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  # 预览模式（推荐先运行）
  python scripts/deduplicate_memories.py --dry-run
  
  # 执行去重
  python scripts/deduplicate_memories.py
  
  # 指定相似度阈值（只处理相似度>=0.9的记忆对）
  python scripts/deduplicate_memories.py --threshold 0.9
  
  # 指定数据目录
  python scripts/deduplicate_memories.py --data-dir data/memory_graph
  
  # 组合使用
  python scripts/deduplicate_memories.py --dry-run --threshold 0.95 --data-dir data/test
        """
    )
    
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="预览模式，不实际删除记忆（推荐先运行此模式）"
    )
    
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.85,
        help="相似度阈值，只处理相似度>=此值的记忆对（默认: 0.85）"
    )
    
    parser.add_argument(
        "--data-dir",
        type=str,
        default="data/memory_graph",
        help="记忆数据目录（默认: data/memory_graph）"
    )
    
    args = parser.parse_args()
    
    # 创建去重器
    deduplicator = MemoryDeduplicator(
        data_dir=args.data_dir,
        dry_run=args.dry_run,
        threshold=args.threshold
    )
    
    try:
        # 执行去重
        await deduplicator.run()
    except KeyboardInterrupt:
        print("\n\n⚠️ 用户中断操作")
    except Exception as e:
        logger.error(f"执行失败: {e}", exc_info=True)
        print(f"\n❌ 执行失败: {e}")
        return 1
    finally:
        # 清理资源
        await deduplicator.cleanup()
    
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
