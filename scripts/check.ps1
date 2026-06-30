<#
.SYNOPSIS
    Neuron — Dependency check and repair
.DESCRIPTION
    Checks every required component. With -Repair, attempts to fix what's missing.
    Exit code: 0 = all good, 1 = issues (repairable with -Repair).
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\check.ps1
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\check.ps1 -Repair
#>

# Self-reinvoke with ExecutionPolicy Bypass
if ($MyInvocation.MyCommand.Path -and -not ($env:__NEURON_BYPASS)) { $env:__NEURON_BYPASS='1'; powershell -ExecutionPolicy Bypass -File $MyInvocation.MyCommand.Path @PSBoundParameters; exit $LASTEXITCODE }

param([switch]$Repair)

$ErrorActionPreference = "Continue"
$issues = @()
$SrcDir = Split-Path -Parent $PSScriptRoot

function Check {
    param([string]$Label, [scriptblock]$Condition, [scriptblock]$RepairAction = $null)
    try {
        $ok = & $Condition
        if ($ok) { Write-Host "  [OK] $Label" -ForegroundColor Green }
        else {
            Write-Host "  [!!] $Label" -ForegroundColor Red
            $issues += $Label
            if ($Repair -and $RepairAction) {
                Write-Host "       Repair..." -ForegroundColor Yellow
                try { & $RepairAction; Write-Host "       OK" -ForegroundColor Green } catch { Write-Host "       Failed: $_" -ForegroundColor Red }
            }
        }
    } catch { Write-Host "  [!!] $Label - $_" -ForegroundColor Red; $issues += $Label }
}

Write-Host "=== Neuron — Dependency Check ===" -ForegroundColor Cyan
Write-Host ""

# ---- 1. Python ----
Write-Host "1. Python runtime" -ForegroundColor Yellow
Check -Label "Python 3.10+" -Condition { python -c "import sys; exit(0 if sys.version_info >= (3,10) else 1)" 2>$null; $LASTEXITCODE -eq 0 }
Check -Label ".venv" -Condition { Test-Path "$SrcDir\.venv\Scripts\python.exe" }
$py = if (Test-Path "$SrcDir\.venv\Scripts\python.exe") { "$SrcDir\.venv\Scripts\python.exe" } else { "python" }

# ---- 2. Rust ----
Write-Host "`n2. Rust toolchain" -ForegroundColor Yellow
Check -Label "rustc" -Condition { Get-Command rustc -ErrorAction SilentlyContinue }
Check -Label "rustup" -Condition { Get-Command rustup -ErrorAction SilentlyContinue }

# ---- 3. MSVC / GNU ----
Write-Host "`n3. C++ toolchain" -ForegroundColor Yellow
$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$msvcOk = $false
if (Test-Path $vswhere) {
    $vi = & $vswhere -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -format json 2>$null | ConvertFrom-Json
    if ($vi) { $msvcOk = $true }
}
$tc = rustup default 2>$null
Check -Label "MSVC (via vswhere) or GNU" -Condition { $msvcOk -or ($tc -match "gnu") } -RepairAction {
    if (-not $msvcOk) {
        Write-Host "     Activating GNU toolchain..." -ForegroundColor Yellow
        rustup toolchain install stable-gnu 2>$null
        rustup default stable-gnu 2>$null
    }
}

# ---- 4. Python deps ----
Write-Host "`n4. Python dependencies" -ForegroundColor Yellow

Check -Label "mcp SDK" -Condition { & $py -c "import mcp" 2>$null; $LASTEXITCODE -eq 0 } -RepairAction {
    & "$SrcDir\.venv\Scripts\pip.exe" install "mcp>=1.28.0" 2>$null
}
Check -Label "fastembed" -Condition { & $py -c "from fastembed import TextEmbedding" 2>$null; $LASTEXITCODE -eq 0 } -RepairAction {
    & "$SrcDir\.venv\Scripts\pip.exe" install "fastembed>=0.5.0" 2>$null
}
Check -Label "pyturso (Turso DB)" -Condition { & $py -c "import turso" 2>$null; $LASTEXITCODE -eq 0 } -RepairAction {
    & "$SrcDir\.venv\Scripts\pip.exe" install "pyturso>=0.6.1" 2>$null
}

# ---- 5. Config ----
Write-Host "`n5. Configuration" -ForegroundColor Yellow
$oc = "$env:USERPROFILE\.config\opencode\opencode.json"
Check -Label "opencode.json" -Condition { Test-Path $oc }

# ---- 6. Result ----
Write-Host ""
if ($issues.Count -eq 0) { Write-Host "All OK. Neuron ready." -ForegroundColor Green; exit 0 }
Write-Host "Issues ($($issues.Count)): $($issues -join ', ')" -ForegroundColor Yellow
if (-not $Repair) { Write-Host "Use: powershell -ExecutionPolicy Bypass -File scripts\check.ps1 -Repair to fix" -ForegroundColor Cyan }
exit 1
