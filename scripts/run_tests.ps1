<#
.SYNOPSIS
    Neuron - one-command test runner.
.DESCRIPTION
    Creates the local .venv if missing, installs the package with the [dev]
    extra (pytest + pytest-asyncio + runtime deps), then runs the test suite.
    Idempotent: re-run any time. The first run downloads the fastembed model
    (~80MB) the full suite needs.
.PARAMETER Core
    Run only tests/test_core.py (mocks the heavy deps; fastest, no model download).
.PARAMETER NoInstall
    Skip the venv/dependency install step and just run the tests.
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\run_tests.ps1
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\run_tests.ps1 -Core
#>
[CmdletBinding()]
param(
    [switch]$Core,
    [switch]$NoInstall
)

# Self-reinvoke with ExecutionPolicy Bypass, using the CURRENT PowerShell host so it
# works under both Windows PowerShell (powershell.exe) AND PowerShell 7 (pwsh); boxes
# with only pwsh have no `powershell` on PATH.
if ($MyInvocation.MyCommand.Path -and -not ($env:__NEURON_BYPASS)) {
    $env:__NEURON_BYPASS = '1'
    $psExe = (Get-Process -Id $PID).Path
    if (-not $psExe) { $psExe = (Get-Command pwsh -ErrorAction SilentlyContinue).Source }
    if (-not $psExe) { $psExe = (Get-Command powershell -ErrorAction SilentlyContinue).Source }
    if ($psExe) {
        & $psExe -ExecutionPolicy Bypass -File $MyInvocation.MyCommand.Path @PSBoundParameters
        exit $LASTEXITCODE
    }
}

$ErrorActionPreference = "Stop"

# Repo root = parent of this script's folder
$Repo = Split-Path -Parent $PSScriptRoot
Set-Location $Repo

$venv = Join-Path $Repo ".venv"
$py   = Join-Path $venv "Scripts\python.exe"

# 1. venv ---------------------------------------------------------------------
if (-not (Test-Path $py)) {
    Write-Host "Creating virtual env at $venv ..." -ForegroundColor Yellow
    python -m venv $venv
    if (-not (Test-Path $py)) { Write-Host "ERROR: venv creation failed." -ForegroundColor Red; exit 1 }
}

# 2. dependencies -------------------------------------------------------------
if (-not $NoInstall) {
    Write-Host "Installing Neuron + [dev] extras ..." -ForegroundColor Yellow
    & $py -m pip install --upgrade pip | Out-Null
    # --find-links vendor: install the PRE-BUILT pyturso win_amd64 wheel instead of
    # compiling it from the Rust sdist. Without this, `pip install -e .[dev]` stalls
    # for minutes at "Preparing metadata (pyproject.toml)" building pyturso, which
    # looks like a freeze. Falls back to PyPI only if this Python's ABI has no wheel.
    $vendor = Join-Path $Repo "vendor"
    $pipArgs = @("-m", "pip", "install", "-e", ".[dev]")
    if (Test-Path $vendor) { $pipArgs += @("--find-links", $vendor) }
    & $py @pipArgs
    if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: pip install failed." -ForegroundColor Red; exit 1 }
}

# 3. tests --------------------------------------------------------------------
$target = if ($Core) { "tests/test_core.py" } else { "tests/" }
Write-Host "`nRunning pytest on $target ..." -ForegroundColor Cyan
& $py -m pytest $target -v
exit $LASTEXITCODE
