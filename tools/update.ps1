# One command to re-check the current Danix build: runs every backtest through Danix, downloads the Bybit trade
# archive and funding rates (cached), runs verify.py and prints only what did not pass.
#
#   powershell -ExecutionPolicy Bypass -File tools\update.ps1 [-Danix "C:\path\Danix.exe"] [-Exchange kucoin] [-Push]
#
# -Exchange kucoin: candle runs of config/runs_kucoin.json (select KuCoin in Danix Settings > Exchanges first);
# results in danix_runs/kucoin, data/kucoin and report/kucoin, previous ones in danix_runs_prev_kucoin.
# -Push commits and pushes the new results, only when verify.py reports 0 failed.
# The previous results are kept in danix_runs_prev for comparison (git ignored).
param(
    [string]$Danix = "Danix.exe",
    [ValidateSet("bybit", "kucoin")][string]$Exchange = "bybit",
    [switch]$Push
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    python -m venv .venv
    & $py -m pip install -q -r requirements.txt
}

$runs = if ($Exchange -eq "bybit") { "danix_runs" } else { "danix_runs\$Exchange" }
$prev = if ($Exchange -eq "bybit") { "danix_runs_prev" } else { "danix_runs_prev_$Exchange" }
$report = if ($Exchange -eq "bybit") { "report" } else { "report\$Exchange" }
if (Test-Path $prev) { Remove-Item -Recurse -Force $prev }
if (Test-Path $runs) { Copy-Item -Recurse $runs $prev }

& (Join-Path $PSScriptRoot "run_danix.ps1") -Danix $Danix -Python $py -Exchange $Exchange
if ($Exchange -eq "bybit") {
    & $py tools\fetch_ticks.py
    if ($LASTEXITCODE -ne 0) { throw "fetch_ticks.py failed" }
}
& $py tools\fetch_funding.py --exchange $Exchange
if ($LASTEXITCODE -ne 0) { throw "fetch_funding.py failed" }

New-Item -ItemType Directory -Force $report | Out-Null
cmd /c "`"$py`" verify.py --exchange $Exchange > $report\console.txt 2>&1"
$code = $LASTEXITCODE
Get-Content $report\console.txt | Where-Object { $_ -match '\[(FAIL|knwn|skip)' -or $_ -match ' passed, ' }
Write-Host ""
if ($code -ne 0) {
    Write-Host "verify.py FAILED (exit $code). Full output: $report\console.txt; old results: $prev" -ForegroundColor Red
    exit $code
}
Write-Host "All checks passed." -ForegroundColor Green
if ($Push) {
    git add -A
    git commit -m ("Re-run on current Danix " + (Get-Date).ToString("yyyy-MM-dd"))
    git push
}
