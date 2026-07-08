<#
.SYNOPSIS
    Neuron - repeatable deploy/sync from the source repo to the active install.

.DESCRIPTION
    Updates the active MCP install (default: %LOCALAPPDATA%\Programs\neuron) to
    the CURRENT source by REINSTALLING the package into the install's own venv -
    i.e. exactly what `python -m neuron` imports (site-packages), NOT a loose
    copy of src\ that nothing loads.

    Why a reinstall and not a file copy: the install created by install.ps1 is a
    normal wheel install in .venv\Lib\site-packages\neuron. Copying source into
    <dest>\src would never be on the import path, so it would SILENTLY leave the
    running MCP server on old code (and a naive verify that imports the copied
    src would still say "OK"). Reinstalling into the venv updates what actually
    runs. `pip install --force-reinstall` also sidesteps the "same __version__ ->
    pip skips" trap, so identical-version code still lands.

    What it does:
      * Reinstalls neuron from the source tree into <dest>\.venv via
        `pip install --force-reinstall --no-deps "<repo>"` (pip's build backend
        builds the wheel; --no-deps leaves runtime deps mcp/fastembed/pyturso
        untouched, so it's fast and can't disturb a working environment).
      * Never touches the install's .venv interpreter, graphs\ or logs\.
      * -DryRun previews the plan (source vs installed version, target venv) and
        changes nothing - safe with no venv present and no network (used by CI).
      * After installing, verifies by importing neuron + neuron.server FROM THE
        VENV (site-packages), and confirms the installed __version__ == source.
      * -RunTests runs the repo test suite with the install's venv (skipped with
        a note if pytest isn't installed there - production installs omit it).

.PARAMETER Dest
    Target install directory. Default: %LOCALAPPDATA%\Programs\neuron

.PARAMETER DryRun
    Show the plan and exit without modifying anything (no venv/network needed).

.PARAMETER RunTests
    After installing, run "python -m pytest tests/ -q" using the install's venv.

.PARAMETER Force
    Reserved for compatibility; no confirmation prompt is issued.

.PARAMETER Prune
    Deprecated no-op: a wheel reinstall already replaces stale package files.
    Still accepted so existing callers/scripts don't break.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\deploy.ps1 -DryRun
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\deploy.ps1 -RunTests
#>
[CmdletBinding()]
param(
    [string]$Dest,                # default computed from -Slug below (with LOCALAPPDATA fallback)
    [string]$Slug = 'neuron5',    # install identity (v5 "Synapse"); use 'neuron' for the v4 line
    [switch]$DryRun,
    [switch]$RunTests,
    [switch]$Force,
    [switch]$Prune,
    [switch]$Yes                  # non-interactive (don't prompt to stop a running server)
)

# Self-reinvoke with ExecutionPolicy Bypass. Rebuild the argument list by hand:
# splatting @PSBoundParameters into `-File` renders a [switch] as "-Name True",
# which -File mode can't bind back to a SwitchParameter. Forward switches as bare
# flags and value params as "-Name Value".
if ($MyInvocation.MyCommand.Path -and -not ($env:__NEURON_BYPASS)) {
    $env:__NEURON_BYPASS = '1'
    $fwd = @()
    foreach ($kv in $PSBoundParameters.GetEnumerator()) {
        if ($kv.Value -is [System.Management.Automation.SwitchParameter]) {
            if ($kv.Value.IsPresent) { $fwd += "-$($kv.Key)" }
        } else {
            $fwd += "-$($kv.Key)"; $fwd += "$($kv.Value)"
        }
    }
    $psExe = (Get-Process -Id $PID).Path                       # current host (works on pwsh-7-only boxes)
    if (-not $psExe) { $psExe = (Get-Command pwsh -ErrorAction SilentlyContinue).Source }
    if (-not $psExe) { $psExe = (Get-Command powershell -ErrorAction SilentlyContinue).Source }
    if ($psExe) { & $psExe -ExecutionPolicy Bypass -File $MyInvocation.MyCommand.Path @fwd; exit $LASTEXITCODE }
    # else: fall through and run in this process (policy already allowed it)
}

$ErrorActionPreference = "Stop"

# Repo root = parent of this script's folder (scripts\deploy.ps1 -> repo root)
$Src = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot '_neuron_paths.ps1')

# Resolve the install dir: -Dest wins; else %LOCALAPPDATA%\Programs\<slug> (with fallback).
if (-not $Dest) { $Dest = Join-Path (Get-LocalAppData) "Programs\$Slug" }
# Don't sync over a running server (locked venv files -> partial write / corruption).
if (-not $DryRun) { Stop-NeuronServices -InstallDir $Dest -Yes:$Yes }

# --- Sanity: are we really pointed at a Neuron source tree? -------------------
if (-not (Test-Path (Join-Path $Src "pyproject.toml")) -or
    -not (Test-Path (Join-Path $Src "src\neuron\server.py"))) {
    Write-Host "ERROR: '$Src' does not look like the Neuron source repo." -ForegroundColor Red
    exit 1
}

if ($Prune) { Write-Host "Note: -Prune is a no-op now (a wheel reinstall already replaces stale files)." -ForegroundColor DarkYellow }

# --- Version helper (parse __version__ from an __init__.py) --------------------
function Get-Version([string]$initPath) {
    if (-not (Test-Path $initPath)) { return $null }
    foreach ($ln in (Get-Content $initPath)) {
        if ($ln -like '*__version__*=*') {
            return (($ln -split '=')[1]).Trim().Trim('"').Trim("'").Trim()
        }
    }
    return $null
}

$venvPy = Join-Path $Dest ".venv\Scripts\python.exe"
$sver   = Get-Version (Join-Path $Src "src\neuron\__init__.py")

Write-Host "Neuron deploy/sync (wheel reinstall)" -ForegroundColor Cyan
Write-Host "  Source : $Src  (version $sver)"
Write-Host "  Target : $Dest"
Write-Host "  Venv   : $venvPy  (exists: $([bool](Test-Path $venvPy)))"
if ($DryRun) { Write-Host "  Mode   : DRY RUN (no changes)" -ForegroundColor Yellow }
Write-Host ""

# --- DRY RUN: report the plan and exit (no venv/network needed) ----------------
if ($DryRun) {
    Write-Host "Plan: reinstall neuron $sver from source into the target venv via" -ForegroundColor Cyan
    Write-Host "        pip install --force-reinstall --no-deps `"$Src`"" -ForegroundColor Cyan
    if (-not (Test-Path $venvPy)) {
        Write-Host "Note: no venv at the target yet - run install.ps1 first for a real deploy." -ForegroundColor DarkYellow
    }
    Write-Host ""
    Write-Host "Dry run complete - no changes made." -ForegroundColor Yellow
    exit 0
}

# --- Real deploy: require the install venv ------------------------------------
if (-not (Test-Path $venvPy)) {
    Write-Host "ERROR: no venv at $venvPy - run install.ps1 first, then re-run deploy." -ForegroundColor Red
    exit 1
}

Write-Host "Reinstalling neuron into the install venv (building from source)..." -ForegroundColor Yellow
& $venvPy -m pip install --force-reinstall --no-deps "$Src"
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: pip reinstall failed - see the pip output above." -ForegroundColor Red
    exit 1
}
Write-Host "  [OK] reinstalled into site-packages." -ForegroundColor Green

# --- Version parity (installed value comes FROM the venv) ---------------------
$dver = (& $venvPy -c "import neuron; print(neuron.__version__)" 2>$null | Out-String).Trim()
Write-Host ""
Write-Host "Version: source=$sver  installed=$dver" -ForegroundColor Cyan
if ($sver -and $dver -and $sver -ne $dver) {
    Write-Host "WARNING: version mismatch after reinstall." -ForegroundColor Red
}

# --- Verify the install (import FROM THE VENV, not a copied src) ---------------
$verifyFailed = $false
Write-Host ""
Write-Host "Verifying install..." -ForegroundColor Yellow
& $venvPy -c "import neuron.server"
if ($LASTEXITCODE -ne 0) { Write-Host "  [FAIL] import neuron.server" -ForegroundColor Red; $verifyFailed = $true }
else { Write-Host "  [OK] import neuron.server" -ForegroundColor Green }

if ($RunTests) {
    & $venvPy -c "import pytest" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [SKIP] pytest isn't installed in the install venv (production install)." -ForegroundColor DarkYellow
        Write-Host "         Run the suite from the repo dev venv instead: python -m pytest -q" -ForegroundColor DarkGray
    } else {
        Push-Location $Src
        try {
            & $venvPy -m pytest tests/ -q
            if ($LASTEXITCODE -ne 0) { Write-Host "  [FAIL] pytest" -ForegroundColor Red; $verifyFailed = $true }
            else { Write-Host "  [OK] pytest" -ForegroundColor Green }
        } finally { Pop-Location }
    }
}

if ($verifyFailed) {
    Write-Host ""
    Write-Host "Deploy completed WITH verification failures." -ForegroundColor Red
    exit 1
}
Write-Host ""
Write-Host "Deploy complete. Restart your MCP client (Claude Desktop, ...) to load the new code." -ForegroundColor Green
exit 0
