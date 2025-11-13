"""
为现有节点生成嵌入向量

批量为图存储中缺少嵌入向量的节点生成并索引嵌入向量

使用场景:
1. 历史记忆节点没有嵌入向量
2. 嵌入生成器之前未配置，现在需要补充生成
3. 向量索引损坏需要重建

使用方法:
    python scripts/generate_missing_embeddings.py [--node-types TOPIC,OBJECT] [--batch-size 50]
    
参数说明:
    --node-types: 需要生成嵌入的节点类型，默认为 TOPIC,OBJECT
    --batch-size: 批量处理大小，默认为 50
"""

import asyncio
import sys
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))


async def generate_missing_embeddings(
    target_node_types: list[str] = None,
    batch_size: int = 50,
):
    """
    为缺失嵌入向量的节点生成嵌入

    Args:
        target_node_types: 需要处理的节点类型列表（如 ["主题", "客体"]）
        batch_size: 批处理大小
    """
    from src.common.logger import get_logger
    from src.memory_graph.manager_singleton import get_memory_manager, initialize_memory_manager
    from src.memory_graph.models import NodeType

    logger = get_logger("generate_missing_embeddings")

    if target_node_types is None:
        target_node_types = [NodeType.TOPIC.value, NodeType.OBJECT.value]

    print(f"\n{'='*80}")
    print("🔧 为节点生成嵌入向量")
    print(f"{'='*80}\n")
    print(f"目标节点类型: {', '.join(target_node_types)}")
    print(f"批处理大小: {batch_size}\n")

    # 1. 初始化记忆管理器
    print("🔧 正在初始化记忆管理器...")
    await initialize_memory_manager()
    manager = get_memory_manager()

    if manager is None:
        print("❌ 记忆管理器初始化失败")
        return

    print("✅ 记忆管理器已初始化\n")

    # 2. 获取已索引的节点ID
    print("🔍 检查现有向量索引...")
    existing_node_ids = set()
    try:
        vector_count = manager.vector_store.collection.count()
        if vector_count > 0:
            # 分批获取所有已索引的ID
            batch_size_check = 1000
            for offset in range(0, vector_count, batch_size_check):
                limit = min(batch_size_check, vector_count - offset)
                result = manager.vector_store.collection.get(
                    limit=limit,
                    offset=offset,
                )
                if result and "ids" in result:
                    existing_node_ids.update(result["ids"])

        print(f"✅ 发现 {len(existing_node_ids)} 个已索引节点\n")
    except Exception as e:
        logger.warning(f"获取已索引节点ID失败: {e}")
        print("⚠️  无法获取已索引节点，将尝试跳过重复项\n")

    # 3. 收集需要生成嵌入的节点
    print("🔍 扫描需要生成嵌入的节点...")
    all_memories = manager.graph_store.get_all_memories()

    nodes_to_process = []
    total_target_nodes = 0
    type_stats = {nt: {"total": 0, "need_emb": 0, "already_indexed": 0} for nt in target_node_types}

    for memory in all_memories:
        for node in memory.nodes:
            if node.node_type.value in target_node_types:
                total_target_nodes += 1
                type_stats[node.node_type.value]["total"] += 1

                # 检查是否已在向量索引中
                if node.id in existing_node_ids:
                    type_stats[node.node_type.value]["already_indexed"] += 1
                    continue

                if not node.has_embedding():
                    nodes_to_process.append({
                        "node": node,
                        "memory_id": memory.id,
                    })
                    type_stats[node.node_type.value]["need_emb"] += 1

    print("\n📊 扫描结果:")
    for node_type in target_node_types:
        stats = type_stats[node_type]
        already_ok = stats["already_indexed"]
        coverage = (stats["total"] - stats["need_emb"]) / stats["total"] * 100 if stats["total"] > 0 else 0
        print(f"  - {node_type}: {stats['total']} 个节点, {stats['need_emb']} 个缺失嵌入, "
              f"{already_ok} 个已索引 (覆盖率: {coverage:.1f}%)")

    print(f"\n  总计: {total_target_nodes} 个目标节点, {len(nodes_to_process)} 个需要生成嵌入\n")

    if len(nodes_to_process) == 0:
        print("✅ 所有节点已有嵌入向量，无需生成")
        return

    # 3. 批量生成嵌入
    print("🚀 开始生成嵌入向量...\n")

    total_batches = (len(nodes_to_process) + batch_size - 1) // batch_size
    success_count = 0
    failed_count = 0
    indexed_count = 0

    for i in range(0, len(nodes_to_process), batch_size):
        batch = nodes_to_process[i : i + batch_size]
        batch_num = i // batch_size + 1

        print(f"📦 批次 {batch_num}/{total_batches} ({len(batch)} 个节点)...")

        try:
            # 提取文本内容
            texts = [item["node"].content for item in batch]

            # 批量生成嵌入
            embeddings = await manager.embedding_generator.generate_batch(texts)

            # 为节点设置嵌入并索引
            batch_nodes_for_index = []

            for j, (item, embedding) in enumerate(zip(batch, embeddings)):
                node = item["node"]

                if embedding is not None:
                    # 设置嵌入向量
                    node.embedding = embedding
                    batch_nodes_for_index.append(node)
                    success_count += 1
                else:
                    failed_count += 1
                    logger.warning(f"  ⚠️  节点 {node.id[:8]}... '{node.content[:30]}' 嵌入生成失败")

            # 批量索引到向量数据库
            if batch_nodes_for_index:
                try:
                    await manager.vector_store.add_nodes_batch(batch_nodes_for_index)
                    indexed_count += len(batch_nodes_for_index)
                    print(f"  ✅ 成功: {len(batch_nodes_for_index)}/{len(batch)} 个节点已生成并索引")
                except Exception as e:
                    # 如果批量失败，尝试逐个添加（跳过重复）
                    logger.warning(f"  批量索引失败，尝试逐个添加: {e}")
                    individual_success = 0
                    for node in batch_nodes_for_index:
                        try:
                            await manager.vector_store.add_node(node)
                            individual_success += 1
                            indexed_count += 1
                        except Exception as e2:
                            if "Expected IDs to be unique" in str(e2):
                                logger.debug(f"    跳过已存在节点: {node.id}")
                            else:
                                logger.error(f"    节点 {node.id} 索引失败: {e2}")
                    print(f"  ⚠️  逐个索引: {individual_success}/{len(batch_nodes_for_index)} 个成功")

        except Exception as e:
            failed_count += len(batch)
            logger.error(f"批次 {batch_num} 处理失败", exc_info=True)
            print(f"  ❌ 批次处理失败: {e}")

        # 显示进度
        total_processed = min(i + batch_size, len(nodes_to_process))
        progress = total_processed / len(nodes_to_process) * 100
        print(f"  📊 总进度: {total_processed}/{len(nodes_to_process)} ({progress:.1f}%)\n")

    # 4. 保存图数据（更新节点的 embedding 字段）
    print("💾 保存图数据...")
    try:
        await manager.persistence.save_graph_store(manager.graph_store)
        print("✅ 图数据已保存\n")
    except Exception as e:
        logger.error("保存图数据失败", exc_info=True)
        print(f"❌ 保存失败: {e}\n")

    # 5. 验证结果
    print("🔍 验证向量索引...")
    final_vector_count = manager.vector_store.collection.count()
    stats = manager.graph_store.get_statistics()
    total_nodes = stats["total_nodes"]

    print(f"\n{'='*80}")
    print("📊 生成完成")
    print(f"{'='*80}")
    print(f"处理节点数: {len(nodes_to_process)}")
    print(f"成功生成: {success_count}")
    print(f"失败数量: {failed_count}")
    print(f"成功索引: {indexed_count}")
    print(f"向量索引节点数: {final_vector_count}")
    print(f"图存储节点数: {total_nodes}")
    print(f"索引覆盖率: {final_vector_count / total_nodes * 100:.1f}%\n")

    # 6. 测试搜索
    print("🧪 测试搜索功能...")
    test_queries = ["小红帽蕾克", "拾风", "杰瑞喵"]

    for query in test_queries:
        results = await manager.search_memories(query=query, top_k=3)
        if results:
            print(f"\n✅ 查询 '{query}' 找到 {len(results)} 条记忆:")
            for i, memory in enumerate(results[:2], 1):
                subject_node = memory.get_subject_node()
                # 获取主题节点（遍历所有节点找TOPIC类型）
                from src.memory_graph.models import NodeType
                topic_nodes = [n for n in memory.nodes if n.node_type == NodeType.TOPIC]
                subject = subject_node.content if subject_node else "?"
                topic = topic_nodes[0].content if topic_nodes else "?"
                print(f"  {i}. {subject} - {topic} (重要性: {memory.importance:.2f})")
        else:
            print(f"\n⚠️  查询 '{query}' 返回 0 条结果")


async def main():
    import argparse

    parser = argparse.ArgumentParser(description="为节点生成嵌入向量")
    parser.add_argument(
        "--node-types",
        type=str,
        default="主题,客体",
        help="需要生成嵌入的节点类型，逗号分隔（默认：主题,客体）",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="批处理大小（默认：50）",
    )

    args = parser.parse_args()

    target_types = [t.strip() for t in args.node_types.split(",")]
    await generate_missing_embeddings(
        target_node_types=target_types,
        batch_size=args.batch_size,
    )


if __name__ == "__main__":
    asyncio.run(main())
