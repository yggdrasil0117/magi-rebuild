param(
    [string]$Config = "magi.qianwen.toml",
    [ValidateRange(1, 65535)][int]$Port = 8765
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
$profileJson = & .\.venv-harness\Scripts\python.exe -c "import json,sys; from pathlib import Path; from magi.harness.config import load_config; c=load_config(Path(sys.argv[1])); print(json.dumps({'provider':c.provider,'api_key_env':c.api_key_env,'embedding_enabled':c.embedding_enabled}))" $Config
if ($LASTEXITCODE -ne 0) { throw '模型配置加载失败' }
$modelProfile = $profileJson | ConvertFrom-Json
$enteredKey = $false
if ($modelProfile.provider -eq 'qianwen' -and -not [Environment]::GetEnvironmentVariable($modelProfile.api_key_env, 'Process')) {
    $secureKey = Read-Host '输入千问 API Key（不显示、不保存）' -AsSecureString
    $keyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
    try {
        [Environment]::SetEnvironmentVariable($modelProfile.api_key_env, [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPointer), 'Process')
        $enteredKey = $true
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPointer)
        $secureKey.Dispose()
    }
}
$enteredEmbeddingKey = $false
if ($modelProfile.embedding_enabled -and -not $env:SILICONFLOW_API_KEY) {
    $embeddingKey = Read-Host '输入硅基流动 API Key（用于BGE-M3，不显示、不保存）' -AsSecureString
    $embeddingPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($embeddingKey)
    try {
        $env:SILICONFLOW_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($embeddingPointer)
        $enteredEmbeddingKey = $true
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($embeddingPointer)
        $embeddingKey.Dispose()
    }
}
if (-not $env:MAGI_API_TOKEN) {
    $env:MAGI_API_TOKEN = [Guid]::NewGuid().ToString('N') + [Guid]::NewGuid().ToString('N')
}
# 沿用当前进程凭据时也显示，避免重启后用户无法连接网页或终端。
# 仅显示在本机启动窗口，不写入文件；这不是千问 API Key。
Write-Host "本次服务凭据（粘贴到本机聊天页、终端或 Pipe Valves）：$env:MAGI_API_TOKEN"
$env:MAGI_CONFIG = $Config
Write-Host "MAGI: http://127.0.0.1:$Port ；Ctrl+C 停止。单 worker，不使用 reload。"
try {
    & .\.venv-harness\Scripts\python.exe -X utf8 -m magi.web --port $Port
} finally {
    if ($enteredEmbeddingKey) { Remove-Item Env:SILICONFLOW_API_KEY -ErrorAction SilentlyContinue }
    if ($enteredKey) { [Environment]::SetEnvironmentVariable($modelProfile.api_key_env, $null, 'Process') }
}
