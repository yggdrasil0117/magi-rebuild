param(
    [string]$Url = "http://127.0.0.1:8765",
    [string]$Chat = "study",
    [switch]$NoColor,
    [switch]$Help,
    [switch]$Preview
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
# 仅启动客户端；复用已运行的后端，不额外启动模型或另建业务数据库。
$terminalArgs = @("-X", "utf8", "-m", "magi.terminal", "--url", $Url, "--chat", $Chat)
if ($NoColor) { $terminalArgs += "--no-color" }
if ($Help) { $terminalArgs += "--help" }
if ($Preview) { $terminalArgs += "--preview" }
& .\.venv-harness\Scripts\python.exe @terminalArgs
exit $LASTEXITCODE
