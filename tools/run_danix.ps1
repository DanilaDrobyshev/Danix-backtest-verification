# Runs every backtest of config/runs.json through the Danix command line and saves the raw answers
# to danix_runs/raw, then tools/collect.py turns them into danix_runs/*.json and data/candles/*.csv.
#
#   powershell -ExecutionPolicy Bypass -File tools\run_danix.ps1 [-Danix "C:\path\Danix.exe"] [-Only ticks_SOLUSDT] [-Exchange kucoin]
#
# -Exchange kucoin runs config/runs_kucoin.json into danix_runs/kucoin (Danix must have KuCoin selected in Settings > Exchanges).
# The runs go to the running Danix (Data\runtime\danix_cli.json); the script stops if it does not answer.
# Needs Danix 0.3+ (with the `tool` command) and internet: Danix downloads the candles and the Bybit trade archive
# itself. Copies the VERIFY strategies and the scenario CSV files into the Danix Data folder first.
param(
    [string]$Danix = "Danix.exe",
    [string]$DataDir = "$env:LOCALAPPDATA\Danix\Data",
    [string]$Only = "",
    [string]$Python = "python",
    [ValidateSet("bybit", "kucoin")][string]$Exchange = "bybit"
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$cfgName = if ($Exchange -eq "bybit") { "runs.json" } else { "runs_$Exchange.json" }
$cfg = Get-Content (Join-Path $root "config\$cfgName") -Raw | ConvertFrom-Json
$raw = if ($Exchange -eq "bybit") { Join-Path $root "danix_runs\raw" } else { Join-Path $root "danix_runs\$Exchange\raw" }

# Without a running Danix the command line would compute locally: the runs must come from the running Danix
try {
    $cli = Get-Content (Join-Path $DataDir "runtime\danix_cli.json") -Raw | ConvertFrom-Json
    Invoke-RestMethod "http://127.0.0.1:$($cli.port)/api/info" -Headers @{ "X-Danix-Token" = $cli.token } -TimeoutSec 5 | Out-Null
}
catch { throw "Danix is not running (no answer via $DataDir\runtime\danix_cli.json): start Danix.exe first" }
New-Item -ItemType Directory -Force $raw | Out-Null

Copy-Item (Join-Path $root "strategies\*.json") (Join-Path $DataDir "strategies") -Force
Get-ChildItem (Join-Path $root "scenarios\*.csv") | ForEach-Object {
    Copy-Item $_.FullName (Join-Path $DataDir ("charts\VERIFY " + $_.Name)) -Force
}

$argsFile = Join-Path $env:TEMP "danix-verify-args.json"
function Invoke-Tool($tool, $toolArgs, $outFile) {
    [IO.File]::WriteAllText($argsFile, ($toolArgs | ConvertTo-Json -Depth 30 -Compress))
    $out = & $Danix tool $tool "@$argsFile" --json
    if ($LASTEXITCODE -ne 0) { throw "$tool failed: $out" }
    [IO.File]::WriteAllText($outFile, ($out -join "`n"))
}

foreach ($e in $cfg.exports) {
    if ($Only -and $e.file -notlike "*$Only*") { continue }
    Write-Host "candles $($e.file)"
    Invoke-Tool "get_candles" $e.args (Join-Path $raw ("candles_" + $e.file + ".json"))
}
foreach ($r in $cfg.runs) {
    if ($Only -and $r.id -notlike "*$Only*") { continue }
    Write-Host "$($r.tool) $($r.id)"
    Invoke-Tool $r.tool $r.args (Join-Path $raw ($r.id + ".json"))
}

$exe = Get-Command $Danix -ErrorAction SilentlyContinue
$meta = @{
    danix_file_version = if ($exe) { (Get-Item $exe.Source).VersionInfo.FileVersion } else { "" }
    run_at_utc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd HH:mm")
}
[IO.File]::WriteAllText((Join-Path $raw "_meta.json"), ($meta | ConvertTo-Json))
& $Python -I (Join-Path $root "tools\collect.py") --exchange $Exchange
if ($LASTEXITCODE -ne 0) { throw "collect.py failed" }
