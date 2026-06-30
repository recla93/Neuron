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
    & $py -m pip install -e ".[dev]"
    if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: pip install failed." -ForegroundColor Red; exit 1 }
}

# 3. tests --------------------------------------------------------------------
$target = if ($Core) { "tests/test_core.py" } else { "tests/" }
Write-Host "`nRunning pytest on $target ..." -ForegroundColor Cyan
& $py -m pytest $target -v
exit $LASTEXITCODE
