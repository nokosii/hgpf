param(
    [switch]$Seed
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $VenvPython)) {
    $RuntimePython = 'C:\Users\User\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    if (-not (Test-Path -LiteralPath $RuntimePython)) { $RuntimePython = 'python' }
    & $RuntimePython -m venv (Join-Path $ProjectRoot '.venv')
    & $VenvPython -m pip install --upgrade pip
    & $VenvPython -m pip install -r (Join-Path $ProjectRoot 'requirements.txt')
}

Push-Location $ProjectRoot
try {
    if ($Seed) { & $VenvPython -m app.seed }
    Write-Host 'HGPF 系統：http://127.0.0.1:8765' -ForegroundColor Green
    Write-Host '按 Ctrl+C 停止本機服務。'
    & $VenvPython -m app
}
finally {
    Pop-Location
}
