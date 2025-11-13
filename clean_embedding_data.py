#!/usr/bin/env python3
"""
清理记忆数据中的向量数据

此脚本用于清理现有 JSON 文件中的 embedding 字段，确保向量数据只存储在专门的向量数据库中。
这样可以：
1. 减少 JSON 文件大小
2. 提高读写性能
3. 避免数据冗余
4. 确保数据一致性

使用方法:
    python clean_embedding_data.py [--dry-run]

    --dry-run: 仅显示将要清理的统计信息，不实际修改文件
"""

import argparse
import json
import logging
from pathlib import Path
from typing import Any

import orjson

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("embedding_cleanup.log", encoding="utf-8")
    ]
)
logger = logging.getLogger(__name__)


class EmbeddingCleaner:
    """向量数据清理器"""

    def __init__(self, data_dir: str = "data"):
        """
        初始化清理器

        Args:
            data_dir: 数据目录路径
        """
        self.data_dir = Path(data_dir)
        self.cleaned_files = []
        self.errors = []
        self.stats = {
            "files_processed": 0,
            "embedings_removed": 0,
            "bytes_saved": 0,
            "nodes_processed": 0
        }

    def find_json_files(self) -> list[Path]:
        """查找可能包含向量数据的 JSON 文件"""
        json_files = []

        # 记忆图数据文件
        memory_graph_file = self.data_dir / "memory_graph" / "memory_graph.json"
        if memory_graph_file.exists():
            json_files.append(memory_graph_file)

        # 测试数据文件
        self.data_dir / "test_*"
        for test_path in self.data_dir.glob("test_*/memory_graph.json"):
            if test_path.exists():
                json_files.append(test_path)

        # 其他可能的记忆相关文件
        potential_files = [
            self.data_dir / "memory_metadata_index.json",
        ]

        for file_path in potential_files:
            if file_path.exists():
                json_files.append(file_path)

        logger.info(f"找到 {len(json_files)} 个需要处理的 JSON 文件")
        return json_files

    def analyze_embedding_in_data(self, data: dict[str, Any]) -> int:
        """
        分析数据中的 embedding 字段数量

        Args:
            data: 要分析的数据

        Returns:
            embedding 字段的数量
        """
        embedding_count = 0

        def count_embeddings(obj):
            nonlocal embedding_count
            if isinstance(obj, dict):
                if "embedding" in obj:
                    embedding_count += 1
                for value in obj.values():
                    count_embeddings(value)
            elif isinstance(obj, list):
                for item in obj:
                    count_embeddings(item)

        count_embeddings(data)
        return embedding_count

    def clean_embedding_from_data(self, data: dict[str, Any]) -> tuple[dict[str, Any], int]:
        """
        从数据中移除 embedding 字段

        Args:
            data: 要清理的数据

        Returns:
            (清理后的数据, 移除的 embedding 数量)
        """
        removed_count = 0

        def remove_embeddings(obj):
            nonlocal removed_count
            if isinstance(obj, dict):
                if "embedding" in obj:
                    del obj["embedding"]
                    removed_count += 1
                for value in obj.values():
                    remove_embeddings(value)
            elif isinstance(obj, list):
                for item in obj:
                    remove_embeddings(item)

        # 创建深拷贝以避免修改原数据
        import copy
        cleaned_data = copy.deepcopy(data)
        remove_embeddings(cleaned_data)

        return cleaned_data, removed_count

    def process_file(self, file_path: Path, dry_run: bool = False) -> bool:
        """
        处理单个文件

        Args:
            file_path: 文件路径
            dry_run: 是否为试运行模式

        Returns:
            是否处理成功
        """
        try:
            logger.info(f"处理文件: {file_path}")

            # 读取原文件
            original_content = file_path.read_bytes()
            original_size = len(original_content)

            # 解析 JSON 数据
            try:
                data = orjson.loads(original_content)
            except orjson.JSONDecodeError:
                # 回退到标准 json
                with open(file_path, encoding="utf-8") as f:
                    data = json.load(f)

            # 分析 embedding 数据
            embedding_count = self.analyze_embedding_in_data(data)

            if embedding_count == 0:
                logger.info("  ✓ 文件中没有 embedding 数据，跳过")
                return True

            logger.info(f"  发现 {embedding_count} 个 embedding 字段")

            if not dry_run:
                # 清理 embedding 数据
                cleaned_data, removed_count = self.clean_embedding_from_data(data)

                if removed_count != embedding_count:
                    logger.warning(f"  ⚠️ 清理数量不一致: 分析发现 {embedding_count}, 实际清理 {removed_count}")

                # 序列化清理后的数据
                try:
                    cleaned_content = orjson.dumps(
                        cleaned_data,
                        option=orjson.OPT_INDENT_2 | orjson.OPT_SERIALIZE_NUMPY
                    )
                except Exception:
                    # 回退到标准 json
                    cleaned_content = json.dumps(
                        cleaned_data,
                        indent=2,
                        ensure_ascii=False
                    ).encode("utf-8")

                cleaned_size = len(cleaned_content)
                bytes_saved = original_size - cleaned_size

                # 原子写入
                temp_file = file_path.with_suffix(".tmp")
                temp_file.write_bytes(cleaned_content)
                temp_file.replace(file_path)

                logger.info("  ✓ 清理完成:")
                logger.info(f"    - 移除 embedding 字段: {removed_count}")
                logger.info(f"    - 节省空间: {bytes_saved:,} 字节 ({bytes_saved/original_size*100:.1f}%)")
                logger.info(f"    - 新文件大小: {cleaned_size:,} 字节")

                # 更新统计
                self.stats["embedings_removed"] += removed_count
                self.stats["bytes_saved"] += bytes_saved

            else:
                logger.info(f"  [试运行] 将移除 {embedding_count} 个 embedding 字段")
                self.stats["embedings_removed"] += embedding_count

            self.stats["files_processed"] += 1
            self.cleaned_files.append(file_path)
            return True

        except Exception as e:
            logger.error(f"  ❌ 处理失败: {e}")
            self.errors.append((str(file_path), str(e)))
            return False

    def analyze_nodes_in_file(self, file_path: Path) -> int:
        """
        分析文件中的节点数量

        Args:
            file_path: 文件路径

        Returns:
            节点数量
        """
        try:
            with open(file_path, encoding="utf-8") as f:
                data = json.load(f)

            node_count = 0
            if "nodes" in data and isinstance(data["nodes"], list):
                node_count = len(data["nodes"])

            return node_count

        except Exception as e:
            logger.warning(f"分析节点数量失败: {e}")
            return 0

    def run(self, dry_run: bool = False):
        """
        运行清理过程

        Args:
            dry_run: 是否为试运行模式
        """
        logger.info("开始向量数据清理")
        logger.info(f"模式: {'试运行' if dry_run else '正式执行'}")

        # 查找要处理的文件
        json_files = self.find_json_files()

        if not json_files:
            logger.info("没有找到需要处理的文件")
            return

        # 统计总节点数
        total_nodes = sum(self.analyze_nodes_in_file(f) for f in json_files)
        self.stats["nodes_processed"] = total_nodes

        logger.info(f"总计 {len(json_files)} 个文件，{total_nodes} 个节点")

        # 处理每个文件
        success_count = 0
        for file_path in json_files:
            if self.process_file(file_path, dry_run):
                success_count += 1

        # 输出统计信息
        self.print_summary(dry_run, success_count, len(json_files))

    def print_summary(self, dry_run: bool, success_count: int, total_files: int):
        """打印清理摘要"""
        logger.info("=" * 60)
        logger.info("清理摘要")
        logger.info("=" * 60)

        mode = "试运行" if dry_run else "正式执行"
        logger.info(f"执行模式: {mode}")
        logger.info(f"处理文件: {success_count}/{total_files}")
        logger.info(f"处理节点: {self.stats['nodes_processed']}")
        logger.info(f"清理 embedding 字段: {self.stats['embedings_removed']}")

        if not dry_run:
            logger.info(f"节省空间: {self.stats['bytes_saved']:,} 字节")
            if self.stats["bytes_saved"] > 0:
                mb_saved = self.stats["bytes_saved"] / 1024 / 1024
                logger.info(f"节省空间: {mb_saved:.2f} MB")

        if self.errors:
            logger.warning(f"遇到 {len(self.errors)} 个错误:")
            for file_path, error in self.errors:
                logger.warning(f"  {file_path}: {error}")

        if success_count == total_files and not self.errors:
            logger.info("所有文件处理成功！")

        logger.info("=" * 60)


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="清理记忆数据中的向量数据",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例用法:
  python clean_embedding_data.py --dry-run  # 试运行，查看统计信息
  python clean_embedding_data.py            # 正式执行清理
        """
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="试运行模式，不实际修改文件"
    )

    parser.add_argument(
        "--data-dir",
        default="data",
        help="数据目录路径 (默认: data)"
    )

    args = parser.parse_args()

    # 确认操作
    if not args.dry_run:
        print("警告：此操作将永久删除 JSON 文件中的 embedding 数据！")
        print("    请确保向量数据库正在正常工作。")
        print()
        response = input("确认继续？(yes/no): ")
        if response.lower() not in ["yes", "y", "是"]:
            print("操作已取消")
            return

    # 执行清理
    cleaner = EmbeddingCleaner(args.data_dir)
    cleaner.run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
