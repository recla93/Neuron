<#
  _neuron_paths.ps1 - single source of truth for every filesystem path the
  Neuron tooling touches on Windows. Dot-source it from install/uninstall/
  summary/deploy so they all agree on WHERE things live.

  Why this file exists: the running server resolves its memory store from
  src\neuron\server.py::_default_graphs_dir() to %LOCALAPPDATA%\<slug>\graphs,
  which is deliberately SEPARATE from the install dir (so reinstalling never
  wipes memory). The PowerShell scripts used to hardcode the install dir only,
  so the summary looked in the wrong place and uninstall orphaned all user data.
  Keep this resolution in lockstep with _default_graphs_dir().

  Identity/slug: v5 "Synapse" is "neuron5"; the classic v4 line is "neuron".
  Override with $env:NEURON_SLUG. Store dir can be overridden with $env:NS_GRAPHS_DIR
  (same env the server honors).
#>

# Write text as UTF-8 WITHOUT a BOM, on every PowerShell host.
#
# Why not `Set-Content -Encoding utf8NoBOM`: that token was added in
# PowerShell 7. Windows PowerShell 5.1 (the default host on every stock
# Windows box) rejects it with "Cannot bind parameter 'Encoding'. Cannot
# convert value 'utf8NoBOM' to type ...FileSystemCmdletProviderEncoding",
# which then bubbles up as "install dir not present" / silent JSON writes
# in the installer + uninstaller. `[IO.File]::WriteAllText` with a
# UTF8Encoding($false) argument produces the same bytes on both hosts.
#
# `$Content` accepts either a single string (already joined, e.g. a
# ConvertTo-Json result) or an array (each element becomes one line, plus a
# trailing newline — matches Set-Content's default array behavior, needed
# for .env scrubbing).
function Write-Utf8NoBom {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)]$Content
    )
    if ($Content -is [System.Array]) {
        $text = ($Content -join [Environment]::NewLine) + [Environment]::NewLine
    } else {
        $text = [string]$Content
    }
    $abs = if ([System.IO.Path]::IsPathRooted($Path)) { $Path } else { Join-Path (Get-Location).Path $Path }
    [System.IO.File]::WriteAllText($abs, $text, [System.Text.UTF8Encoding]::new($false))
}

function Get-LocalAppData {
    if ($env:LOCALAPPDATA) { return $env:LOCALAPPDATA }
    if ($env:USERPROFILE)  { return (Join-Path $env:USERPROFILE 'AppData\Local') }
    return (Join-Path $HOME 'AppData\Local')   # last-ditch fallback, never empty
}

# TRUE when a Python executable is the Microsoft Store build (or its zero-byte
# App-Execution-Alias stub under WindowsApps). The Store build runs in a
# per-package virtualized filesystem: venvs "created" under a normal folder get
# silently redirected into the package's own LocalCache, invisible to every
# other process - the root cause of "installed fine, then nothing can find its
# own folders" AND of uninstalls that miss the real files.
function Test-StorePython {
    param([string]$PyPath)
    if (-not $PyPath) { return $false }
    return ($PyPath -like '*\WindowsApps\*' -or $PyPath -like '*PythonSoftwareFoundation.Python*')
}

# Find a REAL (non-Store) Python on this machine. The py launcher is probed
# first because it never resolves to the Store alias; then every 'python' on
# PATH that is not under WindowsApps. Each candidate is actually EXECUTED
# (the Store alias stub prints a Store hint and exits non-zero, so it filters
# itself out). Returns the resolved sys.executable path, or $null.
function Get-RealPython {
    $cands = @()
    $pyl = Get-Command py -ErrorAction SilentlyContinue
    if ($pyl) {
        $exe = (& $pyl.Source -3 -c "import sys; print(sys.executable)" 2>$null)
        if ($LASTEXITCODE -eq 0 -and $exe) { $cands += ([string]$exe).Trim() }
    }
    foreach ($c in @(Get-Command python -All -ErrorAction SilentlyContinue)) {
        if (Test-StorePython $c.Source) { continue }   # don't even run the alias stub
        $exe = (& $c.Source -c "import sys; print(sys.executable)" 2>$null)
        if ($LASTEXITCODE -eq 0 -and $exe) { $cands += ([string]$exe).Trim() }
    }
    foreach ($p in $cands) {
        if ($p -and -not (Test-StorePython $p)) { return $p }
    }
    return $null
}

# TRUE when $Path lives under a OneDrive-managed folder. A Desktop/Documents
# redirected by OneDrive has an intermediate '\OneDrive[ - Org]\' segment
# (e.g. C:\Users\x\OneDrive\Desktop\...): the sync engine holds transient
# locks and cloud-only placeholders can fail reads/deletes, so callers use
# this to warn up front and to explain failures honestly instead of "OK".
function Test-OneDrivePath {
    param([string]$Path)
    if (-not $Path) { return $false }
    if ($Path -match '(?i)\\OneDrive( - [^\\]+)?\\') { return $true }
    foreach ($v in @($env:OneDrive, $env:OneDriveConsumer, $env:OneDriveCommercial)) {
        if ($v -and $Path.ToLower().StartsWith(($v.ToLower().TrimEnd('\') + '\'))) { return $true }
    }
    return $false
}

# Delete a directory and VERIFY it is actually gone. A plain
# `Remove-Item -Recurse -Force -ErrorAction SilentlyContinue` hides the two
# failure classes seen in the field:
#   - OneDrive-synced paths (redirected Desktop/Documents): sync locks,
#     ReadOnly/pinned attributes and cloud placeholders block deletion;
#   - locked venv files (an AI app still holding the Neuron server open).
# Strategy: clear blocking attributes, Remove-Item with retries + backoff,
# then cmd's `rd /s /q` (more tolerant of deep/locked trees), verifying after
# each pass. Returns $true only when the path no longer exists.
function Remove-DirRobust {
    param([Parameter(Mandatory)][string]$Path, [int]$Retries = 3)
    if (-not (Test-Path -LiteralPath $Path)) { return $true }
    try { & "$env:SystemRoot\System32\attrib.exe" -R -S -H "$Path" /S /D 2>$null | Out-Null } catch {}
    for ($i = 1; $i -le $Retries; $i++) {
        try { Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction Stop } catch {}
        if (-not (Test-Path -LiteralPath $Path)) { return $true }
        Start-Sleep -Milliseconds (300 * $i)
    }
    try { & "$env:SystemRoot\System32\cmd.exe" /d /c "rd /s /q `"$Path`"" 2>$null | Out-Null } catch {}
    return (-not (Test-Path -LiteralPath $Path))
}

# Same idea for a single file. Returns $true only when the file is gone.
function Remove-FileRobust {
    param([Parameter(Mandatory)][string]$Path, [int]$Retries = 3)
    if (-not (Test-Path -LiteralPath $Path)) { return $true }
    try { & "$env:SystemRoot\System32\attrib.exe" -R -S -H "$Path" 2>$null | Out-Null } catch {}
    for ($i = 1; $i -le $Retries; $i++) {
        try { Remove-Item -LiteralPath $Path -Force -ErrorAction Stop } catch {}
        if (-not (Test-Path -LiteralPath $Path)) { return $true }
        Start-Sleep -Milliseconds (250 * $i)
    }
    return (-not (Test-Path -LiteralPath $Path))
}

# Stop a running Neuron server before touching its venv/files, so deploy/uninstall
# don't fail on locked files (or half-write and corrupt). Matches processes running
# `-m neuron`, run_mcp.bat, or anything under the given install dir. Never touches $PID.
function Stop-NeuronServices {
    param([string]$InstallDir, [switch]$Yes)
    $pat = '(?i)(-m\s+neuron\b|\\run_mcp\.bat'
    if ($InstallDir) { $pat += '|' + [regex]::Escape($InstallDir) }
    $pat += ')'
    $procs = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and ($_.CommandLine -match $pat) -and $_.ProcessId -ne $PID })
    if ($procs.Count -eq 0) { return }
    Write-Host "   Found running Neuron process(es):" -ForegroundColor Yellow
    $procs | ForEach-Object { Write-Host ("     PID {0}: {1}" -f $_.ProcessId, $_.CommandLine) -ForegroundColor DarkGray }
    $stop = if ($Yes) { $true } else { (Read-Host "   Stop them first (avoids locked files)? [Y/n]") -notmatch '^\s*(n|no)\s*$' }
    if (-not $stop) { Write-Host "   Continuing without stopping - may fail on locked files." -ForegroundColor DarkYellow; return }
    foreach ($p in $procs) {
        try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop; Write-Host "     stopped PID $($p.ProcessId)" -ForegroundColor Green }
        catch { Write-Host "     could not stop PID $($p.ProcessId): $_" -ForegroundColor Red }
    }
    Start-Sleep -Seconds 1
}

function Get-NeuronPaths {
    param([string]$Slug)
    if (-not $Slug) { if ($env:NEURON_SLUG) { $Slug = $env:NEURON_SLUG } else { $Slug = 'neuron5' } }
    $local = Get-LocalAppData

    if ($env:NS_GRAPHS_DIR) { $store = $env:NS_GRAPHS_DIR }
    else { $store = Join-Path $local (Join-Path $Slug 'graphs') }

    # Model cache: fastembed / huggingface default locations (no cache_dir override
    # is set in-code, so these are the real spots the ~80MB model lands).
    # $env:TEMP / $HOME are never assumed non-empty: with the uninstaller's
    # $ErrorActionPreference='Stop', a Join-Path on an empty prefix would kill
    # the whole script at dot-source time on stripped/service environments.
    $tempDir = if ($env:TEMP) { $env:TEMP } else { Join-Path $local 'Temp' }
    $homeDir = if ($env:USERPROFILE) { $env:USERPROFILE } elseif ($HOME) { $HOME } else { $local }
    $caches = @(
        (Join-Path $tempDir 'fastembed_cache'),
        (Join-Path $homeDir '.cache\huggingface'),
        (Join-Path $local 'Temp\fastembed_cache')
    ) | Where-Object { $_ } | Select-Object -Unique

    return [ordered]@{
        Slug        = $Slug
        InstallDir  = Join-Path $local (Join-Path 'Programs' $Slug)
        StoreDir    = $store
        CrossLinks  = Join-Path $store '_cross_links.json'
        StartMenu   = Join-Path $env:APPDATA ("Microsoft\Windows\Start Menu\Programs\" + $Slug)
        ModelCaches = @($caches)
        # Every place 'Add Neuron to your AI' can register the server (mirror of
        # configuration.ps1::Get-RegistrationTargets). key path is app-specific.
        RegistrationTargets = @(
            @{ app='Claude Desktop'; path="$env:APPDATA\Claude\claude_desktop_config.json"; keys=@('mcpServers', $Slug) },
            @{ app='Claude Code';    path="$env:USERPROFILE\.claude.json";                   keys=@('mcpServers', $Slug) },
            @{ app='Cursor';         path="$env:USERPROFILE\.cursor\mcp.json";               keys=@('mcpServers', $Slug) },
            @{ app='VS Code';        path="$env:APPDATA\Code\User\settings.json";            keys=@('mcp','servers', $Slug) },
            @{ app='OpenCode';       path="$env:USERPROFILE\.config\opencode\opencode.json"; keys=@('mcp', $Slug) },
            @{ app='Zed';            path="$env:APPDATA\Zed\settings.json";                  keys=@('context_servers', $Slug) },
            @{ app='Codex CLI';       path="$env:USERPROFILE\.codex\config.toml";              keys=@('mcp_servers', $Slug); format='toml' },
            @{ app='Codex CLI hooks'; path="$env:USERPROFILE\.codex\hooks.json";               keys=@('hooks', 'SessionStart'); format='hooks-json' }
        )
    }
}
