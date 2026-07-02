<#
.SYNOPSIS
    Neuron - Dependency check and repair
.DESCRIPTION
    Checks every required component. With -Repair, attempts to fix what's missing.
    Exit code: 0 = all good, 1 = issues (repairable with -Repair).
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\check.ps1
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\check.ps1 -Repair
#>

# NOTE: param() MUST be the first executable statement (after comment-based help).
# Putting the self-reinvoke if-block above it is a PowerShell parse error that
# breaks the whole script - that is what made check.bat fail on install.
param([switch]$Repair)

# Self-reinvoke with ExecutionPolicy Bypass, using the CURRENT PowerShell host so
# it works under both Windows PowerShell (powershell.exe) AND PowerShell 7 (pwsh).
# Machines with only pwsh don't have `powershell` on PATH, which used to crash here.
if ($MyInvocation.MyCommand.Path -and -not ($env:__NEURON_BYPASS)) {
    $env:__NEURON_BYPASS = '1'
    $psExe = (Get-Process -Id $PID).Path
    if (-not $psExe) { $psExe = (Get-Command pwsh -ErrorAction SilentlyContinue).Source }
    if (-not $psExe) { $psExe = (Get-Command powershell -ErrorAction SilentlyContinue).Source }
    if ($psExe) {
        & $psExe -ExecutionPolicy Bypass -File $MyInvocation.MyCommand.Path @PSBoundParameters
        exit $LASTEXITCODE
    }
    # No separate host found - continue in this process (already allowed to run).
}

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
            $script:issues += $Label
            if ($Repair -and $RepairAction) {
                Write-Host "       Repair..." -ForegroundColor Yellow
                try { & $RepairAction; Write-Host "       OK" -ForegroundColor Green } catch { Write-Host "       Failed: $_" -ForegroundColor Red }
            }
        }
    } catch { Write-Host "  [!!] $Label - $_" -ForegroundColor Red; $script:issues += $Label }
}

Write-Host "=== Neuron - Dependency Check ===" -ForegroundColor Cyan
Write-Host ""

# ---- 1. Python ----
Write-Host "1. Python runtime" -ForegroundColor Yellow
Check -Label "Python 3.10+" -Condition { python -c "import sys; exit(0 if sys.version_info >= (3,10) else 1)" 2>$null; $LASTEXITCODE -eq 0 }
Check -Label ".venv" -Condition { Test-Path "$SrcDir\.venv\Scripts\python.exe" }
$py = if (Test-Path "$SrcDir\.venv\Scripts\python.exe") { "$SrcDir\.venv\Scripts\python.exe" } else { "python" }

# ---- 2. Python dependencies ----
# Checked FIRST on purpose: if pyturso imports, the Rust/MSVC toolchain is
# irrelevant (it's only a *compile fallback* for when no prebuilt wheel exists).
Write-Host "`n2. Python dependencies" -ForegroundColor Yellow

# pip lives in the venv; use `-m pip` so it works even if the pip.exe launcher
# is missing (e.g. a uv-created venv). Falls back to the base python's pip.
$pipExe = "$SrcDir\.venv\Scripts\pip.exe"
function Invoke-Pip { param([string[]]$PipArgs)
    if (Test-Path $py) { & $py -m pip @PipArgs 2>$null }
    elseif (Test-Path $pipExe) { & $pipExe @PipArgs 2>$null }
}

Check -Label "mcp SDK" -Condition { & $py -c "import mcp" 2>$null; $LASTEXITCODE -eq 0 } -RepairAction {
    Invoke-Pip @("install", "mcp>=1.28.0")
}
Check -Label "fastembed" -Condition { & $py -c "from fastembed import TextEmbedding" 2>$null; $LASTEXITCODE -eq 0 } -RepairAction {
    Invoke-Pip @("install", "fastembed>=0.5.0")
}
Check -Label "pyturso (Turso DB)" -Condition { & $py -c "import turso" 2>$null; $LASTEXITCODE -eq 0 } -RepairAction {
    # --find-links vendor: prefer the prebuilt win_amd64 wheel (no Rust/MSVC compile,
    # which otherwise looks frozen at "Preparing metadata"). Falls back to PyPI only
    # if this Python's ABI has no vendored wheel.
    $vendor = Join-Path $SrcDir "vendor"
    $pipArgs = @("install", "pyturso==0.6.1")
    if (Test-Path $vendor) { $pipArgs += @("--find-links", $vendor) }
    Invoke-Pip $pipArgs
}
# Did pyturso end up importable? Drives whether the toolchain matters below.
& $py -c "import turso" 2>$null; $pytursoOk = ($LASTEXITCODE -eq 0)

# ---- 3. Rust toolchain (ONLY needed to compile pyturso when no prebuilt wheel) ----
Write-Host "`n3. Rust toolchain (only needed if pyturso must be compiled)" -ForegroundColor Yellow
if ($pytursoOk) {
    Write-Host "  [OK] Not needed - pyturso is already installed (prebuilt wheel)." -ForegroundColor Green
} else {
    Check -Label "rustc" -Condition { [bool](Get-Command rustc -ErrorAction SilentlyContinue) }
    Check -Label "rustup" -Condition { [bool](Get-Command rustup -ErrorAction SilentlyContinue) }
}

# ---- 4. C++ toolchain (ONLY needed to compile pyturso when no prebuilt wheel) ----
Write-Host "`n4. C++ toolchain (only needed if pyturso must be compiled)" -ForegroundColor Yellow
if ($pytursoOk) {
    Write-Host "  [OK] Not needed - pyturso is already installed (prebuilt wheel)." -ForegroundColor Green
} else {
    $vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
    $msvcOk = $false
    if (Test-Path $vswhere) {
        $vi = & $vswhere -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -format json 2>$null | ConvertFrom-Json
        if ($vi) { $msvcOk = $true }
    }
    # Guard rustup: a bare call when it's not installed throws CommandNotFoundException
    # (which -ErrorAction can't catch) and aborts the whole check.
    $hasRustup = [bool](Get-Command rustup -ErrorAction SilentlyContinue)
    $tc = if ($hasRustup) { rustup default 2>$null } else { "" }
    Check -Label "MSVC (via vswhere) or GNU" -Condition { $msvcOk -or ($tc -match "gnu") } -RepairAction {
        if ($msvcOk) { return }
        if ($hasRustup) {
            Write-Host "     Activating GNU toolchain..." -ForegroundColor Yellow
            rustup toolchain install stable-gnu 2>$null
            rustup default stable-gnu 2>$null
        } else {
            Write-Host "     Neither MSVC nor rustup found. Easiest fix: use a prebuilt pyturso" -ForegroundColor Yellow
            Write-Host "     wheel (Python 3.10-3.14 + the bundled vendor\ wheels), so no compiler" -ForegroundColor Yellow
            Write-Host "     is needed. Otherwise install Rust from https://rustup.rs and re-run." -ForegroundColor Yellow
        }
    }
}

# ---- 5. Config ----
Write-Host "`n5. Configuration" -ForegroundColor Yellow
$oc = "$env:USERPROFILE\.config\opencode\opencode.json"
Check -Label "opencode.json" -Condition { Test-Path $oc }

# ---- 6. Result ----
Write-Host ""
if ($issues.Count -eq 0) { Write-Host "All OK. Neuron ready." -ForegroundColor Green; exit 0 }
Write-Host "Issues ($($issues.Count)): $($issues -join ', ')" -ForegroundColor Yellow
if (-not $Repair) { Write-Host "Use: check.bat -Repair to fix" -ForegroundColor Cyan }
exit 1
