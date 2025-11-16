"""
记忆图可视化 - API 路由模块

提供 Web API 用于可视化记忆图数据
"""

from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import orjson
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from src.common.security import get_api_key

# 调整项目根目录的计算方式
project_root = Path(__file__).parent.parent.parent
data_dir = project_root / "data" / "memory_graph"

# 缓存
graph_data_cache = None
current_data_file = None

# FastAPI 路由
router = APIRouter(dependencies=[Depends(get_api_key)])

# Jinja2 模板引擎
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def find_available_data_files() -> list[Path]:
    """查找所有可用的记忆图数据文件"""
    files = []
    if not data_dir.exists():
        return files

    possible_files = ["graph_store.json", "memory_graph.json", "graph_data.json"]
    for filename in possible_files:
        file_path = data_dir / filename
        if file_path.exists():
            files.append(file_path)

    for pattern in ["graph_store_*.json", "memory_graph_*.json", "graph_data_*.json"]:
        for backup_file in data_dir.glob(pattern):
            if backup_file not in files:
                files.append(backup_file)

    backups_dir = data_dir / "backups"
    if backups_dir.exists():
        for backup_file in backups_dir.glob("**/*.json"):
            if backup_file not in files:
                files.append(backup_file)

    backup_dir = data_dir.parent / "backup"
    if backup_dir.exists():
        for pattern in ["**/graph_*.json", "**/memory_*.json"]:
            for backup_file in backup_dir.glob(pattern):
                if backup_file not in files:
                    files.append(backup_file)

    return sorted(files, key=lambda f: f.stat().st_mtime, reverse=True)


def load_graph_data_from_file(file_path: Path | None = None) -> dict[str, Any]:
    """从磁盘加载图数据"""
    global graph_data_cache, current_data_file

    if file_path and file_path != current_data_file:
        graph_data_cache = None
        current_data_file = file_path

    if graph_data_cache:
        return graph_data_cache

    try:
        graph_file = current_data_file
        if not graph_file:
            available_files = find_available_data_files()
            if not available_files:
                return {"error": "未找到数据文件", "nodes": [], "edges": [], "stats": {}}
            graph_file = available_files[0]
            current_data_file = graph_file

        if not graph_file.exists():
            return {"error": f"文件不存在: {graph_file}", "nodes": [], "edges": [], "stats": {}}

        with open(graph_file, encoding="utf-8") as f:
            data = orjson.loads(f.read())

        nodes = data.get("nodes", [])
        edges = data.get("edges", [])
        metadata = data.get("metadata", {})

        nodes_dict = {
            node["id"]: {
                **node,
                "label": node.get("content", ""),
                "group": node.get("node_type", ""),
                "title": f"{node.get('node_type', '')}: {node.get('content', '')}",
            }
            for node in nodes
            if node.get("id")
        }

        edges_list = []
        seen_edge_ids = set()
        for edge in edges:
            edge_id = edge.get("id")
            if edge_id and edge_id not in seen_edge_ids:
                edges_list.append(
                    {
                        **edge,
                        "from": edge.get("source", edge.get("source_id")),
                        "to": edge.get("target", edge.get("target_id")),
                        "label": edge.get("relation", ""),
                        "arrows": "to",
                    }
                )
                seen_edge_ids.add(edge_id)

        stats = metadata.get("statistics", {})
        total_memories = stats.get("total_memories", 0)

        graph_data_cache = {
            "nodes": list(nodes_dict.values()),
            "edges": edges_list,
            "memories": [],
            "stats": {
                "total_nodes": len(nodes_dict),
                "total_edges": len(edges_list),
                "total_memories": total_memories,
            },
            "current_file": str(graph_file),
            "file_size": graph_file.stat().st_size,
            "file_modified": datetime.fromtimestamp(graph_file.stat().st_mtime).isoformat(),
        }
        return graph_data_cache

    except Exception as e:
        import traceback

        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"加载图数据失败: {e}")


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """主页面"""
    return templates.TemplateResponse("visualizer.html", {"request": request})


def _format_graph_data_from_manager(memory_manager) -> dict[str, Any]:
    """从 MemoryManager 提取并格式化图数据"""
    if not memory_manager.graph_store:
        return {"nodes": [], "edges": [], "memories": [], "stats": {}}

    all_memories = memory_manager.graph_store.get_all_memories()
    nodes_dict = {}
    edges_dict = {}
    memory_info = []

    for memory in all_memories:
        memory_info.append(
            {
                "id": memory.id,
                "type": memory.memory_type.value,
                "importance": memory.importance,
                "text": memory.to_text(),
            }
        )
        for node in memory.nodes:
            if node.id not in nodes_dict:
                nodes_dict[node.id] = {
                    "id": node.id,
                    "label": node.content,
                    "type": node.node_type.value,
                    "group": node.node_type.name,
                    "title": f"{node.node_type.value}: {node.content}",
                }
        for edge in memory.edges:
            if edge.id not in edges_dict:
                edges_dict[edge.id] = {
                    "id": edge.id,
                    "from": edge.source_id,
                    "to": edge.target_id,
                    "label": edge.relation,
                    "arrows": "to",
                    "memory_id": memory.id,
                }

    edges_list = list(edges_dict.values())

    stats = memory_manager.get_statistics()
    return {
        "nodes": list(nodes_dict.values()),
        "edges": edges_list,
        "memories": memory_info,
        "stats": {
            "total_nodes": stats.get("total_nodes", 0),
            "total_edges": stats.get("total_edges", 0),
            "total_memories": stats.get("total_memories", 0),
        },
        "current_file": "memory_manager (实时数据)",
    }


@router.get("/api/graph/full")
async def get_full_graph():
    """获取完整记忆图数据"""
    try:
        from src.memory_graph.manager_singleton import get_memory_manager

        memory_manager = get_memory_manager()

        data = {}
        if memory_manager and memory_manager._initialized:
            data = _format_graph_data_from_manager(memory_manager)
        else:
            # 如果内存管理器不可用，则从文件加载
            data = await load_graph_data_from_file()

        return JSONResponse(content={"success": True, "data": data})
    except Exception as e:
        import traceback

        traceback.print_exc()
        return JSONResponse(content={"success": False, "error": str(e)}, status_code=500)


@router.get("/api/graph/summary")
async def get_graph_summary():
    """获取图的摘要信息（仅统计数据，不包含节点和边）"""
    try:
        from src.memory_graph.manager_singleton import get_memory_manager

        memory_manager = get_memory_manager()

        if memory_manager and memory_manager._initialized:
            stats = memory_manager.get_statistics()
            return JSONResponse(content={"success": True, "data": {
                "stats": {
                    "total_nodes": stats.get("total_nodes", 0),
                    "total_edges": stats.get("total_edges", 0),
                    "total_memories": stats.get("total_memories", 0),
                },
                "current_file": "memory_manager (实时数据)",
            }})
        else:
            data = await load_graph_data_from_file()
            return JSONResponse(content={"success": True, "data": {
                "stats": data.get("stats", {}),
                "current_file": data.get("current_file", ""),
            }})
    except Exception as e:
        return JSONResponse(content={"success": False, "error": str(e)}, status_code=500)


@router.get("/api/graph/paginated")
async def get_paginated_graph(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(500, ge=100, le=2000, description="每页节点数"),
    min_importance: float = Query(0.0, ge=0.0, le=1.0, description="最小重要性阈值"),
    node_types: str | None = Query(None, description="节点类型过滤，逗号分隔"),
):
    """分页获取图数据，支持重要性过滤"""
    try:
        from src.memory_graph.manager_singleton import get_memory_manager

        memory_manager = get_memory_manager()

        # 获取完整数据
        if memory_manager and memory_manager._initialized:
            full_data = _format_graph_data_from_manager(memory_manager)
        else:
            full_data = await load_graph_data_from_file()

        nodes = full_data.get("nodes", [])
        edges = full_data.get("edges", [])

        # 过滤节点类型
        if node_types:
            allowed_types = set(node_types.split(","))
            nodes = [n for n in nodes if n.get("group") in allowed_types]

        # 按重要性排序（如果有importance字段）
        nodes_with_importance = []
        for node in nodes:
            # 计算节点重要性（连接的边数）
            edge_count = sum(1 for e in edges if e.get("from") == node["id"] or e.get("to") == node["id"])
            importance = edge_count / max(len(edges), 1)
            if importance >= min_importance:
                node["importance"] = importance
                nodes_with_importance.append(node)

        # 按重要性降序排序
        nodes_with_importance.sort(key=lambda x: x.get("importance", 0), reverse=True)

        # 分页
        total_nodes = len(nodes_with_importance)
        total_pages = (total_nodes + page_size - 1) // page_size
        start_idx = (page - 1) * page_size
        end_idx = min(start_idx + page_size, total_nodes)

        paginated_nodes = nodes_with_importance[start_idx:end_idx]
        node_ids = set(n["id"] for n in paginated_nodes)

        # 只保留连接分页节点的边
        paginated_edges = [
            e for e in edges
            if e.get("from") in node_ids and e.get("to") in node_ids
        ]

        return JSONResponse(content={"success": True, "data": {
            "nodes": paginated_nodes,
            "edges": paginated_edges,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total_nodes": total_nodes,
                "total_pages": total_pages,
                "has_next": page < total_pages,
                "has_prev": page > 1,
            },
            "stats": {
                "total_nodes": total_nodes,
                "total_edges": len(paginated_edges),
                "total_memories": full_data.get("stats", {}).get("total_memories", 0),
            },
        }})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse(content={"success": False, "error": str(e)}, status_code=500)


@router.get("/api/graph/clustered")
async def get_clustered_graph(
    max_nodes: int = Query(300, ge=50, le=1000, description="最大节点数"),
    cluster_threshold: int = Query(10, ge=2, le=50, description="聚类阈值")
):
    """获取聚类简化后的图数据"""
    try:
        from src.memory_graph.manager_singleton import get_memory_manager

        memory_manager = get_memory_manager()

        # 获取完整数据
        if memory_manager and memory_manager._initialized:
            full_data = _format_graph_data_from_manager(memory_manager)
        else:
            full_data = await load_graph_data_from_file()

        nodes = full_data.get("nodes", [])
        edges = full_data.get("edges", [])

        # 如果节点数小于阈值，直接返回
        if len(nodes) <= max_nodes:
            return JSONResponse(content={"success": True, "data": {
                "nodes": nodes,
                "edges": edges,
                "stats": full_data.get("stats", {}),
                "clustered": False,
            }})

        # 执行聚类
        clustered_data = _cluster_graph_data(nodes, edges, max_nodes, cluster_threshold)

        return JSONResponse(content={"success": True, "data": {
            **clustered_data,
            "stats": {
                "original_nodes": len(nodes),
                "original_edges": len(edges),
                "clustered_nodes": len(clustered_data["nodes"]),
                "clustered_edges": len(clustered_data["edges"]),
                "total_memories": full_data.get("stats", {}).get("total_memories", 0),
            },
            "clustered": True,
        }})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse(content={"success": False, "error": str(e)}, status_code=500)


def _cluster_graph_data(nodes: list[dict], edges: list[dict], max_nodes: int, cluster_threshold: int) -> dict:
    """简单的图聚类算法：按类型和连接度聚类"""
    # 构建邻接表
    adjacency = defaultdict(set)
    for edge in edges:
        adjacency[edge["from"]].add(edge["to"])
        adjacency[edge["to"]].add(edge["from"])

    # 按类型分组
    type_groups = defaultdict(list)
    for node in nodes:
        type_groups[node.get("group", "UNKNOWN")].append(node)

    clustered_nodes = []
    clustered_edges = []
    node_mapping = {}  # 原始节点ID -> 聚类节点ID

    for node_type, type_nodes in type_groups.items():
        # 如果该类型节点少于阈值，直接保留
        if len(type_nodes) <= cluster_threshold:
            for node in type_nodes:
                clustered_nodes.append(node)
                node_mapping[node["id"]] = node["id"]
        else:
            # 按连接度排序，保留最重要的节点
            node_importance = []
            for node in type_nodes:
                importance = len(adjacency[node["id"]])
                node_importance.append((node, importance))

            node_importance.sort(key=lambda x: x[1], reverse=True)

            # 保留前N个重要节点
            keep_count = min(len(type_nodes), max_nodes // len(type_groups))
            for node, importance in node_importance[:keep_count]:
                clustered_nodes.append(node)
                node_mapping[node["id"]] = node["id"]

            # 其余节点聚合为一个超级节点
            if len(node_importance) > keep_count:
                clustered_node_ids = [n["id"] for n, _ in node_importance[keep_count:]]
                cluster_id = f"cluster_{node_type}_{len(clustered_nodes)}"
                cluster_label = f"{node_type} 集群 ({len(clustered_node_ids)}个节点)"

                clustered_nodes.append({
                    "id": cluster_id,
                    "label": cluster_label,
                    "group": node_type,
                    "title": f"包含 {len(clustered_node_ids)} 个{node_type}节点",
                    "is_cluster": True,
                    "cluster_size": len(clustered_node_ids),
                    "clustered_nodes": clustered_node_ids[:10],  # 只保留前10个用于展示
                })

                for node_id in clustered_node_ids:
                    node_mapping[node_id] = cluster_id

    # 重建边（去重）
    edge_set = set()
    for edge in edges:
        from_id = node_mapping.get(edge["from"])
        to_id = node_mapping.get(edge["to"])

        if from_id and to_id and from_id != to_id:
            edge_key = tuple(sorted([from_id, to_id]))
            if edge_key not in edge_set:
                edge_set.add(edge_key)
                clustered_edges.append({
                    "id": f"{from_id}_{to_id}",
                    "from": from_id,
                    "to": to_id,
                    "label": edge.get("label", ""),
                    "arrows": "to",
                })

    return {
        "nodes": clustered_nodes,
        "edges": clustered_edges,
    }


@router.get("/api/files")
async def list_files_api():
    """列出所有可用的数据文件"""
    try:
        files = find_available_data_files()
        file_list = []
        for f in files:
            stat = f.stat()
            file_list.append(
                {
                    "path": str(f),
                    "name": f.name,
                    "size": stat.st_size,
                    "size_kb": round(stat.st_size / 1024, 2),
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    "modified_readable": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                    "is_current": str(f) == str(current_data_file) if current_data_file else False,
                }
            )

        return JSONResponse(
            content={
                "success": True,
                "files": file_list,
                "count": len(file_list),
                "current_file": str(current_data_file) if current_data_file else None,
            }
        )
    except Exception as e:
        # 增加日志记录
        # logger.error(f"列出数据文件失败: {e}", exc_info=True)
        return JSONResponse(content={"success": False, "error": str(e)}, status_code=500)


@router.post("/select_file")
async def select_file(request: Request):
    """选择要加载的数据文件"""
    global graph_data_cache, current_data_file
    try:
        data = await request.json()
        file_path = data.get("file_path")
        if not file_path:
            raise HTTPException(status_code=400, detail="未提供文件路径")

        file_to_load = Path(file_path)
        if not file_to_load.exists():
            raise HTTPException(status_code=404, detail=f"文件不存在: {file_path}")

        graph_data_cache = None
        current_data_file = file_to_load
        graph_data = await load_graph_data_from_file(file_to_load)

        return JSONResponse(
            content={
                "success": True,
                "message": f"已切换到文件: {file_to_load.name}",
                "stats": graph_data.get("stats", {}),
            }
        )
    except Exception as e:
        return JSONResponse(content={"success": False, "error": str(e)}, status_code=500)


@router.get("/reload")
async def reload_data():
    """重新加载数据"""
    global graph_data_cache
    graph_data_cache = None
    data = await load_graph_data_from_file()
    return JSONResponse(content={"success": True, "message": "数据已重新加载", "stats": data.get("stats", {})})


@router.get("/api/search")
async def search_memories(q: str, limit: int = 50):
    """搜索记忆"""
    try:
        from src.memory_graph.manager_singleton import get_memory_manager

        memory_manager = get_memory_manager()

        results = []
        if memory_manager and memory_manager._initialized and memory_manager.graph_store:
            # 从 memory_manager 搜索
            all_memories = memory_manager.graph_store.get_all_memories()
            for memory in all_memories:
                if q.lower() in memory.to_text().lower():
                    node_ids = [node.id for node in memory.nodes]
                    results.append(
                        {
                            "id": memory.id,
                            "type": memory.memory_type.value,
                            "importance": memory.importance,
                            "text": memory.to_text(),
                            "node_ids": node_ids,  # 返回关联的节点ID
                        }
                    )
        else:
            # 从文件加载的数据中搜索 (降级方案)
            # 注意：此模式下无法直接获取关联节点，前端需要做兼容处理
            data = await load_graph_data_from_file()
            for memory in data.get("memories", []):
                if q.lower() in memory.get("text", "").lower():
                    results.append(memory)  # node_ids 可能不存在

        return JSONResponse(
            content={
                "success": True,
                "data": {
                    "results": results[:limit],
                    "count": len(results),
                },
            }
        )
    except Exception as e:
        # 确保即使在异常情况下也返回 data 字段
        return JSONResponse(
            content={"success": False, "error": str(e), "data": {"results": [], "count": 0}},
            status_code=500,
        )


@router.get("/api/stats")
async def get_statistics():
    """获取统计信息"""
    try:
        data = await load_graph_data_from_file()

        node_types = {}
        memory_types = {}

        for node in data["nodes"]:
            node_type = node.get("type", "Unknown")
            node_types[node_type] = node_types.get(node_type, 0) + 1

        for memory in data.get("memories", []):
            mem_type = memory.get("type", "Unknown")
            memory_types[mem_type] = memory_types.get(mem_type, 0) + 1

        stats = data.get("stats", {})
        stats["node_types"] = node_types
        stats["memory_types"] = memory_types

        return JSONResponse(content={"success": True, "data": stats})
    except Exception as e:
        return JSONResponse(content={"success": False, "error": str(e)}, status_code=500)
