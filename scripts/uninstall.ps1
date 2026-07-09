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

# A Microsoft Store Python virtualizes its filesystem: a venv "created" under
# $P.InstallDir can end up silently redirected into that package's own
# LocalCache instead, invisible from a normal view of the install dir - a
# common cause of "installed but Neuron can't find its own folders" corruption.
# Only ever touches paths under our own install slug, inside the Python
# package's LocalCache - never anything else in %LOCALAPPDATA%\Packages.
function Remove-StorePythonShadowCopy([string]$Slug) {
    $local = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { "$env:USERPROFILE\AppData\Local" }
    $packagesDir = Join-Path $local "Packages"
    if (-not (Test-Path $packagesDir)) { return }
    $pyPackages = Get-ChildItem -LiteralPath $packagesDir -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like "*PythonSoftwareFoundation.Python*" }
    if (-not $pyPackages) { return }
    foreach ($pkg in $pyPackages) {
        $shadow = Join-Path $pkg.FullName "LocalCache\Local\Programs\$Slug"
        Remove-Guarded $shadow $Slug 'Store-Python shadow copy'
    }
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
    ($cfg | ConvertTo-Json -Depth 32) | Set-Content -LiteralPath $t.path -Encoding utf8NoBOM
    Write-Host "  [OK] de-registered from $($t.app) (backup: $($t.path).neuron-bak)" -ForegroundColor Green
}

# Undo Install-OpenCodeHandshakePlugin / Install-ClaudeCodeSessionHook (see
# scripts\configuration.ps1): removes the OpenCode plugin file + its
# opencode.json registration, and the Neuron entries from Claude Code's
# SessionStart hooks - without touching any other plugin/hook the user has
# configured (ponytail, third-party PreToolUse hooks, ...). Paths are
# $env:USERPROFILE-based, so this is correct on any Windows account.
function Remove-ClientPlugins {
    $any = $false

    $ocPath       = "$env:USERPROFILE\.config\opencode\opencode.json"
    $ocPluginFile = "$env:USERPROFILE\.config\opencode\plugins\neuron-handshake.mjs"
    if (Test-Path -LiteralPath $ocPath) {
        try { $cfg = Get-Content -LiteralPath $ocPath -Raw | ConvertFrom-Json -ErrorAction Stop }
        catch { $cfg = $null }
        if ($cfg -and $cfg.PSObject.Properties['plugin'] -and $null -ne $cfg.plugin) {
            $before = @($cfg.plugin)
            $after  = @($before | Where-Object { $_ -ne $ocPluginFile })
            if ($after.Count -ne $before.Count) {
                if ($DryRun) { Write-Host "  [dry-run] would remove neuron-handshake from OpenCode's plugin[]" -ForegroundColor Cyan }
                else {
                    Copy-Item -LiteralPath $ocPath "$ocPath.neuron-bak" -Force -ErrorAction SilentlyContinue
                    $cfg.plugin = $after
                    ($cfg | ConvertTo-Json -Depth 32) | Set-Content -LiteralPath $ocPath -Encoding utf8NoBOM
                    Write-Host "  [OK] Removed neuron-handshake entry from OpenCode's opencode.json" -ForegroundColor Green
                }
                $any = $true
            }
        }
    }
    if (Test-Path -LiteralPath $ocPluginFile) {
        if ($DryRun) { Write-Host "  [dry-run] would delete $ocPluginFile" -ForegroundColor Cyan }
        else {
            Remove-Item -LiteralPath $ocPluginFile -Force -ErrorAction SilentlyContinue
            Write-Host "  [OK] Deleted $ocPluginFile" -ForegroundColor Green
        }
        $any = $true
    }

    $ccPath = "$env:USERPROFILE\.claude\settings.json"
    if (Test-Path -LiteralPath $ccPath) {
        try { $cfg = Get-Content -LiteralPath $ccPath -Raw | ConvertFrom-Json -ErrorAction Stop }
        catch { $cfg = $null }
        if ($cfg -and $cfg.PSObject.Properties['hooks'] -and $cfg.hooks -and
            $cfg.hooks.PSObject.Properties['SessionStart'] -and $null -ne $cfg.hooks.SessionStart) {
            $changed   = $false
            $newGroups = @()
            foreach ($g in @($cfg.hooks.SessionStart)) {
                $beforeHooks = @($g.hooks)
                $afterHooks  = @($beforeHooks | Where-Object {
                    -not ($_.command -and $_.command -match [regex]::Escape('neuron_sessionstart_hook.py'))
                })
                if ($afterHooks.Count -ne $beforeHooks.Count) { $changed = $true }
                if ($afterHooks.Count -gt 0) { $g.hooks = $afterHooks; $newGroups += $g }
            }
            if ($changed) {
                if ($DryRun) { Write-Host "  [dry-run] would remove Neuron SessionStart hook from Claude Code's settings.json" -ForegroundColor Cyan }
                else {
                    Copy-Item -LiteralPath $ccPath "$ccPath.neuron-bak" -Force -ErrorAction SilentlyContinue
                    $cfg.hooks.SessionStart = $newGroups
                    ($cfg | ConvertTo-Json -Depth 32) | Set-Content -LiteralPath $ccPath -Encoding utf8NoBOM
                    Write-Host "  [OK] Removed Neuron SessionStart hook from Claude Code's settings.json" -ForegroundColor Green
                }
                $any = $true
            }
        }
    }
    if (-not $any) { Write-Host "  - client plugins/hooks : none found" -ForegroundColor DarkGray }
}

function Scrub-Env([string]$envPath) {
    if (-not (Test-Path -LiteralPath $envPath)) { Write-Host "  - .env : not present" -ForegroundColor DarkGray; return }
    $lines = Get-Content -LiteralPath $envPath
    $kept  = $lines | Where-Object { $_ -notmatch '^\s*(TURSO_[A-Z_]+|[A-Za-z0-9]+_(API_KEY|TOKEN))\s*=' }
    $n = $lines.Count - $kept.Count
    if ($n -le 0) { Write-Host "  - .env : no secret lines to scrub" -ForegroundColor DarkGray; return }
    if ($DryRun) { Write-Host "  [dry-run] would scrub $n secret line(s) from $envPath" -ForegroundColor Cyan; return }
    Copy-Item -LiteralPath $envPath "$envPath.neuron-bak" -Force -ErrorAction SilentlyContinue
    $kept | Set-Content -LiteralPath $envPath -Encoding utf8NoBOM
    Write-Host "  [OK] scrubbed $n secret line(s) from .env (backup: $envPath.neuron-bak)" -ForegroundColor Green
}

# --- plan -------------------------------------------------------------------
Write-Host ""
Write-Host "  Neuron uninstaller - slug '$($P.Slug)'$(if ($DryRun) {' (DRY RUN)'})" -ForegroundColor Yellow
Write-Host "  Will remove (base):" -ForegroundColor Gray
Write-Host "    - install dir : $($P.InstallDir)"
Write-Host "    - Start Menu  : $($P.StartMenu)"
Write-Host "    - MCP entries : $(( $P.RegistrationTargets | ForEach-Object { $_.app }) -join ', ')"
Write-Host "    - client plugins/hooks : OpenCode handshake plugin, Claude Code SessionStart hook (if present)"
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
Remove-ClientPlugins
Remove-StorePythonShadowCopy $P.Slug

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
