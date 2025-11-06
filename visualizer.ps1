#!/usr/bin/env pwsh
# ======================================================================
# 记忆图可视化工具 - 快捷启动脚本
# ======================================================================
# 此脚本是快捷方式，实际脚本位于 tools/memory_visualizer/ 目录
# ======================================================================

$visualizerScript = Join-Path $PSScriptRoot "tools\memory_visualizer\visualizer.ps1"

if (Test-Path $visualizerScript) {
    & $visualizerScript @args
} else {
    Write-Host "❌ 错误：找不到可视化工具脚本" -ForegroundColor Red
    Write-Host "   预期位置: $visualizerScript" -ForegroundColor Yellow
    exit 1
}
