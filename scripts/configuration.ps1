<#
.SYNOPSIS
    Neuron - Configuration Center (interactive, arrow-key menu).
.DESCRIPTION
    One place to fully set up and drive Neuron on Windows:

      1. Check my system            (scripts\check.ps1)
      2. Install prerequisites      (Python + venv + pip/uv)   <- BEFORE Turso
      3. Install PyTurso engine     (vendored win_amd64 wheel, no compiler)
      4. Install full Neuron        (neuron wheel + verify)
      5. Add Neuron to your AI      (writes the MCP config for your app)
      6. Bridge & Cloud Turso       (connect_turso.py / bridge.py / cloud check)
      7. Run the test suite         (scripts\run_tests.ps1)
      8. Live Log Console           (scripts\neuron_console.py --watch)

    Steps are ordered by execution: installing PyTurso and the deps from the
    PRE-BUILT wheels FIRST is what avoids the classic Windows failure where pip
    tries to compile pyturso from Rust source and appears to hang forever.

    Launched by Configuration.bat. Safe to run repeatedly (idempotent).
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\configuration.ps1
#>

# Self-reinvoke with ExecutionPolicy Bypass, using the CURRENT PowerShell host so
# it works under both Windows PowerShell (powershell.exe) AND PowerShell 7 (pwsh);
# boxes with only pwsh have no `powershell` on PATH.
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

# ---------------------------------------------------------------------------
# UTF-8 everywhere. Several of the Python helpers print Unicode glyphs
# (-> checkmarks, arrows, warning signs). On a default Windows console
# (cp1252/cp850) that raises UnicodeEncodeError and the script dies mid-run.
# Forcing the console to UTF-8 AND telling Python to encode its stdio as UTF-8
# makes every helper we launch render correctly instead of crashing.
# ---------------------------------------------------------------------------
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
try { $OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

# ---------------------------------------------------------------------------
# Paths (single source of truth)
# ---------------------------------------------------------------------------
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path   # ...\scripts
$Repo       = Split-Path -Parent $ScriptDir                     # repo root
$Vendor     = Join-Path $Repo "vendor"                          # prebuilt pyturso wheels
$InstallDir = "$env:LOCALAPPDATA\Programs\neuron"               # deployed MCP server
$InstallVenvPy = "$InstallDir\.venv\Scripts\python.exe"
$RepoVenvPy    = "$Repo\.venv\Scripts\python.exe"
$PyTursoPin = "pyturso==0.6.1"

# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
function Test-Cmd($n) { [bool](Get-Command $n -ErrorAction SilentlyContinue) }

function Pause-Any {
    Write-Host ""
    Write-Host "  Press any key to return to the menu..." -ForegroundColor DarkGray
    try { [void][Console]::ReadKey($true) } catch { Read-Host | Out-Null }
}

function Show-Banner {
    Write-Host ""
    Write-Host '   _  _ ___ _   _ ___  ___  _  _ ' -ForegroundColor Cyan
    Write-Host '  | \| | __| | | | _ \/ _ \| \| |' -ForegroundColor Cyan
    Write-Host '  | .  | _|| |_| |   / (_) | .  |' -ForegroundColor Cyan
    Write-Host '  |_|\_|___|\___/|_|_\\___/|_|\_|' -ForegroundColor Cyan
    Write-Host '  Configuration Center  -  semantic memory for your AI' -ForegroundColor DarkCyan
    Write-Host '  ---------------------------------------------------------------' -ForegroundColor DarkGray
}

# Arrow-key menu. Returns the selected 0-based index, or -1 for Esc/back.
# Falls back to a numbered prompt when the console can't do live key reads
# (e.g. input redirected) so it never hard-crashes.
function Show-Menu {
    param(
        [string]$Title,
        [string[]]$Options,
        [string[]]$Descriptions = @()
    )

    $redirected = $false
    try { $redirected = [Console]::IsInputRedirected } catch { $redirected = $true }

    if ($redirected) {
        Clear-Host; Show-Banner
        Write-Host "`n  $Title`n" -ForegroundColor Cyan
        for ($i = 0; $i -lt $Options.Count; $i++) { Write-Host ("   {0}) {1}" -f $i, $Options[$i]) }
        $raw = Read-Host "`n  Choose a number (blank = back)"
        if ($raw -match '^\d+$' -and [int]$raw -lt $Options.Count) { return [int]$raw }
        return -1
    }

    $sel = 0
    try { [Console]::CursorVisible = $false } catch {}
    try {
        while ($true) {
            Clear-Host; Show-Banner
            Write-Host ""
            Write-Host "  $Title" -ForegroundColor Cyan
            Write-Host "  Up/Down to move  -  Enter to choose  -  Esc to go back`n" -ForegroundColor DarkGray
            for ($i = 0; $i -lt $Options.Count; $i++) {
                if ($i -eq $sel) {
                    Write-Host "   > " -NoNewline -ForegroundColor Green
                    Write-Host (" {0} " -f $Options[$i]) -ForegroundColor Black -BackgroundColor Green
                } else {
                    Write-Host "     $($Options[$i])" -ForegroundColor Gray
                }
            }
            if ($Descriptions.Count -gt $sel -and $Descriptions[$sel]) {
                Write-Host ""
                Write-Host "   $($Descriptions[$sel])" -ForegroundColor DarkCyan
            }
            $k = [Console]::ReadKey($true)
            switch ($k.Key) {
                'UpArrow'   { $sel = ($sel - 1 + $Options.Count) % $Options.Count }
                'DownArrow' { $sel = ($sel + 1) % $Options.Count }
                'Home'      { $sel = 0 }
                'End'       { $sel = $Options.Count - 1 }
                'Enter'     { return $sel }
                'Escape'    { return -1 }
            }
        }
    } finally {
        try { [Console]::CursorVisible = $true } catch {}
    }
}

# Yes/No prompt built on the arrow menu (default No).
function Confirm-YesNo {
    param([string]$Question)
    Clear-Host; Show-Banner
    Write-Host "`n  $Question" -ForegroundColor Yellow
    $ans = Read-Host "  [y/N]"
    return ($ans -match '^(y|yes|s|si)$')
}

# Pick the best Python we have for running .py helpers (install > repo > PATH).
function Get-RunnerPython {
    if (Test-Path $InstallVenvPy) { return $InstallVenvPy }
    if (Test-Path $RepoVenvPy)    { return $RepoVenvPy }
    $p = Get-Command python -ErrorAction SilentlyContinue
    if ($p) { return $p.Source }
    return $null
}

# Is Neuron actually importable by this interpreter? (Several helpers import
# `neuron` at module top and crash hard if it isn't installed yet.)
function Test-NeuronReady {
    param([string]$py)
    if (-not $py -or -not (Test-Path $py)) { return $false }
    & $py -c "import neuron" 2>$null
    return ($LASTEXITCODE -eq 0)
}

# Show a friendly "install first" panel instead of letting a helper crash.
function Show-NotInstalled {
    param([string]$What)
    Write-Host "  [!] $What needs Neuron installed, but it isn't importable yet." -ForegroundColor DarkYellow
    Write-Host "      Run these first (menu items):" -ForegroundColor DarkYellow
    Write-Host "        2) Install prerequisites   3) Install PyTurso   4) Install full Neuron" -ForegroundColor DarkYellow
    Write-Host "      (or the 'Install EVERYTHING' shortcut)." -ForegroundColor DarkYellow
}

# ---------------------------------------------------------------------------
# Install stages (each thin, wheel-based, idempotent)
# ---------------------------------------------------------------------------

# Return "cp313" style tag for the base interpreter, or $null.
function Get-CpTag($py) {
    $t = & $py -c "import sys;print('cp%d%d'%(sys.version_info.major,sys.version_info.minor))" 2>$null
    if ($LASTEXITCODE -eq 0) { return $t.Trim() }
    return $null
}

function Test-VendoredWheel($cpTag) {
    if (-not (Test-Path $Vendor)) { return $false }
    if (-not $cpTag) { return $false }
    return [bool](Get-ChildItem $Vendor -Filter "pyturso-*$cpTag*win_amd64.whl" -ErrorAction SilentlyContinue | Select-Object -First 1)
}

# Stage 1 -------------------------------------------------------------------
function Invoke-Prereqs {
    Clear-Host; Show-Banner
    Write-Host "`n  [1] Install prerequisites (Python + venv)`n" -ForegroundColor Yellow

    $py = Get-Command python -ErrorAction SilentlyContinue
    if (-not $py) {
        Write-Host "  [X] Python not found on PATH." -ForegroundColor Red
        Write-Host "      Install Python 3.10-3.14 from https://python.org (tick 'Add to PATH')," -ForegroundColor Red
        Write-Host "      then re-run this step." -ForegroundColor Red
        Pause-Any; return
    }
    $base = $py.Source
    $ver = (& $base -c "import sys;print('%d.%d'%sys.version_info[:2])").Trim()
    Write-Host "  [OK] Python $ver  ($base)" -ForegroundColor Green

    $cp = Get-CpTag $base
    if (Test-VendoredWheel $cp) {
        Write-Host "  [OK] A prebuilt PyTurso wheel for $cp is bundled (no compiler needed)." -ForegroundColor Green
    } else {
        Write-Host "  [!] No bundled PyTurso wheel for $cp in vendor\." -ForegroundColor DarkYellow
        $have = (Get-ChildItem $Vendor -Filter "pyturso-*.whl" -ErrorAction SilentlyContinue | ForEach-Object { ($_.Name -split '-')[2] }) -join ", "
        Write-Host "      Bundled wheels cover: $have" -ForegroundColor DarkYellow
        Write-Host "      With this Python, PyTurso would COMPILE from Rust source (slow / may hang)." -ForegroundColor DarkYellow
        Write-Host "      Easiest fix: install Python 3.13 or 3.14 and re-run this step." -ForegroundColor DarkYellow
    }

    Write-Host "`n  Creating the install venv at $InstallDir\.venv ..." -ForegroundColor Yellow
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
    $venv = "$InstallDir\.venv"
    if (-not (Test-Path "$venv\Scripts\python.exe")) {
        & $base -m venv $venv
    }
    if (-not (Test-Path "$venv\Scripts\python.exe")) {
        Write-Host "  [X] venv creation failed." -ForegroundColor Red; Pause-Any; return
    }
    $vpy = "$venv\Scripts\python.exe"
    Write-Host "  Upgrading pip ..." -ForegroundColor Yellow
    & $vpy -m pip install --upgrade pip
    Write-Host "`n  [OK] Prerequisites ready. Next: install PyTurso (menu item 3)." -ForegroundColor Green
    Pause-Any
}

# Stage 2 -------------------------------------------------------------------
function Invoke-PyTurso {
    Clear-Host; Show-Banner
    Write-Host "`n  [2] Install the PyTurso database engine (prebuilt wheel)`n" -ForegroundColor Yellow

    if (-not (Test-Path $InstallVenvPy)) {
        Write-Host "  [!] No install venv yet. Run 'Install prerequisites' first." -ForegroundColor DarkYellow
        Pause-Any; return
    }
    $cp = Get-CpTag $InstallVenvPy
    $pipArgs = @("-m", "pip", "install", $PyTursoPin)
    if (Test-Path $Vendor) { $pipArgs += @("--find-links", $Vendor) }

    if (Test-VendoredWheel $cp) {
        Write-Host "  Using the bundled $cp win_amd64 wheel (no Rust/MSVC compile)." -ForegroundColor Green
    } else {
        Write-Host "  [!] No bundled wheel for $cp - pip may try to COMPILE PyTurso and hang." -ForegroundColor Red
        if (-not (Confirm-YesNo "Continue anyway? (recommended: switch to Python 3.13/3.14 instead)")) {
            return
        }
    }
    Write-Host ""
    & $InstallVenvPy @pipArgs
    if ($LASTEXITCODE -eq 0) {
        & $InstallVenvPy -c "import turso; print('  [OK] PyTurso imports correctly')"
    } else {
        Write-Host "  [X] PyTurso install failed. See INSTALL.md > Troubleshooting." -ForegroundColor Red
    }
    Pause-Any
}

# Stage 3 -------------------------------------------------------------------
function Invoke-Neuron {
    Clear-Host; Show-Banner
    Write-Host "`n  [3] Install the full Neuron package`n" -ForegroundColor Yellow

    if (-not (Test-Path $InstallVenvPy)) {
        Write-Host "  [!] No install venv yet. Run 'Install prerequisites' first." -ForegroundColor DarkYellow
        Pause-Any; return
    }

    # Prefer a built wheel (dist\ or repo root); fall back to installing the source tree.
    $wheel = Get-ChildItem -Path $Repo, "$Repo\dist" -Filter "neuron-*.whl" -ErrorAction SilentlyContinue |
             Sort-Object LastWriteTime -Descending | Select-Object -First 1
    $target = if ($wheel) { $wheel.FullName } else { $Repo }
    if ($wheel) { Write-Host "  Wheel: $($wheel.Name)" -ForegroundColor Gray }
    else        { Write-Host "  No wheel found - installing from source tree ($Repo)." -ForegroundColor DarkYellow }

    $pipArgs = @("-m", "pip", "install", $target)
    if (Test-Path $Vendor) { $pipArgs += @("--find-links", $Vendor) }
    Write-Host ""
    & $InstallVenvPy @pipArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [X] Neuron install failed. See INSTALL.md > Troubleshooting." -ForegroundColor Red
        Pause-Any; return
    }

    Write-Host "`n  Verifying imports ..." -ForegroundColor Yellow
    & $InstallVenvPy -c "import turso; print('  [OK] pyturso')"
    & $InstallVenvPy -c "from fastembed import TextEmbedding; print('  [OK] fastembed')"
    & $InstallVenvPy -c "import mcp; print('  [OK] mcp')"
    & $InstallVenvPy -c "import neuron; print('  [OK] neuron', neuron.__version__)"
    Write-Host "`n  Neuron installed into $InstallDir" -ForegroundColor Green
    Write-Host "  Next: 'Add Neuron to your AI' (menu item 5) to wire it into your app." -ForegroundColor Green
    Pause-Any
}

# All-in-one (2 -> 4) -------------------------------------------------------
function Invoke-InstallEverything {
    Invoke-Prereqs
    Invoke-PyTurso
    Invoke-Neuron
}

# ---------------------------------------------------------------------------
# Check / tests / console (delegate to existing scripts)
# ---------------------------------------------------------------------------
function Invoke-Check {
    Clear-Host; Show-Banner
    Write-Host "`n  Running the system check ...`n" -ForegroundColor Yellow
    & "$ScriptDir\check.ps1"
    if ($LASTEXITCODE -ne 0) {
        if (Confirm-YesNo "Issues found. Try to auto-repair them now?") {
            & "$ScriptDir\check.ps1" -Repair
        }
    }
    Pause-Any
}

function Invoke-Tests {
    Clear-Host; Show-Banner
    $idx = Show-Menu -Title "Run the test suite" -Options @(
        "Core tests only (fast, no model download)",
        "Full suite (downloads the ~80MB embedding model on first run)",
        "Back"
    )
    if ($idx -eq 0) { & "$ScriptDir\run_tests.ps1" -Core }
    elseif ($idx -eq 1) { & "$ScriptDir\run_tests.ps1" }
    else { return }
    Pause-Any
}

function Invoke-Console {
    Clear-Host; Show-Banner
    Write-Host "`n  Live Log Console - press Ctrl+C to stop and return to the menu.`n" -ForegroundColor Yellow
    $py = Get-RunnerPython
    if (-not $py) { Write-Host "  [X] No Python available." -ForegroundColor Red; Pause-Any; return }
    if (-not (Test-NeuronReady $py)) { Show-NotInstalled "The Live Log Console"; Pause-Any; return }
    Push-Location $Repo
    try { & $py "$ScriptDir\neuron_console.py" --watch=3 } catch {} finally { Pop-Location }
    Pause-Any
}

# ---------------------------------------------------------------------------
# Bridge & Cloud Turso submenu
# ---------------------------------------------------------------------------
function Invoke-TursoConnect {
    Clear-Host; Show-Banner
    Write-Host "`n  Connect a Turso Cloud database`n" -ForegroundColor Yellow
    $py = Get-RunnerPython
    if (-not $py) { Write-Host "  [X] No Python available." -ForegroundColor Red; Pause-Any; return }

    # connect_turso.py needs the [cloud] extra (libsql-client).
    & $py -c "import libsql_client" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  Installing the cloud extra (libsql-client) first ..." -ForegroundColor Yellow
        & $py -m pip install "libsql-client>=0.3.1"
    }
    Write-Host "  You'll be asked for the database URL and auth token (token entry is hidden)." -ForegroundColor Gray
    Write-Host "  Nothing is written unless a real read+write probe succeeds.`n" -ForegroundColor Gray
    Push-Location $Repo
    try { & $py "$ScriptDir\connect_turso.py" } catch {} finally { Pop-Location }
    Pause-Any
}

function Invoke-CloudCheck {
    Clear-Host; Show-Banner
    Write-Host "`n  Offline cloud-config sanity check (never connects) ...`n" -ForegroundColor Yellow
    $py = Get-RunnerPython
    if (-not $py) { Write-Host "  [X] No Python available." -ForegroundColor Red; Pause-Any; return }
    Push-Location $Repo
    try { & $py "$ScriptDir\check_cloud_config.py" } catch {} finally { Pop-Location }
    Pause-Any
}

function Invoke-Bridge {
    Clear-Host; Show-Banner
    Write-Host "`n  Launch the Neuron -> HTTP bridge (for ChatGPT & remote connectors)`n" -ForegroundColor Yellow
    $py = Get-RunnerPython
    if (-not $py) { Write-Host "  [X] No Python available." -ForegroundColor Red; Pause-Any; return }
    if (-not (Test-NeuronReady $py)) { Show-NotInstalled "The HTTP bridge"; Pause-Any; return }
    Write-Host "  This serves Neuron over http://127.0.0.1:8000/sse ." -ForegroundColor Gray
    Write-Host "  To reach it from ChatGPT you still need a public HTTPS tunnel, e.g.:" -ForegroundColor Gray
    Write-Host "     cloudflared tunnel --url http://127.0.0.1:8000" -ForegroundColor Gray
    Write-Host "  Then add the https://.../sse URL as a connector. Press Ctrl+C to stop.`n" -ForegroundColor Gray
    Push-Location $Repo
    try { & $py "$ScriptDir\bridge.py" } catch {} finally { Pop-Location }
    Pause-Any
}

function Show-BridgeCloudMenu {
    while ($true) {
        $idx = Show-Menu -Title "Bridge & Cloud Turso" -Options @(
            "Connect a Turso Cloud database (connect + test + save to .env)",
            "Check cloud config (offline, never connects)",
            "Launch the HTTP bridge (ChatGPT / remote connectors)",
            "Back"
        ) -Descriptions @(
            "Join a shared Turso Cloud DB so memory survives across machines.",
            "Verify TURSO_* values in .env are well-formed without dialing out.",
            "Expose the local stdio server over HTTP for clients that can't run stdio.",
            ""
        )
        switch ($idx) {
            0 { Invoke-TursoConnect }
            1 { Invoke-CloudCheck }
            2 { Invoke-Bridge }
            default { return }
        }
    }
}

# ---------------------------------------------------------------------------
# "Add Neuron to your AI" - JSON config writer, per client
# ---------------------------------------------------------------------------
function Get-ConfigPython {
    # The command the AI app should launch. Prefer the installed venv; fall back
    # to the repo venv; else warn (they should install first).
    if (Test-Path $InstallVenvPy) { return $InstallVenvPy }
    if (Test-Path $RepoVenvPy)    { return $RepoVenvPy }
    return $InstallVenvPy   # not there yet; still write it - install fills it in
}

function Load-Json {
    param([string]$path)
    if (Test-Path $path) {
        $raw = Get-Content $path -Raw -ErrorAction SilentlyContinue
        if ($raw -and $raw.Trim()) {
            try { return ($raw | ConvertFrom-Json) }
            catch {
                Copy-Item $path "$path.neuron-bak" -Force -ErrorAction SilentlyContinue
                Write-Host "  [!] Existing file wasn't valid JSON; backed it up to:" -ForegroundColor DarkYellow
                Write-Host "      $path.neuron-bak" -ForegroundColor DarkYellow
            }
        }
    }
    return (New-Object psobject)
}

function Set-Prop {
    param([object]$obj, [string]$name, [object]$value)
    if ($obj.PSObject.Properties[$name]) { $obj.$name = $value }
    else { $obj | Add-Member -NotePropertyName $name -NotePropertyValue $value }
}

# Ensure obj.<name> exists as an object and return it.
function Get-OrAddObject {
    param([object]$obj, [string]$name)
    if (-not $obj.PSObject.Properties[$name] -or $null -eq $obj.$name) {
        Set-Prop $obj $name (New-Object psobject)
    }
    return $obj.$name
}

function Save-Json {
    param([object]$obj, [string]$path)
    $dir = Split-Path -Parent $path
    if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    if (Test-Path $path) { Copy-Item $path "$path.neuron-bak" -Force -ErrorAction SilentlyContinue }
    $obj | ConvertTo-Json -Depth 20 | Set-Content -Path $path -Encoding UTF8
    Write-Host "  [OK] Wrote $path" -ForegroundColor Green
    Write-Host "       (previous version, if any, saved as *.neuron-bak)" -ForegroundColor DarkGray
}

function Write-ClientConfig {
    param([string]$App)
    $vpy = Get-ConfigPython
    if (-not (Test-Path $vpy)) {
        Write-Host "  [!] Neuron isn't installed yet, so the launch path" -ForegroundColor DarkYellow
        Write-Host "      $vpy" -ForegroundColor DarkYellow
        Write-Host "      doesn't exist. I'll still write the config - just run" -ForegroundColor DarkYellow
        Write-Host "      'Install full Neuron' before starting your app." -ForegroundColor DarkYellow
    }
    $nargs = @('-m', 'neuron')

    switch ($App) {
        'claude-desktop' {
            $path = "$env:APPDATA\Claude\claude_desktop_config.json"
            $cfg = Load-Json $path
            $servers = Get-OrAddObject $cfg 'mcpServers'
            Set-Prop $servers 'neuron' ([pscustomobject]@{ command = $vpy; args = $nargs })
            Save-Json $cfg $path
            Write-Host "  -> Fully restart Claude Desktop (quit from the tray) to load Neuron." -ForegroundColor Cyan
        }
        'claude-code' {
            $path = "$env:USERPROFILE\.claude.json"
            $cfg = Load-Json $path
            $servers = Get-OrAddObject $cfg 'mcpServers'
            Set-Prop $servers 'neuron' ([pscustomobject]@{ command = $vpy; args = $nargs; cwd = $InstallDir })
            Save-Json $cfg $path
            Write-Host "  -> Restart Claude Code. (Per-project alternative: a .mcp.json in the repo.)" -ForegroundColor Cyan
        }
        'cursor' {
            $path = "$env:USERPROFILE\.cursor\mcp.json"
            $cfg = Load-Json $path
            $servers = Get-OrAddObject $cfg 'mcpServers'
            Set-Prop $servers 'neuron' ([pscustomobject]@{ command = $vpy; args = $nargs })
            Save-Json $cfg $path
            Write-Host "  -> Restart Cursor; enable 'neuron' under Settings > MCP if prompted." -ForegroundColor Cyan
        }
        'vscode' {
            $path = "$env:APPDATA\Code\User\settings.json"
            $cfg = Load-Json $path
            $mcp = Get-OrAddObject $cfg 'mcp'
            $servers = Get-OrAddObject $mcp 'servers'
            Set-Prop $servers 'neuron' ([pscustomobject]@{ type = 'stdio'; command = $vpy; args = $nargs })
            Save-Json $cfg $path
            Write-Host "  -> Restart VS Code. Needs an MCP-capable client (Copilot Agent mode / Continue)." -ForegroundColor Cyan
        }
        'opencode' {
            $path = "$env:USERPROFILE\.config\opencode\opencode.json"
            $cfg = Load-Json $path
            $mcp = Get-OrAddObject $cfg 'mcp'
            Set-Prop $mcp 'neuron' ([pscustomobject]@{ command = @($vpy, '-m', 'neuron'); type = 'local' })
            Save-Json $cfg $path
            Write-Host "  -> Restart OpenCode." -ForegroundColor Cyan
        }
        'zed' {
            $path = "$env:APPDATA\Zed\settings.json"
            $cfg = Load-Json $path
            $cs = Get-OrAddObject $cfg 'context_servers'
            Set-Prop $cs 'neuron' ([pscustomobject]@{ command = [pscustomobject]@{ path = $vpy; args = $nargs } })
            Save-Json $cfg $path
            Write-Host "  -> Restart Zed." -ForegroundColor Cyan
        }
    }
}

function Invoke-AddToAI {
    while ($true) {
        $idx = Show-Menu -Title "Add Neuron to your AI - which app do you use?" -Options @(
            "Claude Desktop",
            "Claude Code (CLI)",
            "Cursor",
            "VS Code (Copilot / Continue)",
            "OpenCode",
            "Zed",
            "ChatGPT or another remote connector",
            "Back"
        ) -Descriptions @(
            "Local MCP app. No API key needed - Neuron runs on your machine.",
            "Anthropic's terminal agent. No API key needed for Neuron itself.",
            "Local MCP app. No API key needed - Neuron runs on your machine.",
            "Local MCP app. No API key needed - Neuron runs on your machine.",
            "Local MCP app. No API key needed - Neuron runs on your machine.",
            "Local MCP app. No API key needed - Neuron runs on your machine.",
            "Can't run local stdio - needs the HTTP bridge + a public HTTPS URL.",
            ""
        )

        $map = @{ 0='claude-desktop'; 1='claude-code'; 2='cursor'; 3='vscode'; 4='opencode'; 5='zed' }

        if ($idx -ge 0 -and $idx -le 5) {
            Clear-Host; Show-Banner
            Write-Host "`n  Configuring: $($map[$idx])`n" -ForegroundColor Yellow
            Write-ClientConfig -App $map[$idx]
            Write-Host "`n  Note: Neuron is a LOCAL server - it does not need any API key." -ForegroundColor Green
            Maybe-StoreLlmKey
            Pause-Any
        }
        elseif ($idx -eq 6) {
            Show-RemoteHelp
        }
        else { return }
    }
}

function Show-RemoteHelp {
    Clear-Host; Show-Banner
    Write-Host "`n  ChatGPT / remote connectors`n" -ForegroundColor Yellow
    Write-Host "  These clients can't launch a local stdio server, so Neuron is exposed"
    Write-Host "  over HTTP by the bridge, then published via a public HTTPS tunnel:"
    Write-Host ""
    Write-Host "    1. Menu > 'Bridge & Cloud Turso' > 'Launch the HTTP bridge'" -ForegroundColor Cyan
    Write-Host "    2. In another terminal:  cloudflared tunnel --url http://127.0.0.1:8000" -ForegroundColor Cyan
    Write-Host "    3. Add the resulting  https://.../sse  URL as a connector" -ForegroundColor Cyan
    Write-Host "       (ChatGPT: Settings > Connectors / Developer mode)." -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  The connector uses that URL (and ChatGPT's own login) - you do NOT paste"
    Write-Host "  a raw API key into Neuron. See docs\BRIDGE.md for the full walkthrough."
    if (Confirm-YesNo "Launch the bridge now?") { Invoke-Bridge }
}

# Optional: store an LLM provider key for Neuron's standalone chat / extract tool.
function Maybe-StoreLlmKey {
    Write-Host ""
    if (-not (Confirm-YesNo "Optional: store an LLM API key for Neuron's standalone chat / 'extract' tool? (not required for MCP)")) {
        return
    }
    $idx = Show-Menu -Title "Which provider's key?" -Options @(
        "OpenAI", "Anthropic", "Google Gemini", "Ollama (local, no key)", "Cancel"
    )
    if ($idx -lt 0 -or $idx -eq 4) { return }
    if ($idx -eq 3) {
        Update-EnvFile @{ 'NS_LLM_ENDPOINT' = 'http://localhost:11434/api/generate'; 'NS_LLM_MODEL' = 'qwen2.5:3b' }
        Write-Host "  [OK] Configured Neuron to use a local Ollama endpoint (no key stored)." -ForegroundColor Green
        return
    }
    $providerEnv = @{ 0 = 'OPENAI_API_KEY'; 1 = 'ANTHROPIC_API_KEY'; 2 = 'GEMINI_API_KEY' }[$idx]
    $sec = Read-Host "  Paste the API key (hidden)" -AsSecureString
    $key = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
             [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
    if (-not $key) { Write-Host "  Nothing entered - skipped." -ForegroundColor DarkYellow; return }
    Update-EnvFile @{ $providerEnv = $key; 'NS_LLM_API_KEY' = $key }
    Write-Host "  [OK] Saved to .env (gitignored). Keep that file private." -ForegroundColor Green
}

# Update key=value lines in the repo .env in place, appending missing ones.
function Update-EnvFile {
    param([hashtable]$Values)
    $path = Join-Path $Repo ".env"
    $lines = @()
    if (Test-Path $path) { $lines = Get-Content $path }
    $remaining = @{}; foreach ($k in $Values.Keys) { $remaining[$k] = $Values[$k] }
    $out = @()
    foreach ($line in $lines) {
        $trim = $line.TrimStart()
        $key = if ($trim -match '^([^#=]+)=') { $Matches[1].Trim() } else { '' }
        if ($key -and $remaining.ContainsKey($key)) {
            $out += "$key=$($remaining[$key])"; $remaining.Remove($key)
        } else { $out += $line }
    }
    foreach ($k in $remaining.Keys) { $out += "$k=$($remaining[$k])" }
    Set-Content -Path $path -Value $out -Encoding UTF8
}

# ---------------------------------------------------------------------------
# Clean install / Uninstall
# ---------------------------------------------------------------------------
function Get-Child { param([object]$obj, [string]$name)
    if ($obj -and $obj.PSObject.Properties[$name]) { return $obj.$name }
    return $null
}
function Remove-Prop { param([object]$obj, [string]$name)
    if ($obj -and $obj.PSObject.Properties[$name]) { $obj.PSObject.Properties.Remove($name) }
}

# The exact places 'Add Neuron to your AI' can write, and where the neuron entry
# lives in each. Used to cleanly de-register on uninstall.
function Get-RegistrationTargets {
    return @(
        @{ app='Claude Desktop'; path="$env:APPDATA\Claude\claude_desktop_config.json";       keys=@('mcpServers','neuron') },
        @{ app='Claude Code';    path="$env:USERPROFILE\.claude.json";                          keys=@('mcpServers','neuron') },
        @{ app='Cursor';         path="$env:USERPROFILE\.cursor\mcp.json";                      keys=@('mcpServers','neuron') },
        @{ app='VS Code';        path="$env:APPDATA\Code\User\settings.json";                   keys=@('mcp','servers','neuron') },
        @{ app='OpenCode';       path="$env:USERPROFILE\.config\opencode\opencode.json";        keys=@('mcp','neuron') },
        @{ app='Zed';            path="$env:APPDATA\Zed\settings.json";                         keys=@('context_servers','neuron') }
    )
}

function Remove-McpRegistrations {
    $removed = 0
    foreach ($t in (Get-RegistrationTargets)) {
        if (-not (Test-Path $t.path)) { continue }
        $cfg = Load-Json $t.path
        $parent = $cfg
        for ($i = 0; $i -lt $t.keys.Count - 1; $i++) { $parent = Get-Child $parent $t.keys[$i]; if (-not $parent) { break } }
        $leaf = $t.keys[$t.keys.Count - 1]
        if ($parent -and $parent.PSObject.Properties[$leaf]) {
            Remove-Prop $parent $leaf
            Save-Json $cfg $t.path
            Write-Host "  [OK] Removed 'neuron' from $($t.app)" -ForegroundColor Green
            $removed++
        }
    }
    if ($removed -eq 0) { Write-Host "  (No AI app had a 'neuron' entry to remove.)" -ForegroundColor DarkGray }
}

# Delete the install dir - but ONLY if the path really is the Neuron install
# location, so a misconfigured var can never point Remove-Item somewhere unsafe.
function Remove-InstallDir {
    $target = $InstallDir
    $safe = $target -and ($target.ToLower().TrimEnd('\').EndsWith('programs\neuron'))
    if (-not $safe) {
        Write-Host "  [X] Refusing to delete '$target' - it doesn't look like the Neuron install dir." -ForegroundColor Red
        return
    }
    if (-not (Test-Path $target)) {
        Write-Host "  (Install dir not present: $target)" -ForegroundColor DarkGray
        return
    }
    try {
        Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction Stop
        Write-Host "  [OK] Removed $target" -ForegroundColor Green
    } catch {
        Write-Host "  [X] Could not remove $target : $_" -ForegroundColor Red
        Write-Host "      Close any app still using Neuron (Claude, Cursor, a running server) and retry." -ForegroundColor DarkYellow
    }
}

function Remove-StartMenuShortcut {
    $sd = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Neuron"
    if (Test-Path $sd) {
        Remove-Item -LiteralPath $sd -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "  [OK] Removed Start Menu shortcut" -ForegroundColor Green
    }
}

function Invoke-CleanUninstall {
    Clear-Host; Show-Banner
    Write-Host "`n  Clean install / Uninstall Neuron`n" -ForegroundColor Yellow
    Write-Host "  This removes the installed Neuron server so you can start from scratch."
    Write-Host "  It will:" -ForegroundColor Gray
    Write-Host "    - delete the install venv:  $InstallDir" -ForegroundColor Gray
    Write-Host "    - remove the Start Menu shortcut" -ForegroundColor Gray
    Write-Host "    - (optionally) de-register 'neuron' from your AI apps" -ForegroundColor Gray
    Write-Host "  It will NOT touch this source repo, and by default KEEPS your memory data." -ForegroundColor Gray
    Write-Host ""
    Write-Host "  Note: your Turso CLOUD database (if any) is never deleted from here." -ForegroundColor DarkGray

    if (-not (Confirm-YesNo "Proceed with uninstall?")) {
        Write-Host "  Cancelled - nothing was changed." -ForegroundColor DarkYellow
        Pause-Any; return
    }

    Write-Host ""
    $deReg = Confirm-YesNo "Also remove Neuron from your AI apps' config (recommended)?"
    $wipeData = Confirm-YesNo "Also DELETE local memory data in this repo (graphs\*.db)? This is irreversible."

    Write-Host "`n  Uninstalling..." -ForegroundColor Yellow
    if ($deReg) { Remove-McpRegistrations }
    Remove-StartMenuShortcut
    Remove-InstallDir

    if ($wipeData) {
        $graphs = Join-Path $Repo "graphs"
        if (Test-Path $graphs) {
            Get-ChildItem $graphs -Filter "*.db*" -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
            Write-Host "  [OK] Cleared local memory graphs in $graphs" -ForegroundColor Green
        }
    } else {
        Write-Host "  Kept your local memory data." -ForegroundColor DarkGray
    }

    Write-Host "`n  Done. Neuron has been uninstalled." -ForegroundColor Green
    if (Confirm-YesNo "Reinstall a fresh copy now (prerequisites -> PyTurso -> Neuron)?") {
        Invoke-InstallEverything
    } else {
        Pause-Any
    }
}

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
function Main {
    while ($true) {
        # Recompute install status once per menu entry (cheap: a single import probe).
        $status = if (Test-NeuronReady $InstallVenvPy) { "Neuron: INSTALLED" } else { "Neuron: not installed yet" }
        $idx = Show-Menu -Title "What would you like to do?    [$status]" -Options @(
            "1) Check my system",
            "2) Install prerequisites (Python + venv)",
            "3) Install PyTurso engine (prebuilt wheel)",
            "4) Install full Neuron",
            "5) Add Neuron to your AI",
            "6) Bridge & Cloud Turso...",
            "7) Run the test suite",
            "8) Live Log Console",
            "-  Install EVERYTHING (steps 2 -> 4 in one go)",
            "-  Clean install / Uninstall Neuron",
            "Exit"
        ) -Descriptions @(
            "Diagnose Python, Rust, MSVC and Python deps; offer to auto-repair.",
            "Verify Python 3.10-3.14 and create the install venv. Do this BEFORE Turso.",
            "Install the database engine from the bundled wheel - no Rust/MSVC, no hang.",
            "Install the Neuron package + all deps, then verify the imports.",
            "Write the MCP config for Claude, Cursor, VS Code, OpenCode, Zed or ChatGPT.",
            "Connect a Turso Cloud DB and/or launch the HTTP bridge for remote clients.",
            "Run the pytest suite (core-only or full).",
            "Live view of graph databases, node/link counts and link health.",
            "Runs prerequisites, PyTurso and full Neuron back-to-back.",
            "Remove the install (venv, shortcut, app registrations); optionally reinstall fresh.",
            "Close the Configuration Center."
        )

        switch ($idx) {
            0 { Invoke-Check }
            1 { Invoke-Prereqs }
            2 { Invoke-PyTurso }
            3 { Invoke-Neuron }
            4 { Invoke-AddToAI }
            5 { Show-BridgeCloudMenu }
            6 { Invoke-Tests }
            7 { Invoke-Console }
            8 { Invoke-InstallEverything }
            9 { Invoke-CleanUninstall }
            10      { break }
            default { break }   # Esc
        }
        if ($idx -eq 10 -or $idx -eq -1) { break }
    }
    Clear-Host
    Write-Host "`n  Thanks for using Neuron. Bye!`n" -ForegroundColor Cyan
}

Main
