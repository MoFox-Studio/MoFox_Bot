#!/usr/bin/env pwsh
# 记忆图可视化工具启动脚本 - PowerShell版本

Write-Host "=" -NoNewline -ForegroundColor Cyan
Write-Host ("=" * 69) -ForegroundColor Cyan
Write-Host "🦊 MoFox Bot - 记忆图可视化工具" -ForegroundColor Yellow
Write-Host "=" -NoNewline -ForegroundColor Cyan
Write-Host ("=" * 69) -ForegroundColor Cyan
Write-Host ""

# 检查虚拟环境
$venvPath = ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPath)) {
    Write-Host "❌ 未找到虚拟环境: $venvPath" -ForegroundColor Red
    Write-Host ""
    Write-Host "请先创建虚拟环境:" -ForegroundColor Yellow
    Write-Host "  python -m venv .venv" -ForegroundColor Cyan
    Write-Host "  .\.venv\Scripts\Activate.ps1" -ForegroundColor Cyan
    Write-Host "  pip install -r requirements.txt" -ForegroundColor Cyan
    Write-Host ""
    exit 1
}

Write-Host "✅ 使用虚拟环境: $venvPath" -ForegroundColor Green
Write-Host ""

# 检查依赖
Write-Host "🔍 检查依赖..." -ForegroundColor Cyan
& $venvPath -c "import flask; import flask_cors" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "⚠️  缺少依赖，正在安装..." -ForegroundColor Yellow
    & $venvPath -m pip install flask flask-cors --quiet
    if ($LASTEXITCODE -ne 0) {
        Write-Host "❌ 安装依赖失败" -ForegroundColor Red
        exit 1
    }
    Write-Host "✅ 依赖安装完成" -ForegroundColor Green
}

Write-Host "✅ 依赖检查完成" -ForegroundColor Green
Write-Host ""

# 显示信息
Write-Host "📊 启动可视化服务器..." -ForegroundColor Cyan
Write-Host "🌐 访问地址: " -NoNewline -ForegroundColor White
Write-Host "http://127.0.0.1:5001" -ForegroundColor Blue
Write-Host "⏹️  按 Ctrl+C 停止服务器" -ForegroundColor Yellow
Write-Host ""
Write-Host "=" -NoNewline -ForegroundColor Cyan
Write-Host ("=" * 69) -ForegroundColor Cyan
Write-Host ""

# 启动服务器
try {
    & $venvPath "tools\memory_visualizer\visualizer_simple.py"
}
catch {
    Write-Host ""
    Write-Host "❌ 启动失败: $_" -ForegroundColor Red
    exit 1
}
finally {
    Write-Host ""
    Write-Host "👋 服务器已停止" -ForegroundColor Yellow
}
