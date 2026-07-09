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
    $caches = @(
        (Join-Path $env:TEMP 'fastembed_cache'),
        (Join-Path $HOME '.cache\huggingface'),
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
            @{ app='Zed';            path="$env:APPDATA\Zed\settings.json";                  keys=@('context_servers', $Slug) }
        )
    }
}
