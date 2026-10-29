param([string]$Config = "magi.harness.toml")
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not $env:MAGI_API_TOKEN) {
    $env:MAGI_API_TOKEN = [Guid]::NewGuid().ToString('N') + [Guid]::NewGuid().ToString('N')
    Write-Host "本次服务凭据（粘贴到本机聊天页或 Pipe Valves）：$env:MAGI_API_TOKEN"
}
$env:MAGI_CONFIG = $Config
Write-Host "MAGI: http://127.0.0.1:8765 ；Ctrl+C 停止。单 worker，不使用 reload。"
& .\.venv-harness\Scripts\python.exe -X utf8 -m magi.web
