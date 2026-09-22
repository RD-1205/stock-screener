# Daily EODHD price-ingest job. Spends the free-tier 20 requests/day quota
# on ingested companies that don't have price data yet, then recomputes the
# market-mood gauge now that new prices have landed.
#
# Run manually with:  powershell -File scripts\daily_price_ingest.ps1
#
# IMPORTANT -- this file lives under OneDrive, and that's fine for manual
# runs, but Task Scheduler cannot reliably launch a script from a OneDrive
# Files-On-Demand folder (confirmed 2026-09-15: schtasks /Run reports
# success and exit code 0, but the script body never actually executes --
# no log line, no data pulled -- while the identical command run from an
# interactive shell works every time). The scheduled task therefore points
# at a plain local copy outside OneDrive:
#   C:\Users\rudra\AppData\Local\us-screener\daily_price_ingest.ps1
# That copy hardcodes $root instead of deriving it from $PSScriptRoot (it
# doesn't live next to the repo). Edit the logic here, then copy it over --
# see the "redeploy" one-liner in that file's own header comment.

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = "C:\Users\rudra\AppData\Local\Python\pythoncore-3.14-64\python.exe"
$logDir = Join-Path $root "logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$log = Join-Path $logDir "daily_price_ingest.log"

function Write-Log($text) {
    $text | Out-File -Append -FilePath $log -Encoding utf8
}

Write-Log "===== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ====="

$pricesOutput = & $python -m screener.cli prices --provider eodhd --unpriced --limit 20 2>&1
Write-Log ($pricesOutput | Out-String).TrimEnd()
Write-Log "prices exit code: $LASTEXITCODE"

$sentimentOutput = & $python -m screener.cli sentiment 2>&1
Write-Log ($sentimentOutput | Out-String).TrimEnd()
Write-Log "sentiment exit code: $LASTEXITCODE"

Write-Log ""
