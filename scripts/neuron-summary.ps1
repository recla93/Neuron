<#
.SYNOPSIS
  Neuron - semantic graph summary (reads the store DB directly, no LLM, zero tokens).
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\neuron-summary.ps1
#>

# Self-reinvoke with ExecutionPolicy Bypass using the CURRENT host, so it works on
# PowerShell-7-only machines where powershell.exe isn't on PATH.
if ($MyInvocation.MyCommand.Path -and -not $env:__NEURON_BYPASS) {
    $env:__NEURON_BYPASS = '1'
    $psExe = (Get-Process -Id $PID).Path
    & $psExe -ExecutionPolicy Bypass -File $MyInvocation.MyCommand.Path @PSBoundParameters
    exit $LASTEXITCODE
}

. (Join-Path $PSScriptRoot '_neuron_paths.ps1')
$P = Get-NeuronPaths

# The store holds one DB per context (graph_<ctx>.db) in %LOCALAPPDATA%\<slug>\graphs,
# NOT a single graph.db in the install dir. Prefer the default context.
$DB = $null
if (Test-Path -LiteralPath $P.StoreDir) {
    $def = Join-Path $P.StoreDir 'graph_default.db'
    if (Test-Path -LiteralPath $def) { $DB = $def }
    else { $DB = (Get-ChildItem -LiteralPath $P.StoreDir -Filter 'graph_*.db' -ErrorAction SilentlyContinue | Select-Object -First 1).FullName }
}
if (-not $DB) {
    Write-Host "  No database found in $($P.StoreDir)." -ForegroundColor Yellow
    Write-Host "  Create a graph with neuron_store_turn first (or set NS_GRAPHS_DIR)." -ForegroundColor Yellow
    exit 0
}

$Python = if (Test-Path -LiteralPath (Join-Path $P.InstallDir '.venv\Scripts\python.exe')) {
    Join-Path $P.InstallDir '.venv\Scripts\python.exe'
} else { 'python' }

$queryScript = Join-Path $PSScriptRoot 'neuron_summary_query.py'
Write-Host "`n  ===== Neuron - Graph Summary ($([System.IO.Path]::GetFileName($DB))) =====" -ForegroundColor Cyan
Write-Host ""
$output = & $Python $queryScript $DB 2>&1
if ($LASTEXITCODE) {
    Write-Host "  ERROR: unable to read database" -ForegroundColor Red
    $output | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
} else {
    $output | ForEach-Object { Write-Host $_ }
}
Write-Host "  =============================================`n" -ForegroundColor Cyan
