$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
$env:DATA_DIR = Join-Path (Get-Location) '.webui'
New-Item -ItemType Directory -Force -Path $env:DATA_DIR | Out-Null
$secretPath = Join-Path $env:DATA_DIR 'secret.key'
if (-not (Test-Path -LiteralPath $secretPath)) {
    [Guid]::NewGuid().ToString('N') + [Guid]::NewGuid().ToString('N') | Set-Content -LiteralPath $secretPath
}
$env:WEBUI_SECRET_KEY = (Get-Content -LiteralPath $secretPath -Raw).Trim()
$env:OFFLINE_MODE = 'true'
$env:ENABLE_OLLAMA_API = 'false'
$env:ENABLE_OPENAI_API = 'false'
$env:ANONYMIZED_TELEMETRY = 'false'
$env:DO_NOT_TRACK = 'true'
Write-Host 'Open WebUI: http://127.0.0.1:8080 ；Ctrl+C 停止。此配置只使用 MAGI Pipe。'
& .\.venv-webui\Scripts\open-webui.exe serve --host 127.0.0.1 --port 8080
