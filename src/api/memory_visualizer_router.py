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


def load_graph_data_from_file(
    file_path: Optional[Path] = None,
    nodes_page: Optional[int] = None,
    nodes_per_page: Optional[int] = None,
    edges_page: Optional[int] = None,
    edges_per_page: Optional[int] = None,
) -> Dict[str, Any]:
    """
    从磁盘加载图数据, 支持分页。
    如果不提供分页参数, 则加载并缓存所有数据。
    """
    global graph_data_cache, current_data_file

    # 如果是请求分页数据, 则不使用缓存的全量数据
    is_paged_request = nodes_page is not None or edges_page is not None

    if file_path and file_path != current_data_file:
        graph_data_cache = None
        current_data_file = file_path

    if graph_data_cache and not is_paged_request:
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

        # 只有在没有缓存时才从磁盘读取和处理文件
        if not graph_data_cache:
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
                "memories": [], # TODO: 未来也可以考虑分页加载记忆
                "stats": {
                    "total_nodes": len(nodes_dict),
                    "total_edges": len(edges_list),
                    "total_memories": total_memories,
                },
                "current_file": str(graph_file),
                "file_size": graph_file.stat().st_size,
                "file_modified": datetime.fromtimestamp(graph_file.stat().st_mtime).isoformat(),
            }

        # 如果是分页请求, 则从缓存中切片数据
        if is_paged_request:
            paged_data = graph_data_cache.copy() # 浅拷贝一份, 避免修改缓存
            
            # 分页节点
            if nodes_page is not None and nodes_per_page is not None:
                node_start = (nodes_page - 1) * nodes_per_page
                node_end = node_start + nodes_per_page
                paged_data["nodes"] = graph_data_cache["nodes"][node_start:node_end]
            
            # 分页边
            if edges_page is not None and edges_per_page is not None:
                edge_start = (edges_page - 1) * edges_per_page
                edge_end = edge_start + edges_per_page
                paged_data["edges"] = graph_data_cache["edges"][edge_start:edge_end]
                
            return paged_data

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

@router.get("/api/graph/paged")
async def get_paged_graph(
    nodes_page: int = 1, nodes_per_page: int = 100, edges_page: int = 1, edges_per_page: int = 200
):
    """获取分页的记忆图数据"""
    try:
        # 确保全量数据已加载到缓存
        full_data = load_graph_data_from_file()
        if "error" in full_data:
             raise HTTPException(status_code=404, detail=full_data["error"])

        # 从缓存中获取全量数据
        all_nodes = full_data.get("nodes", [])
        all_edges = full_data.get("edges", [])
        total_nodes = len(all_nodes)
        total_edges = len(all_edges)
        
        # 计算节点分页
        node_start = (nodes_page - 1) * nodes_per_page
        node_end = node_start + nodes_per_page
        paginated_nodes = all_nodes[node_start:node_end]

        # 计算边分页
        edge_start = (edges_page - 1) * edges_per_page
        edge_end = edge_start + edges_per_page
        paginated_edges = all_edges[edge_start:edge_end]

        return JSONResponse(
            content={
                "success": True,
                "data": {
                    "nodes": paginated_nodes,
                    "edges": paginated_edges,
                    "pagination": {
                        "nodes": {
                            "page": nodes_page,
                            "per_page": nodes_per_page,
                            "total": total_nodes,
                            "total_pages": (total_nodes + nodes_per_page - 1) // nodes_per_page,
                        },
                        "edges": {
                            "page": edges_page,
                            "per_page": edges_per_page,
                            "total": total_edges,
                            "total_pages": (total_edges + edges_per_page - 1) // edges_per_page,
                        },
                    },
                },
            }
        )
    except Exception as e:
        return JSONResponse(content={"success": False, "error": str(e)}, status_code=500)


@router.get("/api/graph/full")
async def get_full_graph_deprecated():
    """
    (已废弃) 获取完整记忆图数据。
    此接口现在只返回第一页的数据, 请使用 /api/graph/paged 进行分页获取。
    """
    return await get_paged_graph(nodes_page=1, nodes_per_page=100, edges_page=1, edges_per_page=200)


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
