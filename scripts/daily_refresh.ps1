# Nightly data refresh: runs `screener.cli refresh` (prices for every ticker
# via yfinance -> snapshot -> mood gauge -> SEC filings check -> normalize ->
# snapshot), the same pipeline the app runs at startup. Replaces
# daily_price_ingest.ps1, which only spent EODHD's 20-requests/day quota and
# so could never keep 800+ tickers current.
#
# Run manually with:  powershell -File scripts\daily_refresh.ps1
#
# The task runs a copy at  %USERPROFILE%\.us-screener\daily_refresh.ps1
# NOT under %LOCALAPPDATA%: the old job's copy was written there from inside
# the Claude desktop app, which is an MSIX package, so Windows silently
# redirected the write into the app's private LocalCache folder. Task
# Scheduler looked at the real path, found nothing, and failed every night
# after 2026-09-22 with 0xFFFD0000 (powershell -File: script not found).
# Redeploy after editing this file with:
#   Copy-Item scripts\daily_refresh.ps1 "$env:USERPROFILE\.us-screener\" -Force
# No edits are needed after copying: $root falls back to the repo path when
# the script is not sitting inside the repo.
#
# The log is written next to that copy, at
#   %USERPROFILE%\.us-screener\logs\daily_refresh.log
# because the old job's only log lived inside the repo, so when the task
# could not reach the repo there was no record that it had run at all.

$ErrorActionPreference = "Continue"

$here = Split-Path -Parent $PSScriptRoot
if (Test-Path (Join-Path $here "screener\cli.py")) {
    $root = $here
} else {
    $root = Join-Path $env:USERPROFILE "OneDrive\Desktop\projects\us-screener"
}

$logDir = Join-Path $env:USERPROFILE ".us-screener\logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
$log = Join-Path $logDir "daily_refresh.log"

function Write-Log($text) {
    $text | Out-File -Append -FilePath $log -Encoding utf8
}

Write-Log "===== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ====="

# Resolve python from PATH first so a Python upgrade doesn't silently break
# the job; the hardcoded path is only a fallback.
$python = (Get-Command python.exe -ErrorAction SilentlyContinue |
           Where-Object { $_.Source -notmatch 'WindowsApps' } |
           Select-Object -First 1).Source
if (-not $python) {
    $python = Join-Path $env:LOCALAPPDATA "Python\pythoncore-3.14-64\python.exe"
}

try {
    Set-Location $root -ErrorAction Stop
} catch {
    Write-Log "cannot reach repo at ${root}: $_"
    exit 1
}

# yfinance prints a "possibly delisted" block per dead ticker on stderr;
# keep only the pipeline's own summary lines so the log stays readable.
$output = & $python -m screener.cli refresh 2>&1 | ForEach-Object { "$_" }
$code = $LASTEXITCODE
$summary = $output | Where-Object { $_ -match '^\s*(prices|snapshot|sentiment|filings|normalize)\b' -or $_ -match 'Traceback|Error' }
Write-Log (($summary | Out-String).TrimEnd())
Write-Log "refresh exit code: $code"
Write-Log ""
exit $code
