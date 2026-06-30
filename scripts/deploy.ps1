<#
.SYNOPSIS
    Neuron - repeatable deploy/sync from the source repo to the active install.

.DESCRIPTION
    Syncs the source tree to the active MCP install
    (default: %LOCALAPPDATA%\Programs\neuron) WITHOUT touching the toolchain
    (no Rust/MSVC/venv steps - that is install.ps1's job). This is the missing
    "just push my code changes to the install" command: idempotent, previewable
    and verifiable, so source and install no longer drift via manual copies.

    What it does:
      * Copies only changed/new deployable files (code, config, docs, the seed
        knowledge DB), skipping runtime data and junk (see $ExcludeDirs/$ExcludeFile).
      * Never overwrites the install's .venv, graphs\ (per-context runtime DBs) or
        knowledge_grown\ - those are excluded from the deploy set entirely.
      * -DryRun previews every action and changes nothing (verifiable plan).
      * -Prune removes files deleted from source (only inside code dirs).
      * After syncing, verifies the install: byte-compile + import smoke test, and
        (-RunTests) the pytest suite, using the install's own venv.
      * Confirms the deployed __version__ matches the source.

.PARAMETER Dest
    Target install directory. Default: %LOCALAPPDATA%\Programs\neuron

.PARAMETER DryRun
    Show the plan (new/changed/prune) and exit without modifying anything.

.PARAMETER Prune
    Delete files in the install that no longer exist in source (code dirs only:
    src, scripts, skills, clients, tests).

.PARAMETER RunTests
    After syncing, run "python -m pytest tests/ -q" using the install's venv.

.PARAMETER Force
    Don't prompt for confirmation on prune.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\deploy.ps1 -DryRun
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\deploy.ps1 -Prune -RunTests
#>
[CmdletBinding()]
param(
    [string]$Dest = "$env:LOCALAPPDATA\Programs\neuron",
    [switch]$DryRun,
    [switch]$Prune,
    [switch]$RunTests,
    [switch]$Force
)

$ErrorActionPreference = "Stop"

# Repo root = parent of this script's folder (scripts\deploy.ps1 -> repo root)
$Src = Split-Path -Parent $PSScriptRoot

# --- Sanity: are we really pointed at a Neuron source tree? -------------------
if (-not (Test-Path (Join-Path $Src "pyproject.toml")) -or
    -not (Test-Path (Join-Path $Src "src\neuron\server.py"))) {
    Write-Host "ERROR: '$Src' does not look like the Neuron source repo." -ForegroundColor Red
    exit 1
}

Write-Host "Neuron deploy/sync" -ForegroundColor Cyan
Write-Host "  Source: $Src"
Write-Host "  Target: $Dest"
if ($DryRun) { Write-Host "  Mode  : DRY RUN (no changes)" -ForegroundColor Yellow }
Write-Host ""

# --- Exclusion rules (mirror install.ps1 + runtime/junk) ----------------------
$ExcludeDirs  = @(".git",".idea",".vscode",".claude",".venv","venv","build","dist",
                  ".pytest_cache","__pycache__","graphs","knowledge_grown")
$ExcludeFile  = @("*.pyc","*.pyo","*.db-wal","*.db-shm","*.log","*.tmp","*.bak",
                  ".fuse_hidden*",".DS_Store","Thumbs.db",".env")
# excluded by exact relative path (root opencode example only; clients\ copy is kept)
$ExcludeRel   = @("opencode.example.json")
# directories whose deleted files -Prune is allowed to remove
$PruneRoots   = @("src","scripts","skills","clients","tests")

function Test-Excluded([string]$rel) {
    $parts = $rel -split "[\\/]"
    if ($parts.Count -gt 1) {
        foreach ($seg in $parts[0..($parts.Count-2)]) {   # directory segments only
            if ($ExcludeDirs -contains $seg) { return $true }
            if ($seg -like "*.egg-info") { return $true }
        }
    }
    if ($ExcludeRel -contains $rel) { return $true }
    $base = $parts[-1]
    foreach ($g in $ExcludeFile) { if ($base -like $g) { return $true } }
    return $false
}

function Get-RelPath([string]$full, [string]$base) {
    return $full.Substring($base.Length).TrimStart('\','/')
}

# --- Build the source deploy set ----------------------------------------------
$srcFiles = @{}
Get-ChildItem $Src -Recurse -File | ForEach-Object {
    $rel = Get-RelPath $_.FullName $Src
    if (-not (Test-Excluded $rel)) { $srcFiles[$rel] = $_.FullName }
}

# --- Classify: new / changed / unchanged --------------------------------------
$new = New-Object System.Collections.Generic.List[string]
$changed = New-Object System.Collections.Generic.List[string]
$unchanged = 0

foreach ($rel in ($srcFiles.Keys | Sort-Object)) {
    $dst = Join-Path $Dest $rel
    if (-not (Test-Path $dst)) { $new.Add($rel); continue }
    $hs = (Get-FileHash -Algorithm MD5 $srcFiles[$rel]).Hash
    $hd = (Get-FileHash -Algorithm MD5 $dst).Hash
    if ($hs -ne $hd) { $changed.Add($rel) } else { $unchanged++ }
}

# --- Prune candidates (files in install code dirs not present in source) ------
$pruneList = New-Object System.Collections.Generic.List[string]
if ($Prune -and (Test-Path $Dest)) {
    foreach ($root in $PruneRoots) {
        $rdir = Join-Path $Dest $root
        if (-not (Test-Path $rdir)) { continue }
        Get-ChildItem $rdir -Recurse -File | ForEach-Object {
            $rel = Get-RelPath $_.FullName $Dest
            if ((Test-Excluded $rel)) { return }
            if (-not $srcFiles.ContainsKey($rel)) { $pruneList.Add($rel) }
        }
    }
}

# --- Report -------------------------------------------------------------------
$pruneNote = ""
if ($Prune) { $pruneNote = ", $($pruneList.Count) to prune" }
Write-Host "Plan: $($new.Count) new, $($changed.Count) changed, $unchanged unchanged$pruneNote" -ForegroundColor Cyan
foreach ($f in $new)       { Write-Host "  + $f" -ForegroundColor Green }
foreach ($f in $changed)   { Write-Host "  ~ $f" -ForegroundColor Yellow }
foreach ($f in $pruneList) { Write-Host "  - $f" -ForegroundColor Red }

if ($DryRun) {
    Write-Host ""
    Write-Host "Dry run complete - no changes made." -ForegroundColor Yellow
    exit 0
}

if ($new.Count -eq 0 -and $changed.Count -eq 0 -and $pruneList.Count -eq 0) {
    Write-Host ""
    Write-Host "Already in sync. Nothing to copy." -ForegroundColor Green
} else {
    # --- Copy new + changed ---
    foreach ($rel in ($new + $changed)) {
        $dst = Join-Path $Dest $rel
        $dir = Split-Path $dst -Parent
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
        Copy-Item $srcFiles[$rel] $dst -Force
    }
    Write-Host "Copied $($new.Count + $changed.Count) file(s)." -ForegroundColor Green

    # --- Prune ---
    if ($pruneList.Count -gt 0) {
        $ok = [bool]$Force
        if (-not $ok) {
            $ans = Read-Host "Delete $($pruneList.Count) stale file(s) from the install? [y/N]"
            $ok = ($ans -eq 'y' -or $ans -eq 'Y')
        }
        if ($ok) {
            foreach ($rel in $pruneList) { Remove-Item (Join-Path $Dest $rel) -Force }
            Write-Host "Pruned $($pruneList.Count) file(s)." -ForegroundColor Green
        } else {
            Write-Host "Prune skipped." -ForegroundColor DarkYellow
        }
    }
}

# --- Version parity check -----------------------------------------------------
function Get-Version([string]$initPath) {
    if (-not (Test-Path $initPath)) { return $null }
    foreach ($ln in (Get-Content $initPath)) {
        if ($ln -like '*__version__*=*') {
            return (($ln -split '=')[1]).Trim().Trim('"').Trim("'").Trim()
        }
    }
    return $null
}
$sver = Get-Version (Join-Path $Src  "src\neuron\__init__.py")
$dver = Get-Version (Join-Path $Dest "src\neuron\__init__.py")
Write-Host ""
Write-Host "Version: source=$sver  install=$dver" -ForegroundColor Cyan
if ($sver -and $dver -and $sver -ne $dver) {
    Write-Host "WARNING: version mismatch after sync." -ForegroundColor Red
}

# --- Verify the install -------------------------------------------------------
$pyExe = Join-Path $Dest ".venv\Scripts\python.exe"
$verifyFailed = $false
if (Test-Path $pyExe) {
    Write-Host ""
    Write-Host "Verifying install..." -ForegroundColor Yellow
    Push-Location $Dest
    try {
        & $pyExe -m compileall -q src | Out-Null
        if ($LASTEXITCODE -ne 0) { Write-Host "  [FAIL] byte-compile" -ForegroundColor Red; $verifyFailed = $true }
        else { Write-Host "  [OK] byte-compile" -ForegroundColor Green }

        & $pyExe -c "import sys; sys.path.insert(0,'src'); import neuron.server"
        if ($LASTEXITCODE -ne 0) { Write-Host "  [FAIL] import neuron.server" -ForegroundColor Red; $verifyFailed = $true }
        else { Write-Host "  [OK] import neuron.server" -ForegroundColor Green }

        if ($RunTests) {
            & $pyExe -m pytest tests/ -q
            if ($LASTEXITCODE -ne 0) { Write-Host "  [FAIL] pytest" -ForegroundColor Red; $verifyFailed = $true }
            else { Write-Host "  [OK] pytest" -ForegroundColor Green }
        }
    } finally { Pop-Location }
} else {
    Write-Host ""
    Write-Host "Note: no venv at $pyExe - skipping verification (run install.ps1 first)." -ForegroundColor DarkYellow
}

if ($verifyFailed) {
    Write-Host ""
    Write-Host "Deploy completed WITH verification failures." -ForegroundColor Red
    exit 1
}
Write-Host ""
Write-Host "Deploy complete." -ForegroundColor Green
exit 0
