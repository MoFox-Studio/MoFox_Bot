#!/bin/bash
# 记忆图可视化工具启动脚本 - Bash版本 (Linux/Mac)

echo "======================================================================"
echo "🦊 MoFox Bot - 记忆图可视化工具"
echo "======================================================================"
echo ""

# 检查虚拟环境
VENV_PYTHON=".venv/bin/python"
if [ ! -f "$VENV_PYTHON" ]; then
    echo "❌ 未找到虚拟环境: $VENV_PYTHON"
    echo ""
    echo "请先创建虚拟环境:"
    echo "  python -m venv .venv"
    echo "  source .venv/bin/activate"
    echo "  pip install -r requirements.txt"
    echo ""
    exit 1
fi

echo "✅ 使用虚拟环境: $VENV_PYTHON"
echo ""

# 检查依赖
echo "🔍 检查依赖..."
$VENV_PYTHON -c "import flask; import flask_cors" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "⚠️  缺少依赖，正在安装..."
    $VENV_PYTHON -m pip install flask flask-cors --quiet
    if [ $? -ne 0 ]; then
        echo "❌ 安装依赖失败"
        exit 1
    fi
    echo "✅ 依赖安装完成"
fi

echo "✅ 依赖检查完成"
echo ""

# 显示信息
echo "📊 启动可视化服务器..."
echo "🌐 访问地址: http://127.0.0.1:5001"
echo "⏹️  按 Ctrl+C 停止服务器"
echo ""
echo "======================================================================"
echo ""

# 启动服务器
$VENV_PYTHON "tools/memory_visualizer/visualizer_simple.py"

echo ""
echo "👋 服务器已停止"
