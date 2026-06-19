# Windows Task Scheduler: process pending uploads for every configured channel.
# Usage: .\scripts\run-all-channels.ps1

$ErrorActionPreference = "Stop"

$Retries = if ($env:UPLOADER_UPLOAD_RETRIES) { $env:UPLOADER_UPLOAD_RETRIES } else { "5" }
$LogDir = if ($env:UPLOADER_LOG_DIR) { $env:UPLOADER_LOG_DIR } else { "logs" }

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir ("uploader-all-{0:yyyyMMdd}.log" -f (Get-Date))

$stamp = Get-Date -Format "o"
"=== $stamp uploader run-all ===" | Add-Content $LogFile
try {
    uploader run-all --upload-retries $Retries 2>&1 | Add-Content $LogFile
    "=== exit 0 ===" | Add-Content $LogFile
} catch {
    "=== FAILED: $_ ===" | Add-Content $LogFile
    exit 1
}
