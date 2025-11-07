"""
记忆图可视化服务器

提供 Web API 用于可视化记忆图数据
"""

import asyncio
import logging

# 添加项目根目录到 Python 路径
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from flask import Flask, jsonify, render_template, request
from flask_cors import CORS

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from src.memory_graph.manager import MemoryManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)  # 允许跨域请求

# 全局记忆管理器
memory_manager: Optional[MemoryManager] = None


def init_memory_manager():
    """初始化记忆管理器"""
    global memory_manager
    if memory_manager is None:
        try:
            memory_manager = MemoryManager()
            # 在新的事件循环中初始化
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(memory_manager.initialize())
            logger.info("记忆管理器初始化成功")
        except Exception as e:
            logger.error(f"初始化记忆管理器失败: {e}")
            raise


@app.route("/")
def index():
    """主页面"""
    return render_template("visualizer.html")


@app.route("/api/graph/full")
def get_full_graph():
    """
    获取完整记忆图数据

    返回所有节点和边，格式化为前端可用的结构
    """
    try:
        if memory_manager is None:
            init_memory_manager()

        # 获取所有记忆
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        # 获取所有记忆
        all_memories = memory_manager.graph_store.get_all_memories()

        # 构建节点和边数据
        nodes_dict = {}  # {node_id: node_data}
        edges_dict = {}  # {edge_id: edge_data} - 使用字典去重
        memory_info = []

        for memory in all_memories:
            # 添加记忆信息
            memory_info.append(
                {
                    "id": memory.id,
                    "type": memory.memory_type.value,
                    "importance": memory.importance,
                    "activation": memory.activation,
                    "status": memory.status.value,
                    "created_at": memory.created_at.isoformat(),
                    "text": memory.to_text(),
                    "access_count": memory.access_count,
                }
            )

            # 处理节点
            for node in memory.nodes:
                if node.id not in nodes_dict:
                    nodes_dict[node.id] = {
                        "id": node.id,
                        "label": node.content,
                        "type": node.node_type.value,
                        "group": node.node_type.name,  # 用于颜色分组
                        "title": f"{node.node_type.value}: {node.content}",
                        "metadata": node.metadata,
                        "created_at": node.created_at.isoformat(),
                    }

            # 处理边 - 使用字典自动去重
            for edge in memory.edges:
                edge_id = edge.id
                # 如果ID已存在，生成唯一ID
                counter = 1
                original_edge_id = edge_id
                while edge_id in edges_dict:
                    edge_id = f"{original_edge_id}_{counter}"
                    counter += 1

                edges_dict[edge_id] = {
                    "id": edge_id,
                    "from": edge.source_id,
                    "to": edge.target_id,
                    "label": edge.relation,
                    "type": edge.edge_type.value,
                    "importance": edge.importance,
                    "title": f"{edge.edge_type.value}: {edge.relation}",
                    "arrows": "to",
                    "memory_id": memory.id,
                }

        nodes_list = list(nodes_dict.values())
        edges_list = list(edges_dict.values())

        return jsonify(
            {
                "success": True,
                "data": {
                    "nodes": nodes_list,
                    "edges": edges_list,
                    "memories": memory_info,
                    "stats": {
                        "total_nodes": len(nodes_list),
                        "total_edges": len(edges_list),
                        "total_memories": len(all_memories),
                    },
                },
            }
        )

    except Exception as e:
        logger.error(f"获取图数据失败: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/memory/<memory_id>")
def get_memory_detail(memory_id: str):
    """
    获取特定记忆的详细信息

    Args:
        memory_id: 记忆ID
    """
    try:
        if memory_manager is None:
            init_memory_manager()

        memory = memory_manager.graph_store.get_memory_by_id(memory_id)

        if memory is None:
            return jsonify({"success": False, "error": "记忆不存在"}), 404

        return jsonify({"success": True, "data": memory.to_dict()})

    except Exception as e:
        logger.error(f"获取记忆详情失败: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/search")
def search_memories():
    """
    搜索记忆

    Query参数:
        - q: 搜索关键词
        - type: 记忆类型过滤
        - limit: 返回数量限制
    """
    try:
        if memory_manager is None:
            init_memory_manager()

        query = request.args.get("q", "")
        memory_type = request.args.get("type", None)
        limit = int(request.args.get("limit", 50))

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        # 执行搜索
        results = loop.run_until_complete(memory_manager.search_memories(query=query, top_k=limit))

        # 构建返回数据
        memories = []
        for memory in results:
            memories.append(
                {
                    "id": memory.id,
                    "text": memory.to_text(),
                    "type": memory.memory_type.value,
                    "importance": memory.importance,
                    "created_at": memory.created_at.isoformat(),
                }
            )

        return jsonify(
            {
                "success": True,
                "data": {
                    "results": memories,
                    "count": len(memories),
                },
            }
        )

    except Exception as e:
        logger.error(f"搜索失败: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/stats")
def get_statistics():
    """
    获取记忆图统计信息
    """
    try:
        if memory_manager is None:
            init_memory_manager()

        # 获取统计信息
        all_memories = memory_manager.graph_store.get_all_memories()
        all_nodes = set()
        all_edges = 0

        for memory in all_memories:
            for node in memory.nodes:
                all_nodes.add(node.id)
            all_edges += len(memory.edges)

        stats = {
            "total_memories": len(all_memories),
            "total_nodes": len(all_nodes),
            "total_edges": all_edges,
            "node_types": {},
            "memory_types": {},
        }

        # 统计节点类型分布
        for memory in all_memories:
            mem_type = memory.memory_type.value
            stats["memory_types"][mem_type] = stats["memory_types"].get(mem_type, 0) + 1

            for node in memory.nodes:
                node_type = node.node_type.value
                stats["node_types"][node_type] = stats["node_types"].get(node_type, 0) + 1

        return jsonify({"success": True, "data": stats})

    except Exception as e:
        logger.error(f"获取统计信息失败: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/files")
def list_files():
    """
    列出所有可用的数据文件
    注意: 完整版服务器直接使用内存中的数据，不支持文件切换
    """
    try:
        from pathlib import Path

        data_dir = Path("data/memory_graph")

        files = []
        if data_dir.exists():
            for f in data_dir.glob("*.json"):
                stat = f.stat()
                files.append(
                    {
                        "path": str(f),
                        "name": f.name,
                        "size": stat.st_size,
                        "size_kb": round(stat.st_size / 1024, 2),
                        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                        "modified_readable": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                        "is_current": True,  # 完整版始终使用内存数据
                    }
                )

        return jsonify(
            {
                "success": True,
                "files": files,
                "count": len(files),
                "current_file": "memory_manager (实时数据)",
                "note": "完整版服务器使用实时内存数据，如需切换文件请使用独立版服务器",
            }
        )
    except Exception as e:
        logger.error(f"获取文件列表失败: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/reload")
def reload_data():
    """
    重新加载数据
    """
    return jsonify({"success": True, "message": "完整版服务器使用实时数据，无需重新加载", "note": "数据始终是最新的"})


def run_server(host: str = "127.0.0.1", port: int = 5000, debug: bool = False):
    """
    启动可视化服务器

    Args:
        host: 服务器地址
        port: 端口号
        debug: 是否开启调试模式
    """
    logger.info(f"启动记忆图可视化服务器: http://{host}:{port}")
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    run_server(debug=True)
