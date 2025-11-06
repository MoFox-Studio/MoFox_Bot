"""
记忆图可视化 - 独立版本

直接从存储的数据文件生成可视化,无需启动完整的记忆管理器
"""

import json
import sys
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Set

from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# 添加项目根目录
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from flask import Flask, jsonify, render_template_string, request, send_from_directory
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# 数据缓存
graph_data_cache = None
data_dir = project_root / "data" / "memory_graph"
current_data_file = None  # 当前选择的数据文件


def find_available_data_files() -> List[Path]:
    """查找所有可用的记忆图数据文件"""
    files = []
    
    if not data_dir.exists():
        return files
    
    # 查找多种可能的文件名
    possible_files = [
        "graph_store.json",
        "memory_graph.json",
        "graph_data.json",
    ]
    
    for filename in possible_files:
        file_path = data_dir / filename
        if file_path.exists():
            files.append(file_path)
    
    # 查找所有备份文件
    for pattern in ["graph_store_*.json", "memory_graph_*.json", "graph_data_*.json"]:
        for backup_file in data_dir.glob(pattern):
            if backup_file not in files:
                files.append(backup_file)
    
    # 查找backups子目录
    backups_dir = data_dir / "backups"
    if backups_dir.exists():
        for backup_file in backups_dir.glob("**/*.json"):
            if backup_file not in files:
                files.append(backup_file)
    
    # 查找data/backup目录
    backup_dir = data_dir.parent / "backup"
    if backup_dir.exists():
        for backup_file in backup_dir.glob("**/graph_*.json"):
            if backup_file not in files:
                files.append(backup_file)
        for backup_file in backup_dir.glob("**/memory_*.json"):
            if backup_file not in files:
                files.append(backup_file)
    
    return sorted(files, key=lambda f: f.stat().st_mtime, reverse=True)


def load_graph_data(file_path: Optional[Path] = None) -> Dict[str, Any]:
    """从磁盘加载图数据"""
    global graph_data_cache, current_data_file
    
    # 如果指定了新文件，清除缓存
    if file_path is not None and file_path != current_data_file:
        graph_data_cache = None
        current_data_file = file_path
    
    if graph_data_cache is not None:
        return graph_data_cache
    
    try:
        # 确定要加载的文件
        if current_data_file is not None:
            graph_file = current_data_file
        else:
            # 尝试查找可用的数据文件
            available_files = find_available_data_files()
            if not available_files:
                print(f"⚠️  未找到任何图数据文件")
                print(f"📂 搜索目录: {data_dir}")
                return {
                    "nodes": [], 
                    "edges": [], 
                    "memories": [],
                    "stats": {"total_nodes": 0, "total_edges": 0, "total_memories": 0},
                    "error": "未找到数据文件",
                    "available_files": []
                }
            
            # 使用最新的文件
            graph_file = available_files[0]
            current_data_file = graph_file
            print(f"📂 自动选择最新文件: {graph_file}")
        
        if not graph_file.exists():
            print(f"⚠️  图数据文件不存在: {graph_file}")
            return {
                "nodes": [], 
                "edges": [], 
                "memories": [],
                "stats": {"total_nodes": 0, "total_edges": 0, "total_memories": 0},
                "error": f"文件不存在: {graph_file}"
            }
        
        print(f"📂 加载图数据: {graph_file}")
        with open(graph_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # 解析数据
        nodes_dict = {}
        edges_list = []
        memory_info = []
        
        # 实际文件格式是 {nodes: [], edges: [], metadata: {}}
        # 不是 {memories: [{nodes: [], edges: []}]}
        nodes = data.get("nodes", [])
        edges = data.get("edges", [])
        metadata = data.get("metadata", {})
        
        print(f"✅ 找到 {len(nodes)} 个节点, {len(edges)} 条边")
        
        # 处理节点
        for node in nodes:
            node_id = node.get('id', '')
            if node_id and node_id not in nodes_dict:
                memory_ids = node.get('metadata', {}).get('memory_ids', [])
                nodes_dict[node_id] = {
                    'id': node_id,
                    'label': node.get('content', ''),
                    'type': node.get('node_type', ''),
                    'group': extract_group_from_type(node.get('node_type', '')),
                    'title': f"{node.get('node_type', '')}: {node.get('content', '')}",
                    'metadata': node.get('metadata', {}),
                    'created_at': node.get('created_at', ''),
                    'memory_ids': memory_ids,
                }
        
        # 处理边 - 使用集合去重，避免重复的边ID
        existing_edge_ids = set()
        for edge in edges:
            # 边的ID字段可能是 'id' 或 'edge_id'
            edge_id = edge.get('edge_id') or edge.get('id', '')
            # 如果ID为空或已存在，跳过这条边
            if not edge_id or edge_id in existing_edge_ids:
                continue
            
            existing_edge_ids.add(edge_id)
            memory_id = edge.get('metadata', {}).get('memory_id', '')
            
            # 注意: GraphStore 保存的格式使用 'source'/'target', 不是 'source_id'/'target_id'
            edges_list.append({
                'id': edge_id,
                'from': edge.get('source', edge.get('source_id', '')),
                'to': edge.get('target', edge.get('target_id', '')),
                'label': edge.get('relation', ''),
                'type': edge.get('edge_type', ''),
                'importance': edge.get('importance', 0.5),
                'title': f"{edge.get('edge_type', '')}: {edge.get('relation', '')}",
                'arrows': 'to',
                'memory_id': memory_id,
            })
        
        # 从元数据中获取统计信息
        stats = metadata.get('statistics', {})
        total_memories = stats.get('total_memories', 0)
        
        # TODO: 如果需要记忆详细信息,需要从其他地方加载
        # 目前只有节点和边的数据
        
        graph_data_cache = {
            'nodes': list(nodes_dict.values()),
            'edges': edges_list,
            'memories': memory_info,  # 空列表,因为文件中没有记忆详情
            'stats': {
                'total_nodes': len(nodes_dict),
                'total_edges': len(edges_list),
                'total_memories': total_memories,
            },
            'current_file': str(graph_file),
            'file_size': graph_file.stat().st_size,
            'file_modified': datetime.fromtimestamp(graph_file.stat().st_mtime).isoformat(),
        }
        
        print(f"📊 统计: {len(nodes_dict)} 个节点, {len(edges_list)} 条边, {total_memories} 条记忆")
        print(f"📄 数据文件: {graph_file} ({graph_file.stat().st_size / 1024:.2f} KB)")
        return graph_data_cache
        
    except Exception as e:
        print(f"❌ 加载失败: {e}")
        import traceback
        traceback.print_exc()
        return {"nodes": [], "edges": [], "memories": [], "stats": {}}


def extract_group_from_type(node_type: str) -> str:
    """从节点类型提取分组名"""
    # 假设类型格式为 "主体" 或 "SUBJECT"
    type_mapping = {
        '主体': 'SUBJECT',
        '主题': 'TOPIC',
        '客体': 'OBJECT',
        '属性': 'ATTRIBUTE',
        '值': 'VALUE',
    }
    return type_mapping.get(node_type, node_type)


def generate_memory_text(memory: Dict[str, Any]) -> str:
    """生成记忆的文本描述"""
    try:
        nodes = {n['id']: n for n in memory.get('nodes', [])}
        edges = memory.get('edges', [])
        
        subject_id = memory.get('subject_id', '')
        if not subject_id or subject_id not in nodes:
            return f"[记忆 {memory.get('id', '')[:8]}]"
        
        parts = [nodes[subject_id]['content']]
        
        # 找主题节点
        for edge in edges:
            if edge.get('edge_type') == '记忆类型' and edge.get('source_id') == subject_id:
                topic_id = edge.get('target_id', '')
                if topic_id in nodes:
                    parts.append(nodes[topic_id]['content'])
                    
                    # 找客体
                    for e2 in edges:
                        if e2.get('edge_type') == '核心关系' and e2.get('source_id') == topic_id:
                            obj_id = e2.get('target_id', '')
                            if obj_id in nodes:
                                parts.append(f"{e2.get('relation', '')} {nodes[obj_id]['content']}")
                                break
                    break
        
        return " ".join(parts)
    except Exception:
        return f"[记忆 {memory.get('id', '')[:8]}]"


# 使用内嵌的HTML模板(与之前相同)
HTML_TEMPLATE = open(project_root / "tools" / "memory_visualizer" / "templates" / "visualizer.html", 'r', encoding='utf-8').read()


@app.route('/')
def index():
    """主页面"""
    return render_template_string(HTML_TEMPLATE)


@app.route('/api/graph/full')
def get_full_graph():
    """获取完整记忆图数据"""
    try:
        data = load_graph_data()
        return jsonify({
            'success': True,
            'data': data
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/memory/<memory_id>')
def get_memory_detail(memory_id: str):
    """获取记忆详情"""
    try:
        data = load_graph_data()
        memory = next((m for m in data['memories'] if m['id'] == memory_id), None)
        
        if memory is None:
            return jsonify({
                'success': False,
                'error': '记忆不存在'
            }), 404
        
        return jsonify({
            'success': True,
            'data': memory
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/search')
def search_memories():
    """搜索记忆"""
    try:
        query = request.args.get('q', '').lower()
        limit = int(request.args.get('limit', 50))
        
        data = load_graph_data()
        
        # 简单的文本匹配搜索
        results = []
        for memory in data['memories']:
            text = memory.get('text', '').lower()
            if query in text:
                results.append(memory)
        
        return jsonify({
            'success': True,
            'data': {
                'results': results[:limit],
                'count': len(results),
            }
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/stats')
def get_statistics():
    """获取统计信息"""
    try:
        data = load_graph_data()
        
        # 扩展统计信息
        node_types = {}
        memory_types = {}
        
        for node in data['nodes']:
            node_type = node.get('type', 'Unknown')
            node_types[node_type] = node_types.get(node_type, 0) + 1
        
        for memory in data['memories']:
            mem_type = memory.get('type', 'Unknown')
            memory_types[mem_type] = memory_types.get(mem_type, 0) + 1
        
        stats = data.get('stats', {})
        stats['node_types'] = node_types
        stats['memory_types'] = memory_types
        
        return jsonify({
            'success': True,
            'data': stats
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/reload')
def reload_data():
    """重新加载数据"""
    global graph_data_cache
    graph_data_cache = None
    data = load_graph_data()
    return jsonify({
        'success': True,
        'message': '数据已重新加载',
        'stats': data.get('stats', {})
    })


@app.route('/api/files')
def list_files():
    """列出所有可用的数据文件"""
    try:
        files = find_available_data_files()
        file_list = []
        
        for f in files:
            stat = f.stat()
            file_list.append({
                'path': str(f),
                'name': f.name,
                'size': stat.st_size,
                'size_kb': round(stat.st_size / 1024, 2),
                'modified': datetime.fromtimestamp(stat.st_mtime).isoformat(),
                'modified_readable': datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
                'is_current': str(f) == str(current_data_file) if current_data_file else False
            })
        
        return jsonify({
            'success': True,
            'files': file_list,
            'count': len(file_list),
            'current_file': str(current_data_file) if current_data_file else None
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/select_file', methods=['POST'])
def select_file():
    """选择要加载的数据文件"""
    global graph_data_cache, current_data_file
    
    try:
        data = request.get_json()
        file_path = data.get('file_path')
        
        if not file_path:
            return jsonify({
                'success': False,
                'error': '未提供文件路径'
            }), 400
        
        file_path = Path(file_path)
        if not file_path.exists():
            return jsonify({
                'success': False,
                'error': f'文件不存在: {file_path}'
            }), 404
        
        # 清除缓存并加载新文件
        graph_data_cache = None
        current_data_file = file_path
        graph_data = load_graph_data(file_path)
        
        return jsonify({
            'success': True,
            'message': f'已切换到文件: {file_path.name}',
            'stats': graph_data.get('stats', {})
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


def run_server(host: str = '127.0.0.1', port: int = 5001, debug: bool = False):
    """启动服务器"""
    print("=" * 60)
    print("🦊 MoFox Bot - 记忆图可视化工具 (独立版)")
    print("=" * 60)
    print(f"📂 数据目录: {data_dir}")
    print(f"🌐 访问地址: http://{host}:{port}")
    print("⏹️  按 Ctrl+C 停止服务器")
    print("=" * 60)
    print()
    
    # 预加载数据
    load_graph_data()
    
    app.run(host=host, port=port, debug=debug)


if __name__ == '__main__':
    try:
        run_server(debug=True)
    except KeyboardInterrupt:
        print("\n\n👋 服务器已停止")
    except Exception as e:
        print(f"\n❌ 启动失败: {e}")
        sys.exit(1)
