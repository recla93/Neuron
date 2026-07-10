<#
  _neuron_utils.ps1 — shared utilities for install / uninstall / configuration.

  Dot-source this from any script that needs JSON reading/writing, MCP config
  manipulation, filesystem cleanup, or process management.  Every function here
  is standalone (no dependency on the caller's variables beyond the parameters
  listed), so uninstall.ps1 and configuration.ps1 behave identically.

  DESIGN RULE: every JSON write uses -Depth 100 (never 32). The lower value
  silently truncates deeply nested configs (VS Code settings.json with many
  extensions, Claude Code's GrowthBook flags) to the literal string
  "System.Collections.Hashtable" — valid JSON on disk but completely broken.
  See T1 in FiveFix_Analisi_e_Piano_di_Risoluzione.md.

  DESIGN RULE: every Remove-Item goes through GuaranteedRemove-Item (3 retries
  with a 1.5 s delay), because OneDrive Known-Folder-Move can lock files for
  seconds during sync, and a single Remove-Item -Force silently fails.

  DESIGN RULE: Stop-NeuronServices has a CIM → Get-Process fallback chain, so
  it never blocks uninstall on Windows N/KN editions or broken WMI.
#>

# ---------------------------------------------------------------------------
# Retrying filesystem delete (OneDrive-safe)
# ---------------------------------------------------------------------------
function GuaranteedRemove-Item {
    <#
    .SYNOPSIS
      Remove-Item with up to $Retries attempts and a delay between them.

      OneDrive's Known-Folder-Move can hold a file lock for 1-3 s during
      sync, which makes a single Remove-Item -Force fail silently even though
      the file IS deletable a moment later.  Three retries at 1.5 s intervals
      handle this without blocking the install for long.
    #>
    param(
        [Parameter(Mandatory)][string]$LiteralPath,
        [int]$Retries = 3,
        [int]$DelayMs = 1500,
        [switch]$Recurse
    )
    $recurseFlag = @{}
    if ($Recurse) { $recurseFlag['Recurse'] = $true }
    for ($i = 1; $i -le $Retries; $i++) {
        Remove-Item -LiteralPath $LiteralPath -Force -ErrorAction SilentlyContinue @recurseFlag
        if (-not (Test-Path -LiteralPath $LiteralPath)) { return $true }
        if ($i -lt $Retries) { Start-Sleep -Milliseconds $DelayMs }
    }
    Write-Host "  [!] Could not fully remove after $Retries attempts: $LiteralPath" -ForegroundColor Yellow
    return $false
}

# ---------------------------------------------------------------------------
# JSON helpers (Load / Save / Assert)
# ---------------------------------------------------------------------------
function Load-Json {
    <#
    .SYNOPSIS
      Read a JSON file.
      Returns:  parsed PSObject;  NEW empty PSObject for a missing/empty file;
      or $null when the file EXISTS but can't be parsed (JSONC // comments,
      trailing commas — common in VS Code settings.json).  $null means "hands
      off, don't overwrite".
    #>
    param([string]$path)
    if (Test-Path $path) {
        $raw = Get-Content $path -Raw -ErrorAction SilentlyContinue
        if ($raw -and $raw.Trim()) {
            try { return ($raw | ConvertFrom-Json) }
            catch { return $null }
        }
    }
    return (New-Object psobject)
}

function Save-Json {
    <#
    .SYNOPSIS
      Serialize $obj to $path with backup, verification, and rollback.

      Returns $true on success, $false on failure (and rolls back the file).
      Backup filename includes a timestamp (T15: *.neuron-bak.YYYYMMDD-HHmmss)
      so multiple runs never overwrite each other.
    #>
    param([object]$obj, [string]$path)
    $dir = Split-Path -Parent $path
    if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $backup = $null
    if (Test-Path $path) {
        $backup = "$path.neuron-bak.$timestamp"
        Copy-Item $path $backup -Force -ErrorAction SilentlyContinue
    }
    try {
        Write-Utf8NoBom -Path $path -Content ($obj | ConvertTo-Json -Depth 100)
    } catch {
        Write-Host "  [X] Could not write $path : $_" -ForegroundColor Red
        return $false
    }
    try { Get-Content $path -Raw | ConvertFrom-Json -ErrorAction Stop | Out-Null }
    catch {
        $verifyErr = $_.Exception.Message
        $failCopy = "$path.neuron-failed-write.$timestamp"
        try { Copy-Item $path $failCopy -Force -ErrorAction SilentlyContinue } catch {}
        if ($backup) { Copy-Item $backup $path -Force -ErrorAction SilentlyContinue }
        Write-Host "  [X] Write verification failed — restored your original file." -ForegroundColor Red
        Write-Host "      Reason: $verifyErr" -ForegroundColor Red
        Write-Host "      Failed output saved for inspection: $failCopy" -ForegroundColor DarkYellow
        return $false
    }
    Write-Host "  [OK] Wrote $path" -ForegroundColor Green
    Write-Host "       (backup: $backup)" -ForegroundColor DarkGray
    return $true
}

function Assert-JsonKey {
    <#
    .SYNOPSIS
      After Save-Json succeeds, re-read $Path and confirm the nested key chain
      $Keys actually exists on disk.  Returns $true when present.
    #>
    param([string]$Path, [string[]]$Keys, [string]$Label)
    try {
        $cfg = Get-Content $Path -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
    } catch {
        Write-Host "  [X] $Label - could not re-read $Path to verify: $_" -ForegroundColor Red
        return $false
    }
    $cur = $cfg
    foreach ($k in $Keys) {
        if ($null -eq $cur -or -not $cur.PSObject.Properties[$k]) {
            $keyPath = ($Keys -join ".")
            Write-Host "  [X] $Label - Save-Json returned OK but '$keyPath' is MISSING from" -ForegroundColor Red
            Write-Host "      $Path after the write (silent-rollback bug pattern)." -ForegroundColor Red
            Write-Host "      Add it by hand, or re-run after inspecting a backup." -ForegroundColor Red
            return $false
        }
        $cur = $cur.$k
    }
    return $true
}

# ---------------------------------------------------------------------------
# Set-Prop — helper used by MCP registration code
# ---------------------------------------------------------------------------
function Set-Prop {
    param([object]$obj, [string]$name, [object]$value)
    if ($obj.PSObject.Properties[$name]) { $obj.$name = $value }
    else { $obj | Add-Member -NotePropertyName $name -NotePropertyValue $value }
}

# ---------------------------------------------------------------------------
# Remove-Prop — used by MCP de-registration
# ---------------------------------------------------------------------------
function Remove-Prop {
    param([object]$obj, [string]$name)
    if ($obj -and $obj.PSObject.Properties[$name]) { $obj.PSObject.Properties.Remove($name) }
}

# ---------------------------------------------------------------------------
# Get-Child — safe navigation through nested PSObject properties
# ---------------------------------------------------------------------------
function Get-Child {
    param([object]$obj, [string]$name)
    if ($obj -and $obj.PSObject.Properties[$name]) { return $obj.$name }
    return $null
}

# ---------------------------------------------------------------------------
# Stop running Neuron processes
# ---------------------------------------------------------------------------
function Stop-NeuronServices {
    <#
    .SYNOPSIS
      Find and kill any running Neuron MCP server under $InstallDir.
      Uses Get-CimInstance first (native); falls back to Get-Process if CIM
      is unavailable (Windows N / broken WMI — T2 fix).

      Never touches $PID.
    #>
    param([string]$InstallDir, [switch]$Yes)
    $pat = '(?i)(-m\s+neuron\b|\\run_mcp\.bat'
    if ($InstallDir) { $pat += '|' + [regex]::Escape($InstallDir) }
    $pat += ')'
    $procs = @()
    try {
        $procs = @(Get-CimInstance Win32_Process -ErrorAction Stop |
            Where-Object { $_.CommandLine -and ($_.CommandLine -match $pat) -and $_.ProcessId -ne $PID })
    } catch {
        # CIM unavailable — fall back to Get-Process by path prefix (T2).
        if ($InstallDir) {
            $root = $InstallDir.ToLower()
            $procs = @(Get-Process -ErrorAction SilentlyContinue |
                Where-Object {
                    try { $_.Id -ne $PID -and $_.Path -and $_.Path.ToLower().StartsWith($root) }
                    catch { $false }
                } |
                ForEach-Object { [pscustomobject]@{ ProcessId = $_.Id; CommandLine = $_.Path } })
        }
    }
    if ($procs.Count -eq 0) { return }
    Write-Host "   Found running Neuron process(es):" -ForegroundColor Yellow
    $procs | ForEach-Object { Write-Host ("     PID {0}: {1}" -f $_.ProcessId, $_.CommandLine) -ForegroundColor DarkGray }
    $stop = if ($Yes) { $true } else { (Read-Host "   Stop them first (avoids locked files)? [Y/n]") -notmatch '^\s*(n|no)\s*$' }
    if (-not $stop) { Write-Host "   Continuing without stopping — may fail on locked files." -ForegroundColor DarkYellow; return }
    foreach ($p in $procs) {
        try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop; Write-Host "     stopped PID $($p.ProcessId)" -ForegroundColor Green }
        catch { Write-Host "     could not stop PID $($p.ProcessId): $_" -ForegroundColor Red }
    }
    Start-Sleep -Seconds 1
}

# ---------------------------------------------------------------------------
# MCP registration manipulation (add / remove)
# ---------------------------------------------------------------------------

function Remove-McpRegistrations {
    <#
    .SYNOPSIS
      Remove the $Slug entry from every AI-app MCP config listed in
      $RegistrationTargets (passed in).  Uses Load-Json / Save-Json so it
      handles JSONC files safely (does nothing, warns) and verifies the write.
    #>
    param(
        [Parameter(Mandatory)][string]$Slug,
        [Parameter(Mandatory)][array]$RegistrationTargets
    )
    $removed = 0
    foreach ($t in $RegistrationTargets) {
        if ($t.format -eq 'toml') { Write-Host "  [!] TOML config - remove '$Slug' by hand from $($t.path)" -ForegroundColor DarkYellow; continue }
        if ($t.format -eq 'hooks-json') { continue }
        if (-not (Test-Path $t.path)) { continue }
        $cfg = Load-Json $t.path
        if ($null -eq $cfg) {
            Write-Host "  [!] Skipped $($t.app): its config isn't plain JSON — remove '$Slug' by hand." -ForegroundColor DarkYellow
            continue
        }
        $parent = $cfg
        for ($i = 0; $i -lt $t.keys.Count - 1; $i++) { $parent = Get-Child $parent $t.keys[$i]; if (-not $parent) { break } }
        $leaf = $t.keys[$t.keys.Count - 1]
        if ($parent -and $parent.PSObject.Properties[$leaf]) {
            Remove-Prop $parent $leaf
            Save-Json $cfg $t.path
            Write-Host "  [OK] Removed '$Slug' from $($t.app)" -ForegroundColor Green
            $removed++
        }
    }
    if ($removed -eq 0) { Write-Host "  (No AI app had a '$Slug' entry to remove.)" -ForegroundColor DarkGray }
}

# ---------------------------------------------------------------------------
# Client plugins / hooks removal
# ---------------------------------------------------------------------------
function Remove-ClientPlugins {
    <#
    .SYNOPSIS
      Remove the OpenCode handshake plugin + its opencode.json registration,
      and the Neuron entries from Claude Code's SessionStart hooks.

    .DESCRIPTION
      OpenCode config path: $env:OPENCODE_CONFIG → $env:USERPROFILE\.config\opencode\opencode.json
      Claude Code config:   $env:CLAUDE_CONFIG   → $env:USERPROFILE\.claude\settings.json
      Set these env vars to override the default paths (e.g. portable/Insiders installs).
    #>
    $any = $false

    # --- OpenCode ---
    $ocConfigPath = if ($env:OPENCODE_CONFIG) { $env:OPENCODE_CONFIG }
                    else { "$env:USERPROFILE\.config\opencode\opencode.json" }
    $ocConfigDir  = Split-Path -Parent $ocConfigPath -ErrorAction SilentlyContinue
    $ocPluginDir  = if ($ocConfigDir) { Join-Path $ocConfigDir "plugins" }
                    else { "$env:USERPROFILE\.config\opencode\plugins" }
    $ocPluginFile = Join-Path $ocPluginDir "neuron-handshake.mjs"

    if (Test-Path $ocConfigPath) {
        $cfg = Load-Json $ocConfigPath
        if ($cfg -and $cfg.PSObject.Properties['plugin'] -and $null -ne $cfg.plugin) {
            $before = @($cfg.plugin)
            $after  = @($before | Where-Object { $_ -ne $ocPluginFile })
            if ($after.Count -ne $before.Count) {
                Set-Prop $cfg 'plugin' $after
                Save-Json $cfg $ocConfigPath
                Write-Host "  [OK] Removed neuron-handshake entry from OpenCode's opencode.json" -ForegroundColor Green
                $any = $true
            }
        }
    }
    if (Test-Path $ocPluginFile) {
        GuaranteedRemove-Item -LiteralPath $ocPluginFile
        Write-Host "  [OK] Deleted $ocPluginFile" -ForegroundColor Green
        $any = $true
    }

    # --- Claude Code: ~/.claude/settings.json hooks.SessionStart ---
    $ccSettingsPath = if ($env:CLAUDE_CONFIG) { $env:CLAUDE_CONFIG }
                      else { "$env:USERPROFILE\.claude\settings.json" }
    if (Test-Path $ccSettingsPath) {
        $cfg = Load-Json $ccSettingsPath
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
                if ($afterHooks.Count -gt 0) {
                    Set-Prop $g 'hooks' $afterHooks
                    $newGroups += $g
                }
            }
            if ($changed) {
                Set-Prop $cfg.hooks 'SessionStart' $newGroups
                Save-Json $cfg $ccSettingsPath
                Write-Host "  [OK] Removed Neuron SessionStart hook from Claude Code's settings.json" -ForegroundColor Green
                $any = $true
            }
        }
    }

    # --- Codex CLI: ~/.codex/hooks.json ------
    $codexHookPath = "$env:USERPROFILE\.codex\hooks.json"
    if (Test-Path $codexHookPath) {
        if (GuaranteedRemove-Item -LiteralPath $codexHookPath) {
            Write-Host "  [OK] Deleted Codex CLI hooks.json" -ForegroundColor Green; $any = $true
        } else { Write-Host "  [X] Could not delete $codexHookPath - remove by hand." -ForegroundColor Red }
    }

    if (-not $any) { Write-Host "  (No OpenCode plugin, Codex hook, or Claude Code hook found to remove.)" -ForegroundColor DarkGray }
    return $any
}

# ---------------------------------------------------------------------------
# .env scrubbing
# ---------------------------------------------------------------------------
function Scrub-Env {
    <#
    .SYNOPSIS
      Remove TURSO_* and *_API_KEY / *_TOKEN lines from a .env file.
      Keeps everything else.  Creates a timestamped backup.
    #>
    param([string]$EnvPath)
    if (-not (Test-Path -LiteralPath $EnvPath)) {
        Write-Host "  - .env : not present" -ForegroundColor DarkGray
        return
    }
    $lines = Get-Content -LiteralPath $EnvPath
    $kept  = $lines | Where-Object { $_ -notmatch '^\s*(TURSO_[A-Z_]+|[A-Za-z0-9]+_(API_KEY|TOKEN))\s*=' }
    $n = $lines.Count - $kept.Count
    if ($n -le 0) {
        Write-Host "  - .env : no secret lines to scrub" -ForegroundColor DarkGray
        return
    }
    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    Copy-Item -LiteralPath $EnvPath "$EnvPath.neuron-bak.$timestamp" -Force -ErrorAction SilentlyContinue
    Write-Utf8NoBom -Path $EnvPath -Content $kept
    Write-Host "  [OK] scrubbed $n secret line(s) from .env (backup: $EnvPath.neuron-bak.$timestamp)" -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# Microsoft Store Python shadow copy removal
# ---------------------------------------------------------------------------
function Remove-StorePythonShadowCopy {
    <#
    .SYNOPSIS
      Microsoft Store Python virtualizes its filesystem: a venv created under
      $InstallDir can be silently redirected into that Store package's own
      LocalCache.  Remove-InstallDir alone can't clean that up.

      T3 fix: uses Get-AppxPackage FIRST (covers ALL Store Python packages
      regardless of naming), then falls back to filesystem glob in
      %LOCALAPPDATA%\Packages for systems without Get-AppxPackage.
      Also probes multiple candidate shadow folder paths.
    #>
    param([string]$Slug)

    $local = Get-LocalAppData
    $packagesDir = Join-Path $local "Packages"

    # --- Collect Python packages ---
    $pyPackages = @()
    try {
        $appx = Get-AppxPackage -Name "*Python*" -ErrorAction SilentlyContinue
        foreach ($p in $appx) {
            $pyPackages += [pscustomobject]@{
                PackageFamilyName = $p.PackageFamilyName
                InstallLocation   = $p.InstallLocation
            }
        }
    } catch {}
    if (-not $pyPackages -and (Test-Path $packagesDir)) {
        $dirs = Get-ChildItem -LiteralPath $packagesDir -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match '(?i)python' }
        foreach ($d in $dirs) {
            $pyPackages += [pscustomobject]@{
                PackageFamilyName = $d.Name
                InstallLocation   = $d.FullName
            }
        }
    }
    if (-not $pyPackages) { return }

    $found = $false
    foreach ($pkg in $pyPackages) {
        $baseDirs = if ($pkg.InstallLocation) { @($pkg.InstallLocation) }
                    else { @((Join-Path $packagesDir $pkg.PackageFamilyName)) }
        foreach ($base in $baseDirs) {
            $candidates = @(
                Join-Path $base "LocalCache\Local\Programs\$Slug"
                Join-Path $base "LocalCache\Local\Programs\neuron5"
                Join-Path $base "LocalState\Programs\$Slug"
                Join-Path $base "LocalCache\Local\neuron5"
                Join-Path $base "LocalCache\Local\Programs\neuron"
            ) | Select-Object -Unique
            foreach ($shadow in $candidates) {
                if (Test-Path $shadow) {
                    $found = $true
                    Write-Host "  [!] Found a Store-Python-virtualized copy: $shadow" -ForegroundColor DarkYellow
                    GuaranteedRemove-Item -LiteralPath $shadow -Recurse
                    if (-not (Test-Path $shadow)) {
                        Write-Host "  [OK] Removed Store-Python shadow copy" -ForegroundColor Green
                    } else {
                        Write-Host "  [X] Could not fully remove $shadow — delete it by hand." -ForegroundColor Red
                    }
                }
            }
        }
    }
    if (-not $found) {
        Write-Host "  (No Store-Python shadow copy found.)" -ForegroundColor DarkGray
    }
}

# ---------------------------------------------------------------------------
# Install dir removal (with safety guard + lock retry)
# ---------------------------------------------------------------------------
function Remove-InstallDir {
    <#
    .SYNOPSIS
      Delete the Neuron install dir, but ONLY if the path matches the expected
      pattern (ends with \Programs\<slug>).  Guards against a misconfigured
      variable pointing Remove-Item somewhere dangerous.
    #>
    param([string]$Slug, [string]$InstallDir)
    $target = $InstallDir
    $safe = $target -and ($target.ToLower().TrimEnd('\').EndsWith('programs\' + $Slug.ToLower()))
    if (-not $safe) {
        Write-Host "  [X] Refusing to delete '$target' — it doesn't look like the Neuron install dir." -ForegroundColor Red
        return
    }
    if (-not (Test-Path $target)) {
        Write-Host "  (Install dir not present: $target)" -ForegroundColor DarkGray
        return
    }
    $ok = GuaranteedRemove-Item -LiteralPath $target -Recurse
    if (-not (Test-Path $target)) { Write-Host "  [OK] Removed $target" -ForegroundColor Green; return }

    # Still there — locked by a running process.  Offer to stop it.
    Write-Host "  [!] Files are locked — likely a running Neuron process." -ForegroundColor DarkYellow
    $procs = @()
    try {
        $procs = Get-Process -ErrorAction SilentlyContinue | Where-Object {
            try { $_.Path -and $_.Path.ToLower().StartsWith($target.ToLower()) }
            catch { $false }
        }
    } catch {}
    if ($procs.Count -gt 0) {
        Write-Host ("      Holding open: " + (($procs | ForEach-Object { "$($_.ProcessName)($($_.Id))" }) -join ", ")) -ForegroundColor DarkYellow
        Write-Host "  Stop these processes and retry removal manually." -ForegroundColor Yellow
    } else {
        Write-Host "      Lock not identified as a Neuron process — may be your AI app itself." -ForegroundColor DarkYellow
    }
    Write-Host "  [X] Could not fully remove. Delete manually: $target" -ForegroundColor Red
}

# ---------------------------------------------------------------------------
# Start Menu shortcut removal
# ---------------------------------------------------------------------------
function Remove-StartMenuShortcut {
    param([string]$Slug)
    $sd = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\$Slug"
    if (Test-Path $sd) {
        GuaranteedRemove-Item -LiteralPath $sd -Recurse
        Write-Host "  [OK] Removed Start Menu shortcut" -ForegroundColor Green
    }
}

# ---------------------------------------------------------------------------
# Temp-files cleanup (empty-stdin, etc.)
# ---------------------------------------------------------------------------
function Remove-TempFiles {
    <#
    .SYNOPSIS
      Clean up temporary files created by Neuron scripts during their
      lifetime (e.g. the empty-stdin file created by configuration.ps1's
      Get-NullDevicePath).
    #>
    $nullFile = "$env:TEMP\neuron5\empty-stdin"
    if (Test-Path $nullFile) { Remove-Item -LiteralPath $nullFile -Force -ErrorAction SilentlyContinue }
}

# ---------------------------------------------------------------------------
# Safe path removal with guard (legacy helper, kept for backward compat)
# ---------------------------------------------------------------------------
function Remove-Guarded {
    <#
    .SYNOPSIS
      Remove a path only if it looks like what we expect (guard against a
      collapsed path like "\Programs\neuron5" at a drive root when an env var
      was empty).  Used by uninstall.ps1 for paths that don't need the full
      Remove-InstallDir safety net.
    #>
    param([string]$path, [string]$mustContain, [string]$label)
    if (-not $path) { return }
    if (-not (Test-Path -LiteralPath $path)) { Write-Host "  - $label : not present" -ForegroundColor DarkGray; return }
    $full = (Resolve-Path -LiteralPath $path).Path
    if ($mustContain -and ($full -notmatch [regex]::Escape($mustContain))) {
        Write-Host "  [!] SKIP $label : '$full' doesn't look right (expected to contain '$mustContain')" -ForegroundColor Yellow
        return
    }
    GuaranteedRemove-Item -LiteralPath $full -Recurse
    if (Test-Path -LiteralPath $full) {
        Write-Host "  [X] $label : could not fully remove $full" -ForegroundColor Red
    } else {
        Write-Host "  [OK] removed $label : $full" -ForegroundColor Green
    }
}
