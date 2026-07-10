<#
.SYNOPSIS
    Neuron - Dependency check
.DESCRIPTION
    Checks every required component. Read-only: reports issues but never modifies anything.
    Replaces a prior version with a -Repair flag that was unused in practice.
    Exit code: 0 = all good, 1 = issues found.
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\check.ps1
#>

# Self-reinvoke with ExecutionPolicy Bypass, using the CURRENT PowerShell host so
# it works under both Windows PowerShell (powershell.exe) AND PowerShell 7 (pwsh).
# Machines with only pwsh don't have `powershell` on PATH, which used to crash here.
if ($MyInvocation.MyCommand.Path -and -not ($env:__NEURON_BYPASS)) {
    $env:__NEURON_BYPASS = '1'
    $psExe = (Get-Process -Id $PID).Path
    if (-not $psExe) { $psExe = (Get-Command pwsh -ErrorAction SilentlyContinue).Source }
    if (-not $psExe) { $psExe = (Get-Command powershell -ErrorAction SilentlyContinue).Source }
    if ($psExe) {
        & $psExe -ExecutionPolicy Bypass -File $MyInvocation.MyCommand.Path
        exit $LASTEXITCODE
    }
}

$ErrorActionPreference = "Continue"
$issues = @()
$SrcDir = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "_neuron_paths.ps1")
$NP = Get-NeuronPaths
$InstallDir = $NP.InstallDir

function Check {
    param([string]$Label, [scriptblock]$Condition)
    try {
        $ok = & $Condition
        if ($ok) { Write-Host "  [OK] $Label" -ForegroundColor Green }
        else { Write-Host "  [!!] $Label" -ForegroundColor Red; $script:issues += $Label }
    } catch { Write-Host "  [!!] $Label - $_" -ForegroundColor Red; $script:issues += $Label }
}

Write-Host "=== Neuron - Dependency Check ===" -ForegroundColor Cyan
Write-Host ""

# ---- 1. Python ----
Write-Host "1. Python runtime" -ForegroundColor Yellow
Check -Label "Python 3.10+" -Condition { python -c "import sys; exit(0 if sys.version_info >= (3,10) else 1)" 2>$null; $LASTEXITCODE -eq 0 }
Check -Label "not the Microsoft Store Python" -Condition {
    $realPy = (python -c "import sys; print(sys.executable)" 2>$null)
    -not ($realPy -like '*\WindowsApps\*')
}
$InstallVenvPy = "$InstallDir\.venv\Scripts\python.exe"
$RepoVenvPy    = "$SrcDir\.venv\Scripts\python.exe"
Check -Label "venv ($InstallDir)" -Condition { Test-Path $InstallVenvPy }
$py = if (Test-Path $InstallVenvPy) { $InstallVenvPy } elseif (Test-Path $RepoVenvPy) { $RepoVenvPy } else { "python" }

# ---- 2. Python dependencies ----
# Checked FIRST on purpose: if pyturso imports, the Rust/MSVC toolchain is
# irrelevant (it's only a *compile fallback* for when no prebuilt wheel exists).
Write-Host "`n2. Python dependencies" -ForegroundColor Yellow


Check -Label "mcp SDK" -Condition { & $py -c "import mcp" 2>$null; $LASTEXITCODE -eq 0 }
Check -Label "fastembed" -Condition { & $py -c "from fastembed import TextEmbedding" 2>$null; $LASTEXITCODE -eq 0 }
Check -Label "pyturso (Turso DB)" -Condition { & $py -c "import turso" 2>$null; $LASTEXITCODE -eq 0 }
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
    Check -Label "MSVC (via vswhere) or GNU" -Condition { $msvcOk -or ($tc -match "gnu") }
}

# ---- 5. Config ----
Write-Host "`n5. Configuration" -ForegroundColor Yellow
$oc = if ($env:OPENCODE_CONFIG) { $env:OPENCODE_CONFIG } else { "$env:USERPROFILE\.config\opencode\opencode.json" }
Check -Label "opencode.json" -Condition { Test-Path $oc }

# ---- 6. Result ----
Write-Host ""
if ($issues.Count -eq 0) { Write-Host "All OK. Neuron ready." -ForegroundColor Green; exit 0 }
Write-Host "Issues ($($issues.Count)): $($issues -join ', ')" -ForegroundColor Yellow
Write-Host "Re-run install.ps1 or configuration.bat -> Install to fix." -ForegroundColor Cyan
exit 1
