<#
.SYNOPSIS
  Remove Neuron from this machine - you choose HOW MUCH.

.DESCRIPTION
  Nothing destructive happens by default beyond the app itself, and the source
  repo is never deleted. Escalating removal is opt-in per flag, each confirmed:

    (base, always)   install dir + venv/deps, Start-Menu shortcut, MCP de-registration
    -Data            + the memory store (%LOCALAPPDATA%\<slug>\graphs, legacy neuron\graphs,
                       and this repo's graphs\*.db) - your knowledge graph. IRREVERSIBLE.
    -Secrets         + scrub .env (TURSO_* and *_API_KEY / *_TOKEN lines)
    -Cache           + the fastembed/HuggingFace model cache (~80MB, re-downloads next run)
    -All             = -Data -Secrets -Cache

  On-demand system tools possibly installed as fallbacks (Rust, MSVC Build Tools,
  uv, cloudflared) are NEVER auto-removed - they may be shared. They are only listed.

  Your Turso CLOUD database is never touched from here (delete it via the Turso CLI).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\uninstall.ps1            # app only
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\uninstall.ps1 -All -DryRun   # preview a full wipe
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\uninstall.ps1 -Data -Yes     # app + data, no prompts
#>
[CmdletBinding()]
param(
    [string]$Slug,
    [switch]$Data,
    [switch]$Secrets,
    [switch]$Cache,
    [switch]$All,
    [switch]$Yes,        # skip confirmations (for automation)
    [switch]$DryRun      # print what would happen, change nothing
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '_neuron_paths.ps1')
if ($All) { $Data = $true; $Secrets = $true; $Cache = $true }

$P    = Get-NeuronPaths -Slug $Slug
$Repo = Split-Path -Parent $PSScriptRoot

function Confirm-Step([string]$msg) {
    if ($Yes) { return $true }
    $a = Read-Host "$msg [y/N]"
    return ($a -match '^(y|yes)$')
}

# Remove a path only if it looks like what we expect (guard against a collapsed
# path like "\Programs\neuron5" at a drive root when an env var was empty).
function Remove-Guarded([string]$path, [string]$mustContain, [string]$label) {
    if (-not $path) { return }
    if (-not (Test-Path -LiteralPath $path)) { Write-Host "  - $label : not present" -ForegroundColor DarkGray; return }
    $full = (Resolve-Path -LiteralPath $path).Path
    if ($mustContain -and ($full -notmatch [regex]::Escape($mustContain))) {
        Write-Host "  [!] SKIP $label : '$full' doesn't look right (expected to contain '$mustContain')" -ForegroundColor Yellow
        return
    }
    if ($DryRun) { Write-Host "  [dry-run] would remove $label : $full" -ForegroundColor Cyan; return }
    Remove-Item -LiteralPath $full -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "  [OK] removed $label : $full" -ForegroundColor Green
}

function Remove-McpEntry($t) {
    if (-not (Test-Path -LiteralPath $t.path)) { return }
    try { $cfg = Get-Content -LiteralPath $t.path -Raw | ConvertFrom-Json -ErrorAction Stop }
    catch { Write-Host "  [!] $($t.app): not plain JSON - remove '$($P.Slug)' by hand ($($t.path))" -ForegroundColor Yellow; return }
    $parent = $cfg
    for ($i = 0; $i -lt $t.keys.Count - 1; $i++) {
        $k = $t.keys[$i]
        if ($parent.PSObject.Properties[$k]) { $parent = $parent.$k } else { return }  # nothing registered
    }
    $leaf = $t.keys[$t.keys.Count - 1]
    if (-not $parent.PSObject.Properties[$leaf]) { return }
    if ($DryRun) { Write-Host "  [dry-run] would de-register from $($t.app)" -ForegroundColor Cyan; return }
    Copy-Item -LiteralPath $t.path "$($t.path).neuron-bak" -Force -ErrorAction SilentlyContinue
    $parent.PSObject.Properties.Remove($leaf)
    ($cfg | ConvertTo-Json -Depth 32) | Set-Content -LiteralPath $t.path -Encoding UTF8
    Write-Host "  [OK] de-registered from $($t.app) (backup: $($t.path).neuron-bak)" -ForegroundColor Green
}

function Scrub-Env([string]$envPath) {
    if (-not (Test-Path -LiteralPath $envPath)) { Write-Host "  - .env : not present" -ForegroundColor DarkGray; return }
    $lines = Get-Content -LiteralPath $envPath
    $kept  = $lines | Where-Object { $_ -notmatch '^\s*(TURSO_[A-Z_]+|[A-Za-z0-9]+_(API_KEY|TOKEN))\s*=' }
    $n = $lines.Count - $kept.Count
    if ($n -le 0) { Write-Host "  - .env : no secret lines to scrub" -ForegroundColor DarkGray; return }
    if ($DryRun) { Write-Host "  [dry-run] would scrub $n secret line(s) from $envPath" -ForegroundColor Cyan; return }
    Copy-Item -LiteralPath $envPath "$envPath.neuron-bak" -Force -ErrorAction SilentlyContinue
    $kept | Set-Content -LiteralPath $envPath -Encoding UTF8
    Write-Host "  [OK] scrubbed $n secret line(s) from .env (backup: $envPath.neuron-bak)" -ForegroundColor Green
}

# --- plan -------------------------------------------------------------------
Write-Host ""
Write-Host "  Neuron uninstaller - slug '$($P.Slug)'$(if ($DryRun) {' (DRY RUN)'})" -ForegroundColor Yellow
Write-Host "  Will remove (base):" -ForegroundColor Gray
Write-Host "    - install dir : $($P.InstallDir)"
Write-Host "    - Start Menu  : $($P.StartMenu)"
Write-Host "    - MCP entries : $(( $P.RegistrationTargets | ForEach-Object { $_.app }) -join ', ')"
if ($Data)    { Write-Host "    - DATA        : $($P.StoreDir) (+ legacy neuron\graphs, repo graphs\*.db)  IRREVERSIBLE" -ForegroundColor Red }
if ($Secrets) { Write-Host "    - SECRETS     : scrub $Repo\.env (Turso token + API keys)" -ForegroundColor Red }
if ($Cache)   { Write-Host "    - CACHE       : $(( $P.ModelCaches ) -join '; ')" -ForegroundColor Red }
Write-Host ""
if (-not (Confirm-Step "Proceed?")) { Write-Host "  Cancelled - nothing changed." -ForegroundColor DarkYellow; return }

# --- base -------------------------------------------------------------------
Write-Host "`n  Removing app..." -ForegroundColor Yellow
if (-not $DryRun) { Stop-NeuronServices -InstallDir $P.InstallDir -Yes:$Yes }  # unlock venv files first
Remove-Guarded $P.InstallDir 'Programs' 'install dir'
Remove-Guarded $P.StartMenu  'Start Menu' 'Start-Menu shortcut'
foreach ($t in $P.RegistrationTargets) { Remove-McpEntry $t }

# --- data (opt-in) ----------------------------------------------------------
if ($Data -and (Confirm-Step "Delete your memory data? This cannot be undone.")) {
    Remove-Guarded $P.StoreDir 'graphs' 'memory store'
    $legacy = Join-Path (Get-LocalAppData) 'neuron\graphs'
    Remove-Guarded $legacy 'graphs' 'legacy v4 store'
    $repoGraphs = Join-Path $Repo 'graphs'
    if (Test-Path -LiteralPath $repoGraphs) {
        if ($DryRun) { Write-Host "  [dry-run] would clear $repoGraphs\*.db*" -ForegroundColor Cyan }
        else {
            Get-ChildItem -LiteralPath $repoGraphs -Filter '*.db*' -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
            Write-Host "  [OK] cleared repo graphs\*.db" -ForegroundColor Green
        }
    }
}

# --- secrets (opt-in) -------------------------------------------------------
if ($Secrets) { Write-Host "`n  Scrubbing secrets..." -ForegroundColor Yellow; Scrub-Env (Join-Path $Repo '.env') }

# --- cache (opt-in) ---------------------------------------------------------
if ($Cache) {
    Write-Host "`n  Removing model cache..." -ForegroundColor Yellow
    foreach ($c in $P.ModelCaches) { Remove-Guarded $c 'cache' 'model cache' }
}

# --- always: report what we deliberately did NOT remove ---------------------
Write-Host "`n  Left in place (remove manually if you want them gone):" -ForegroundColor DarkGray
Write-Host "    - On-demand tools possibly installed as fallbacks: Rust (rustup), MSVC Build Tools, uv, cloudflared." -ForegroundColor DarkGray
Write-Host "    - Your Turso CLOUD database (delete via: turso db destroy NAME)." -ForegroundColor DarkGray
Write-Host "    - This source repo ($Repo)." -ForegroundColor DarkGray
if (-not $Data)    { Write-Host "    - Memory data (re-run with -Data to delete)." -ForegroundColor DarkGray }
if (-not $Secrets) { Write-Host "    - .env secrets (re-run with -Secrets to scrub)." -ForegroundColor DarkGray }
if (-not $Cache)   { Write-Host "    - Model cache (re-run with -Cache to delete)." -ForegroundColor DarkGray }
Write-Host "`n  Done.$(if ($DryRun) {' (dry run - nothing was actually changed)'})" -ForegroundColor Green
