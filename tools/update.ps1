# One command to re-check the current Danix build: runs every backtest through Danix, downloads the Bybit trade
# archive and funding rates (cached), runs verify.py and prints only what did not pass.
#
#   powershell -ExecutionPolicy Bypass -File tools\update.ps1 [-Danix "C:\path\Danix.exe"] [-Push]
#
# -Push commits and pushes the new results, only when verify.py reports 0 failed.
# The previous results are kept in danix_runs_prev for comparison (git ignored).
param(
    [string]$Danix = "Danix.exe",
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

if (Test-Path danix_runs_prev) { Remove-Item -Recurse -Force danix_runs_prev }
Copy-Item -Recurse danix_runs danix_runs_prev

& (Join-Path $PSScriptRoot "run_danix.ps1") -Danix $Danix -Python $py
& $py tools\fetch_ticks.py
if ($LASTEXITCODE -ne 0) { throw "fetch_ticks.py failed" }
& $py tools\fetch_funding.py
if ($LASTEXITCODE -ne 0) { throw "fetch_funding.py failed" }

New-Item -ItemType Directory -Force report | Out-Null
cmd /c "`"$py`" verify.py > report\console.txt 2>&1"
$code = $LASTEXITCODE
Get-Content report\console.txt | Where-Object { $_ -match '\[(FAIL|knwn|skip)' -or $_ -match ' passed, ' }
Write-Host ""
if ($code -ne 0) {
    Write-Host "verify.py FAILED (exit $code). Full output: report\console.txt; old results: danix_runs_prev" -ForegroundColor Red
    exit $code
}
Write-Host "All checks passed." -ForegroundColor Green
if ($Push) {
    git add -A
    git commit -m ("Re-run on current Danix " + (Get-Date).ToString("yyyy-MM-dd"))
    git push
}
