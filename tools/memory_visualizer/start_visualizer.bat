@echo off
REM 记忆图可视化工具启动脚本 - CMD版本

echo ======================================================================
echo 🦊 MoFox Bot - 记忆图可视化工具
echo ======================================================================
echo.

REM 检查虚拟环境
set VENV_PYTHON=.venv\Scripts\python.exe
if not exist "%VENV_PYTHON%" (
    echo ❌ 未找到虚拟环境: %VENV_PYTHON%
    echo.
    echo 请先创建虚拟环境:
    echo   python -m venv .venv
    echo   .venv\Scripts\activate.bat
    echo   pip install -r requirements.txt
    echo.
    exit /b 1
)

echo ✅ 使用虚拟环境: %VENV_PYTHON%
echo.

REM 检查依赖
echo 🔍 检查依赖...
"%VENV_PYTHON%" -c "import flask; import flask_cors" 2>nul
if errorlevel 1 (
    echo ⚠️  缺少依赖，正在安装...
    "%VENV_PYTHON%" -m pip install flask flask-cors --quiet
    if errorlevel 1 (
        echo ❌ 安装依赖失败
        exit /b 1
    )
    echo ✅ 依赖安装完成
)

echo ✅ 依赖检查完成
echo.

REM 显示信息
echo 📊 启动可视化服务器...
echo 🌐 访问地址: http://127.0.0.1:5001
echo ⏹️  按 Ctrl+C 停止服务器
echo.
echo ======================================================================
echo.

REM 启动服务器
"%VENV_PYTHON%" "tools\memory_visualizer\visualizer_simple.py"

echo.
echo 👋 服务器已停止
