# Windows Task Scheduler / manual cron equivalent.
# Usage: .\scripts\run-channel.ps1 justcavefire

param(
    [string]$Channel = $env:UPLOADER_DEFAULT_CHANNEL
)

$ErrorActionPreference = "Stop"
if (-not $Channel) { $Channel = "justcavefire" }

$Retries = if ($env:UPLOADER_UPLOAD_RETRIES) { $env:UPLOADER_UPLOAD_RETRIES } else { "5" }
$LogDir = if ($env:UPLOADER_LOG_DIR) { $env:UPLOADER_LOG_DIR } else { "logs" }

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir ("uploader-{0}-{1:yyyyMMdd}.log" -f $Channel, (Get-Date))

$stamp = Get-Date -Format "o"
"=== $stamp uploader run --channel $Channel ===" | Add-Content $LogFile
try {
    uploader run --channel $Channel --upload-retries $Retries 2>&1 | Add-Content $LogFile
    "=== exit 0 ===" | Add-Content $LogFile
} catch {
    "=== FAILED: $_ ===" | Add-Content $LogFile
    exit 1
}
