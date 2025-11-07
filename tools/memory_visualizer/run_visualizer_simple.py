"""
快速启动脚本 - 记忆图可视化工具 (独立版)

使用说明:
1. 直接运行此脚本启动可视化服务器
2. 工具会自动搜索可用的数据文件
3. 如果找到多个文件,会使用最新的文件
4. 你也可以在Web界面中选择其他文件
"""

import sys
from pathlib import Path

# 添加项目根目录
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

if __name__ == '__main__':
    print("=" * 70)
    print("🦊 MoFox Bot - 记忆图可视化工具 (独立版)")
    print("=" * 70)
    print()
    print("✨ 特性:")
    print("  • 自动搜索可用的数据文件")
    print("  • 支持在Web界面中切换文件")
    print("  • 快速启动,无需完整初始化")
    print()
    print("=" * 70)
    
    try:
        from tools.memory_visualizer.visualizer_simple import run_server
        run_server(host='127.0.0.1', port=5001, debug=True)
    except KeyboardInterrupt:
        print("\n\n👋 服务器已停止")
    except Exception as e:
        print(f"\n❌ 启动失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
