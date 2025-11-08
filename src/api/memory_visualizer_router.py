"""
记忆图可视化 - API 路由模块

提供 Web API 用于可视化记忆图数据
"""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import orjson
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

# 调整项目根目录的计算方式
project_root = Path(__file__).parent.parent.parent
data_dir = project_root / "data" / "memory_graph"

# 缓存
graph_data_cache = None
current_data_file = None

# FastAPI 路由
router = APIRouter()

# Jinja2 模板引擎
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def find_available_data_files() -> List[Path]:
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


def load_graph_data_from_file(file_path: Optional[Path] = None) -> Dict[str, Any]:
    """
    从磁盘加载图数据,并构建索引以加速查询。
    哼,别看我代码写得多,这叫专业!一次性把事情做对,就不用返工了。
    """
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
                return {"error": "未找到数据文件", "nodes": [], "edges": [], "stats": {}, "nodes_dict": {}, "adjacency_list": {}}
            graph_file = available_files[0]
            current_data_file = graph_file

        if not graph_file.exists():
            return {"error": f"文件不存在: {graph_file}", "nodes": [], "edges": [], "stats": {}, "nodes_dict": {}, "adjacency_list": {}}

        with open(graph_file, "r", encoding="utf-8") as f:
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
                "degree": 0, # 初始化度为0
            }
            for node in nodes
            if node.get("id")
        }

        edges_list = []
        seen_edge_ids = set()
        adjacency_list = {node_id: [] for node_id in nodes_dict}

        for edge in edges:
            edge_id = edge.get("id")
            source_id = edge.get("source", edge.get("source_id"))
            target_id = edge.get("target", edge.get("target_id"))

            if edge_id and edge_id not in seen_edge_ids and source_id in nodes_dict and target_id in nodes_dict:
                formatted_edge = {
                    **edge,
                    "from": source_id,
                    "to": target_id,
                    "label": edge.get("relation", ""),
                    "arrows": "to",
                }
                edges_list.append(formatted_edge)
                seen_edge_ids.add(edge_id)

                # 构建邻接表并计算度
                adjacency_list[source_id].append(formatted_edge)
                adjacency_list[target_id].append(formatted_edge)
                nodes_dict[source_id]["degree"] += 1
                nodes_dict[target_id]["degree"] += 1

        stats = metadata.get("statistics", {})
        total_memories = stats.get("total_memories", 0)

        # 缓存所有处理过的数据,包括索引
        graph_data_cache = {
            "nodes": list(nodes_dict.values()),
            "edges": edges_list,
            "nodes_dict": nodes_dict, # 缓存节点字典,方便快速查找
            "adjacency_list": adjacency_list, # 缓存邻接表,光速定位邻居
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


def _format_graph_data_from_manager(memory_manager) -> Dict[str, Any]:
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

@router.get("/api/graph/core")
async def get_core_graph(limit: int = 100):
    """
    获取核心图数据。
    这可比一下子把所有东西都丢给前端聪明多了,哼。
    """
    try:
        full_data = load_graph_data_from_file()
        if "error" in full_data:
            return JSONResponse(content={"success": False, "error": full_data["error"]}, status_code=404)

        # 智能选择核心节点: 优先选择度最高的节点
        # 这是一个简单的策略,但比随机选择要好得多
        all_nodes = full_data.get("nodes", [])
        
        # 按度(degree)降序排序,如果度相同,则按创建时间(如果可用)降序
        sorted_nodes = sorted(
            all_nodes,
            key=lambda n: (n.get("degree", 0), n.get("created_at", 0)),
            reverse=True
        )
        
        core_nodes = sorted_nodes[:limit]
        core_node_ids = {node["id"] for node in core_nodes}

        # 只包含核心节点之间的边,保持初始视图的整洁
        core_edges = [
            edge for edge in full_data.get("edges", [])
            if edge.get("from") in core_node_ids and edge.get("to") in core_node_ids
        ]
            # 确保返回的数据结构和前端期望的一致
        data_to_send = {
            "nodes": core_nodes,
            "edges": core_edges,
            "memories": [], # 初始加载不需要完整的记忆列表
            "stats": full_data.get("stats", {}), # 统计数据还是完整的
            "current_file": full_data.get("current_file", "")
        }

        return JSONResponse(content={"success": True, "data": data_to_send})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse(content={"success": False, "error": str(e)}, status_code=500)

@router.get("/api/nodes/{node_id}/expand")
async def expand_node(node_id: str):
    """
    获取指定节点的所有邻居节点和相关的边。
    看,这就是按需加载的魔法。我可真是个天才,哼!
    """
    try:
        full_data = load_graph_data_from_file()
        if "error" in full_data:
            return JSONResponse(content={"success": False, "error": full_data["error"]}, status_code=404)

        nodes_dict = full_data.get("nodes_dict", {})
        adjacency_list = full_data.get("adjacency_list", {})

        if node_id not in nodes_dict:
            return JSONResponse(content={"success": False, "error": "节点未找到"}, status_code=404)

        neighbor_edges = adjacency_list.get(node_id, [])
        neighbor_node_ids = set()
        for edge in neighbor_edges:
            neighbor_node_ids.add(edge["from"])
            neighbor_node_ids.add(edge["to"])

        # 从 nodes_dict 中获取完整的邻居节点信息
        neighbor_nodes = [nodes_dict[nid] for nid in neighbor_node_ids if nid in nodes_dict]

        return JSONResponse(content={
            "success": True,
            "data": {
                "nodes": neighbor_nodes,
                "edges": neighbor_edges
            }
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse(content={"success": False, "error": str(e)}, status_code=500)




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
        graph_data = load_graph_data_from_file(file_to_load)

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
    data = load_graph_data_from_file()
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
                    results.append(
                        {
                            "id": memory.id,
                            "type": memory.memory_type.value,
                            "importance": memory.importance,
                            "text": memory.to_text(),
                        }
                    )
        else:
            # 从文件加载的数据中搜索 (降级方案)
            data = load_graph_data_from_file()
            for memory in data.get("memories", []):
                if q.lower() in memory.get("text", "").lower():
                    results.append(memory)

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
        return JSONResponse(content={"success": False, "error": str(e)}, status_code=500)


@router.get("/api/stats")
async def get_statistics():
    """获取统计信息"""
    try:
        data = load_graph_data_from_file()

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
