# 记忆图可视化工具统一启动脚本
param(
    [switch]$Simple,
    [switch]$Full,
    [switch]$Generate,
    [switch]$Test
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $ScriptDir)
Set-Location $ProjectRoot

function Get-Python {
    $paths = @(".venv\Scripts\python.exe", "venv\Scripts\python.exe")
    foreach ($p in $paths) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

$python = Get-Python
if (-not $python) {
    Write-Host "ERROR: Virtual environment not found" -ForegroundColor Red
    exit 1
}

if ($Simple) {
    Write-Host "Starting Simple Server on http://127.0.0.1:5001" -ForegroundColor Green
    & $python "$ScriptDir\visualizer_simple.py"
}
elseif ($Full) {
    Write-Host "Starting Full Server on http://127.0.0.1:5000" -ForegroundColor Green
    & $python "$ScriptDir\visualizer_server.py"
}
elseif ($Generate) {
    & $python "$ScriptDir\generate_sample_data.py"
}
elseif ($Test) {
    & $python "$ScriptDir\test_visualizer.py"
}
else {
    Write-Host "MoFox Bot - Memory Graph Visualizer" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "[1] Start Simple Server (Recommended)"
    Write-Host "[2] Start Full Server"
    Write-Host "[3] Generate Test Data"
    Write-Host "[4] Run Tests"
    Write-Host "[Q] Quit"
    Write-Host ""
    $choice = Read-Host "Select"
    
    switch ($choice) {
        "1" { & $python "$ScriptDir\visualizer_simple.py" }
        "2" { & $python "$ScriptDir\visualizer_server.py" }
        "3" { & $python "$ScriptDir\generate_sample_data.py" }
        "4" { & $python "$ScriptDir\test_visualizer.py" }
        default { exit 0 }
    }
}
