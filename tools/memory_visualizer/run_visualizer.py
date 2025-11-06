#!/usr/bin/env python3
"""
记忆图可视化工具启动脚本

快速启动记忆图可视化Web服务器
"""

import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from tools.memory_visualizer.visualizer_server import run_server

if __name__ == '__main__':
    print("=" * 60)
    print("🦊 MoFox Bot - 记忆图可视化工具")
    print("=" * 60)
    print()
    print("📊 启动可视化服务器...")
    print("🌐 访问地址: http://127.0.0.1:5000")
    print("⏹️  按 Ctrl+C 停止服务器")
    print()
    print("=" * 60)
    
    try:
        run_server(
            host='127.0.0.1',
            port=5000,
            debug=True
        )
    except KeyboardInterrupt:
        print("\n\n👋 服务器已停止")
    except Exception as e:
        print(f"\n❌ 启动失败: {e}")
        sys.exit(1)
