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

# Current console width (safe default when it can't be read).
function Get-ConWidth {
    $w = 80; try { $w = [Console]::WindowWidth } catch {}
    if ($w -lt 20) { $w = 80 }
    return $w
}

# Write one frame line PADDED to the full width, so redrawing over a previous
# frame (without Clear-Host) leaves no leftover characters -> no flicker.
function Write-FrameLine {
    param([string]$Text = "", [System.ConsoleColor]$Fg = [System.ConsoleColor]::Gray)
    $w = Get-ConWidth
    if ($Text.Length -ge $w) { $Text = $Text.Substring(0, $w - 1) } else { $Text = $Text.PadRight($w - 1) }
    Write-Host $Text -ForegroundColor $Fg
}

# One menu row; highlighted rows keep the green background only under the label
# but still pad the rest of the line (default colors) to erase any ghost.
function Write-MenuOption {
    param([string]$Text, [switch]$Selected)
    $w = Get-ConWidth
    if ($Selected) {
        $label = " $Text "
        Write-Host "   > " -NoNewline -ForegroundColor Green
        Write-Host $label   -NoNewline -ForegroundColor Black -BackgroundColor Green
        $pad = ($w - 1) - (5 + $label.Length)
        if ($pad -gt 0) { Write-Host (' ' * $pad) -NoNewline }
        Write-Host ""
    } else {
        Write-FrameLine ("     " + $Text)
    }
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
    if ($redirected) { return (Read-NumberedMenu -Title $Title -Options $Options) }

    $sel = 0
    try { [Console]::CursorVisible = $false } catch {}
    Clear-Host                       # clear ONCE; later frames overwrite in place
    try {
        while ($true) {
            # Home the cursor instead of clearing every frame - a full Clear-Host
            # on each keypress is what makes the menu flicker. Every line is padded
            # to the window width so moving the highlight leaves no ghost characters.
            try { [Console]::SetCursorPosition(0, 0) } catch { Clear-Host }
            Show-Banner
            Write-FrameLine ""
            Write-FrameLine "  $Title" -Fg Cyan
            Write-FrameLine "  Up/Down to move  -  Enter to choose  -  Esc to go back" -Fg DarkGray
            Write-FrameLine ""
            for ($i = 0; $i -lt $Options.Count; $i++) {
                Write-MenuOption -Text $Options[$i] -Selected:($i -eq $sel)
            }
            Write-FrameLine ""
            $desc = if ($Descriptions.Count -gt $sel -and $Descriptions[$sel]) { "   " + $Descriptions[$sel] } else { "" }
            Write-FrameLine $desc -Fg DarkCyan
            # Some hosts (ISE, embedded consoles) can't do live key reads and throw
            # here - fall back to the numbered prompt instead of crashing.
            try { $k = [Console]::ReadKey($true) }
            catch { return (Read-NumberedMenu -Title $Title -Options $Options) }
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

# Numbered fallback menu (redirected stdin, or a host without live key input).
function Read-NumberedMenu {
    param([string]$Title, [string[]]$Options)
    Clear-Host; Show-Banner
    Write-Host "`n  $Title`n" -ForegroundColor Cyan
    for ($i = 0; $i -lt $Options.Count; $i++) { Write-Host ("   {0}) {1}" -f $i, $Options[$i]) }
    $raw = Read-Host "`n  Choose a number (blank = back)"
    if ($raw -match '^\d+$' -and [int]$raw -lt $Options.Count) { return [int]$raw }
    return -1
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
    Write-Host "      From the main menu: '2) Install / Update Neuron...' ->" -ForegroundColor DarkYellow
    Write-Host "      'Install / Update Neuron (FULL - recommended)'." -ForegroundColor DarkYellow
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
    Write-Host "`n  [OK] Prerequisites ready. Next: 'PyTurso engine only', or just run the FULL install." -ForegroundColor Green
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
    # Don't let a STALE bundled wheel shadow newer source (that would silently
    # install an old version on 'update'). Read the source version and skip an
    # older wheel so updates always land the newest code.
    $srcVer = $null
    $initTxt = Get-Content "$Repo\src\neuron\__init__.py" -Raw -ErrorAction SilentlyContinue
    if ($initTxt -match '__version__\s*=\s*"([\d.]+)"') { $srcVer = $Matches[1] }
    if ($wheel -and $srcVer -and ($wheel.Name -match 'neuron-([\d.]+)-')) {
        if ([version]$Matches[1] -lt [version]$srcVer) {
            Write-Host "  Ignoring older bundled wheel ($($Matches[1]) < source $srcVer) - building from source." -ForegroundColor DarkYellow
            $wheel = $null
        }
    }
    $target = if ($wheel) { $wheel.FullName } else { $Repo }
    if ($wheel) { Write-Host "  Wheel: $($wheel.Name)" -ForegroundColor Gray }
    else        { Write-Host "  Installing from source tree ($Repo)." -ForegroundColor DarkYellow }

    # --upgrade so an existing (older) install is actually replaced.
    $pipArgs = @("-m", "pip", "install", "--upgrade", $target)
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

    Invoke-ModelPrewarm -py $InstallVenvPy

    Write-Host "`n  Neuron installed into $InstallDir" -ForegroundColor Green
    Write-Host "  Next: main menu -> '3) Add Neuron to your AI' to wire it into your app." -ForegroundColor Green
    Pause-Any
}

# Download the 384-dim embedding model up front so the first real use is instant
# and any network problem surfaces here, not mid-conversation. The model itself
# is mandatory (every graph/vector op needs it); only the *download timing* is
# deferred. Fully skippable and offline-safe.
function Invoke-ModelPrewarm {
    param([string]$py)
    if (-not (Confirm-YesNo "Pre-download the ~80MB embedding model now? (recommended - makes first use instant; skip if offline)")) {
        Write-Host "  Skipped - the model will download automatically on first use." -ForegroundColor DarkYellow
        return
    }
    Write-Host "`n  Downloading the embedding model (~80MB, one-time). This can take a minute..." -ForegroundColor Yellow
    $code = "from fastembed import TextEmbedding; e=TextEmbedding('sentence-transformers/all-MiniLM-L6-v2'); list(e.embed(['warm up'])); print('OK')"
    # Run from the install dir (a known-good cwd, same as the server) and CAPTURE
    # output so a Python traceback never dumps a scary wall of text at the user.
    Push-Location $InstallDir
    try { $out = & $py -c $code 2>&1 } finally { Pop-Location }
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  [OK] Embedding model cached - first use will be instant." -ForegroundColor Green
    } else {
        $reason = ($out | Where-Object { $_ -match '\S' } | Select-Object -Last 1)
        Write-Host "  [!] Could not pre-download the model right now (this is NON-fatal)." -ForegroundColor DarkYellow
        Write-Host "      Neuron will fetch it automatically the first time you use it." -ForegroundColor DarkYellow
        if ($reason) { Write-Host "      Reason: $reason" -ForegroundColor DarkGray }
    }
}

# All-in-one (2 -> 4) -------------------------------------------------------
function Invoke-InstallEverything {
    Invoke-Prereqs
    Invoke-PyTurso
    Invoke-Neuron
}

# Run an action while capturing EVERYTHING to a timestamped log, so install
# errors that scroll off (or that a later screen-clear wipes) are always
# recoverable. Prints the log path at the end.
function Invoke-Logged {
    param([string]$Name, [scriptblock]$Action)
    $logDir = Join-Path $InstallDir "logs"
    try { New-Item -ItemType Directory -Path $logDir -Force | Out-Null } catch {}
    $log = Join-Path $logDir ("{0}-{1}.log" -f $Name, (Get-Date -Format "yyyyMMdd-HHmmss"))
    $started = $false
    try { Start-Transcript -Path $log -ErrorAction Stop | Out-Null; $started = $true } catch {}
    try { & $Action }
    finally {
        if ($started) {
            try { Stop-Transcript | Out-Null } catch {}
            Write-Host "`n  A full log of this run was saved to:" -ForegroundColor DarkGray
            Write-Host "    $log" -ForegroundColor DarkGray
            Write-Host "  (If anything failed above, copy that file when asking for help.)" -ForegroundColor DarkGray
            Pause-Any
        }
    }
}

# Consolidated install menu: exactly three choices (FULL / dependencies /
# PyTurso), all logged. FULL doubles as the UPDATE path (uses pip --upgrade).
function Show-InstallMenu {
    while ($true) {
        $idx = Show-Menu -Title "Install / Update Neuron" -Options @(
            "Install / Update Neuron (FULL - recommended)",
            "Dependencies only (Python + venv)",
            "PyTurso engine only",
            "Back"
        ) -Descriptions @(
            "Does everything: prerequisites -> PyTurso -> Neuron + model. Also UPDATES an existing install.",
            "Just verify Python and create the install venv (no packages yet).",
            "Just (re)install the PyTurso database engine from the bundled wheel.",
            ""
        )
        switch ($idx) {
            0 { Invoke-Logged -Name "install-full" -Action { Invoke-InstallEverything } }
            1 { Invoke-Logged -Name "install-deps" -Action { Invoke-Prereqs } }
            2 { Invoke-Logged -Name "install-pyturso" -Action { Invoke-PyTurso } }
            default { return }
        }
    }
}

# Explain what the seed knowledge DB is and how to build/import one.
function Invoke-SeedGuide {
    Clear-Host; Show-Banner
    Write-Host "`n  Seed knowledge DB - what it is & how to build one`n" -ForegroundColor Yellow
    Write-Host "  Neuron builds a private memory graph from YOUR conversations as you go." -ForegroundColor Gray
    Write-Host "  The optional 'seed' is a pre-built knowledge base (notes/docs turned into" -ForegroundColor Gray
    Write-Host "  nodes + 384-dim vectors) that warm-starts cross-domain suggestions." -ForegroundColor Gray
    Write-Host ""
    Write-Host "  Neuron now ships WITHOUT a seed (empty base knowledge)." -ForegroundColor Cyan
    Write-Host "  Everything works without one - you just won't get seeded cross-domain hits" -ForegroundColor Gray
    Write-Host "  until you build your own. To build one from a folder of notes/markdown:" -ForegroundColor Gray
    Write-Host ""
    Write-Host "    1. Put your notes/docs in one folder (an Obsidian vault works well)." -ForegroundColor White
    Write-Host "    2. Run:" -ForegroundColor White
    Write-Host "         set NEURON_VAULT=C:\path\to\your\notes" -ForegroundColor White
    Write-Host "         `"$InstallVenvPy`" `"$ScriptDir\import_vault.py`"" -ForegroundColor White
    Write-Host "       (or:  python scripts\import_vault.py --vault <path> --out .\knowledge\base_knowledge.db)" -ForegroundColor Gray
    Write-Host "    3. To SHIP it as the default seed, copy the generated .db to:" -ForegroundColor White
    Write-Host "         src\neuron\data\base_knowledge.db   (only if you want it bundled)" -ForegroundColor Gray
    Write-Host ""
    Write-Host "  Full details: README.md and docs\DEVELOPER.md ('Seed knowledge')." -ForegroundColor DarkGray
    if ((Test-Path $InstallVenvPy) -and (Confirm-YesNo "Do you have a notes folder ready to import now?")) {
        $vault = Read-Host "  Path to your notes/vault folder"
        if ($vault -and (Test-Path $vault)) {
            $env:NEURON_VAULT = $vault
            Push-Location $Repo
            try { & $InstallVenvPy "$ScriptDir\import_vault.py" } catch {} finally { Pop-Location }
        } else {
            Write-Host "  That path doesn't exist - skipped." -ForegroundColor DarkYellow
        }
    }
    Pause-Any
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
    Write-Host "`n  Live Graph Console - refreshes only when the graph changes." -ForegroundColor Yellow
    Write-Host "  Press q (or Esc) to stop and return to the menu.`n" -ForegroundColor DarkGray
    $py = Get-RunnerPython
    if (-not $py) { Write-Host "  [X] No Python available." -ForegroundColor Red; Pause-Any; return }
    if (-not (Test-NeuronReady $py)) { Show-NotInstalled "The Live Graph Console"; Pause-Any; return }
    # --watch polls quietly and only re-prints when node/link/vector counts change.
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

# A runner able to launch mcp-proxy (the bridge's ONLY hard dependency).
function Get-McpProxyRunner {
    foreach ($c in @('mcp-proxy', 'uvx', 'uv', 'pipx')) {
        if (Get-Command $c -ErrorAction SilentlyContinue) { return $c }
    }
    return $null
}

# Are Turso CLOUD credentials configured (env or repo .env)? Cloud is the ONLY
# thing that needs libsql-client; the bridge itself does not.
function Test-CloudCredsConfigured {
    if ($env:TURSO_DATABASE_URL -and $env:TURSO_AUTH_TOKEN) { return $true }
    $envFile = Join-Path $Repo ".env"
    if (Test-Path $envFile) {
        $txt = Get-Content $envFile -Raw -ErrorAction SilentlyContinue
        $u = [regex]::Match($txt, '(?m)^\s*TURSO_DATABASE_URL\s*=\s*(\S.*)$')
        $t = [regex]::Match($txt, '(?m)^\s*TURSO_AUTH_TOKEN\s*=\s*(\S.*)$')
        return ($u.Success -and $t.Success)
    }
    return $false
}

# ---------------------------------------------------------------------------
# Background bridge state
# ---------------------------------------------------------------------------
# The bridge is launched in the BACKGROUND (a tracked child process) so the menu
# stays usable: the user can press Esc to return to the menu while it keeps
# serving, stop it with Ctrl+D on the bridge screen, or reopen/stop it later from
# the "HTTP bridge RUNNING" item that appears in the main menu only while alive.
$script:BridgeProc = $null
$script:BridgeUrl  = $null
$script:BridgeLog  = $null
$script:BridgeErr  = $null
$script:BridgePort = 8000
# Cloudflare quick-tunnel that exposes the bridge over public HTTPS (for ChatGPT).
$script:TunnelProc = $null
$script:TunnelUrl  = $null
$script:TunnelLog  = $null

function Test-BridgeAlive {
    return ($null -ne $script:BridgeProc -and -not $script:BridgeProc.HasExited)
}

# Stop the background bridge AND its mcp-proxy child. taskkill /T kills the whole
# tree; killing only the python parent would orphan the proxy holding the port.
function Stop-Bridge {
    Stop-Tunnel                       # never leave a tunnel pointing at a dead bridge
    if ($null -eq $script:BridgeProc) { return }
    $procId = $script:BridgeProc.Id
    try {
        if (-not $script:BridgeProc.HasExited) { & taskkill /PID $procId /T /F *> $null }
    } catch {}
    try { $script:BridgeProc.Dispose() } catch {}
    $script:BridgeProc = $null
    $script:BridgeUrl  = $null
}

# --- Cloudflare quick-tunnel -------------------------------------------------
# Exposes the local bridge over a public https://<random>.trycloudflare.com URL
# so ChatGPT (which can't reach localhost) can use it. No account/login needed.
function Test-TunnelAlive {
    return ($null -ne $script:TunnelProc -and -not $script:TunnelProc.HasExited)
}

function Get-Cloudflared {
    $c = Get-Command cloudflared -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    return $null
}

# Start the tunnel in the BACKGROUND and capture the public URL it prints. Sets
# $script:TunnelUrl to the ready-to-paste .../sse endpoint. Returns $true on success.
function Start-Tunnel {
    if (Test-TunnelAlive) { return $true }
    $cf = Get-Cloudflared
    if (-not $cf) {
        Write-Host "  [!] 'cloudflared' not found - can't open a public tunnel." -ForegroundColor DarkYellow
        if ((Get-Command winget -ErrorAction SilentlyContinue) -and (Confirm-YesNo "Install cloudflared now via winget?")) {
            & winget install --id Cloudflare.cloudflared -e --accept-source-agreements --accept-package-agreements
            $env:Path = [Environment]::GetEnvironmentVariable('Path','User') + ";" + [Environment]::GetEnvironmentVariable('Path','Machine')
            $cf = Get-Cloudflared
        }
        if (-not $cf) {
            Write-Host "      Install it (https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)" -ForegroundColor DarkYellow
            Write-Host "      or run by hand:  cloudflared tunnel --url http://127.0.0.1:$script:BridgePort" -ForegroundColor DarkYellow
            Start-Sleep -Milliseconds 1200
            return $false
        }
    }
    $logDir = Join-Path $InstallDir "logs"
    try { New-Item -ItemType Directory -Path $logDir -Force | Out-Null } catch {}
    $script:TunnelLog = Join-Path $logDir ("tunnel-{0}.log" -f (Get-Date -Format "yyyyMMdd-HHmmss"))
    $target = "http://127.0.0.1:$script:BridgePort"
    try {
        $script:TunnelProc = Start-Process -FilePath $cf `
            -ArgumentList @("tunnel", "--no-autoupdate", "--url", $target) `
            -PassThru -WindowStyle Hidden `
            -RedirectStandardOutput $script:TunnelLog `
            -RedirectStandardError  "$($script:TunnelLog).err"
    } catch {
        Write-Host "  [X] Could not start cloudflared: $_" -ForegroundColor Red
        $script:TunnelProc = $null
        return $false
    }
    # cloudflared prints the URL (to stderr) within a few seconds - poll for it.
    Write-Host "  Opening Cloudflare tunnel (a few seconds)..." -ForegroundColor DarkGray
    $rx = 'https://[a-z0-9-]+\.trycloudflare\.com'
    for ($i = 0; $i -lt 40; $i++) {
        if ($script:TunnelProc.HasExited) { break }
        foreach ($f in @($script:TunnelLog, "$($script:TunnelLog).err")) {
            if (Test-Path $f) {
                $m = Select-String -Path $f -Pattern $rx -ErrorAction SilentlyContinue | Select-Object -First 1
                if ($m) { $script:TunnelUrl = $m.Matches[0].Value.TrimEnd('/') + "/sse"; break }
            }
        }
        if ($script:TunnelUrl) { break }
        Start-Sleep -Milliseconds 500
    }
    if (-not $script:TunnelUrl) {
        Write-Host "  [!] Tunnel started but no public URL yet - see $script:TunnelLog" -ForegroundColor DarkYellow
        Start-Sleep -Milliseconds 800
    }
    return (Test-TunnelAlive)
}

function Stop-Tunnel {
    if ($null -eq $script:TunnelProc) { return }
    $tid = $script:TunnelProc.Id
    try { if (-not $script:TunnelProc.HasExited) { & taskkill /PID $tid /T /F *> $null } } catch {}
    try { $script:TunnelProc.Dispose() } catch {}
    $script:TunnelProc = $null
    $script:TunnelUrl  = $null
}

# Interactive "bridge is running" screen. Non-blocking: it watches for a key AND
# for the bridge dying on its own. Ctrl+D stops it; Esc leaves it running.
function Watch-Bridge {
    try { [Console]::CursorVisible = $true } catch {}
    while ($true) {
        Clear-Host; Show-Banner
        if (-not (Test-BridgeAlive)) {
            Write-Host "`n  The bridge process has stopped." -ForegroundColor DarkYellow
            foreach ($f in @($script:BridgeLog, $script:BridgeErr)) {
                if ($f -and (Test-Path $f)) {
                    Get-Content $f -Tail 12 -ErrorAction SilentlyContinue |
                        ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
                }
            }
            Stop-Bridge
            Pause-Any
            return
        }
        Write-Host "`n  HTTP bridge is RUNNING (in the background)" -ForegroundColor Green
        Write-Host "     local endpoint : $script:BridgeUrl" -ForegroundColor Gray
        Write-Host "     PID            : $($script:BridgeProc.Id)" -ForegroundColor DarkGray
        Write-Host ""
        if ((Test-TunnelAlive) -and $script:TunnelUrl) {
            Write-Host "  PUBLIC URL (Cloudflare): $script:TunnelUrl" -ForegroundColor Green
            Write-Host "     Paste THIS as the MCP connector URL in ChatGPT (Developer mode)." -ForegroundColor Gray
        } elseif (Test-TunnelAlive) {
            Write-Host "  Cloudflare tunnel: starting - URL not ready yet, press a key to refresh." -ForegroundColor DarkYellow
        } else {
            Write-Host "  Not exposed publicly yet. Press [T] to open a Cloudflare tunnel" -ForegroundColor Gray
            Write-Host "  (or by hand:  cloudflared tunnel --url http://127.0.0.1:$script:BridgePort )." -ForegroundColor DarkGray
        }
        Write-Host ""
        Write-Host "  ---------------------------------------------------------------" -ForegroundColor DarkGray
        $tkey = if (Test-TunnelAlive) { "[T] stop tunnel" } else { "[T] expose via Cloudflare" }
        Write-Host "   [Ctrl+D] stop bridge    [Esc] back to menu    $tkey" -ForegroundColor Cyan
        Write-Host "  ---------------------------------------------------------------" -ForegroundColor DarkGray

        # Wait for a key OR the process exiting, without blocking forever on ReadKey.
        $key = $null
        while ($true) {
            $avail = $false
            try { $avail = [Console]::KeyAvailable } catch { $avail = $true }
            if ($avail) { try { $key = [Console]::ReadKey($true) } catch { $key = $null }; break }
            if (-not (Test-BridgeAlive)) { break }   # died on its own -> outer loop redraws "stopped"
            Start-Sleep -Milliseconds 250
        }
        if ($null -eq $key) { Start-Sleep -Milliseconds 150; continue }

        # Ctrl+D -> stop.  Esc -> leave it running and return to the menu.
        if ($key.Key -eq 'D' -and ($key.Modifiers -band [ConsoleModifiers]::Control)) {
            Write-Host "`n  Stopping the bridge..." -ForegroundColor Yellow
            Stop-Bridge
            Write-Host "  [OK] Bridge stopped." -ForegroundColor Green
            Pause-Any
            return
        }
        # T -> toggle the public Cloudflare tunnel.
        if ($key.Key -eq 'T') {
            if (Test-TunnelAlive) {
                Write-Host "`n  Closing the Cloudflare tunnel..." -ForegroundColor Yellow
                Stop-Tunnel
            } else {
                Start-Tunnel | Out-Null
            }
            continue
        }
        if ($key.Key -eq 'Escape') { return }
        # any other key just redraws the screen
    }
}

# --- Port-conflict helpers ---------------------------------------------------
# A bridge left running from a previous session (Esc), or any other app, can hold
# port 8000 — mcp-proxy would then die with a cryptic uvicorn STARTUP_FAILURE.
# These let Invoke-Bridge detect it and offer to reclaim the port or move on.
function Get-PortOwners([int]$Port) {
    $owners = @()
    try {
        $owners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop |
                    Select-Object -Expand OwningProcess -Unique)
    } catch {
        # Older hosts without Get-NetTCPConnection: parse netstat.
        try {
            foreach ($ln in (netstat -ano | Select-String ":$Port\s" | Select-String "LISTENING")) {
                $parts = ($ln.ToString() -split '\s+') | Where-Object { $_ }
                if ($parts.Count -ge 5) { $owners += [int]$parts[-1] }
            }
            $owners = @($owners | Select-Object -Unique)
        } catch {}
    }
    return @($owners)
}

function Test-PortFree([int]$Port) {
    return ((Get-PortOwners $Port).Count -eq 0)
}

# First free port at/after $Start (bounded scan); returns 0 if none found.
function Get-FreePort([int]$Start) {
    for ($p = $Start; $p -lt ($Start + 20); $p++) {
        if (Test-PortFree $p) { return $p }
    }
    return 0
}

function Invoke-Bridge {
    Clear-Host; Show-Banner
    Write-Host "`n  Launch the Neuron -> HTTP bridge (for ChatGPT & remote connectors)`n" -ForegroundColor Yellow
    # Already running? Just bring its status screen back to the front.
    if (Test-BridgeAlive) { Watch-Bridge; return }
    $py = Get-RunnerPython
    if (-not $py) { Write-Host "  [X] No Python available." -ForegroundColor Red; Pause-Any; return }
    if (-not (Test-NeuronReady $py)) { Show-NotInstalled "The HTTP bridge"; Pause-Any; return }

    # --- Plan B #1: the bridge needs a runner for mcp-proxy (uv/uvx/pipx) ---
    if (-not (Get-McpProxyRunner)) {
        Write-Host "  [!] The bridge runs 'mcp-proxy', which needs 'uv' (or pipx) - none found." -ForegroundColor DarkYellow
        if (Confirm-YesNo "Install uv now? (recommended - fetches mcp-proxy on demand)") {
            Write-Host "  Installing uv..." -ForegroundColor Yellow
            try { Invoke-Expression (Invoke-RestMethod -Uri 'https://astral.sh/uv/install.ps1') } catch { Write-Host "  [X] uv install failed: $_" -ForegroundColor Red }
            # Make uv visible to THIS session.
            $env:Path = "$env:USERPROFILE\.local\bin;$env:USERPROFILE\.cargo\bin;" +
                        [Environment]::GetEnvironmentVariable('Path','User') + ";" +
                        [Environment]::GetEnvironmentVariable('Path','Machine')
        }
        if (-not (Get-McpProxyRunner)) {
            Write-Host "  [X] Still no mcp-proxy runner - can't start the bridge." -ForegroundColor Red
            Write-Host "      Install uv (irm https://astral.sh/uv/install.ps1 | iex) or pipx, then retry." -ForegroundColor DarkYellow
            Pause-Any; return
        }
        Write-Host "  [OK] mcp-proxy runner available." -ForegroundColor Green
    }

    # --- Plan B #2: libsql is ONLY for the cloud tier, not for the bridge ---
    $serveLocal = $false
    if (Test-CloudCredsConfigured) {
        & $py -c "import libsql_client" 2>$null
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  [!] Cloud credentials are set but 'libsql-client' isn't installed." -ForegroundColor DarkYellow
            Write-Host "      libsql is needed ONLY for the CLOUD tier - the bridge works locally without it." -ForegroundColor Gray
            if (Confirm-YesNo "Install libsql-client to serve the CLOUD tier over the bridge?") {
                & $py -m pip install "libsql-client>=0.3.1"
                & $py -c "import libsql_client" 2>$null
                if ($LASTEXITCODE -eq 0) { Write-Host "  [OK] Cloud tier enabled." -ForegroundColor Green }
                else { Write-Host "  [!] Install failed - Plan B: serving the LOCAL engine." -ForegroundColor DarkYellow; $serveLocal = $true }
            } else { Write-Host "  Serving the LOCAL engine (Plan B)." -ForegroundColor DarkGray; $serveLocal = $true }
        }
    }

    $port = 8000; $bhost = "127.0.0.1"

    # Port-conflict guard: if 8000 is already taken (often a bridge orphaned by a
    # previous session), don't let mcp-proxy fail with a cryptic uvicorn error —
    # let the user reclaim the port or move to a free one.
    if (-not (Test-PortFree $port)) {
        $owners = Get-PortOwners $port
        $desc = (($owners | ForEach-Object {
            $pn = (Get-Process -Id $_ -ErrorAction SilentlyContinue).ProcessName
            if ($pn) { "$pn($_)" } else { "PID $_" }
        }) -join ", ")
        Clear-Host; Show-Banner
        Write-Host "`n  [!] Port $port is already in use by: $desc" -ForegroundColor DarkYellow
        Write-Host "      Most likely a bridge left running from a previous session." -ForegroundColor Gray
        $pick = Show-Menu -Title "Port $port is busy - what do you want to do?" -Options @(
            "Stop what's using it and start fresh on $port",
            "Start on the next free port instead",
            "Cancel"
        ) -Descriptions @(
            "Kills the process(es) holding port $port, then launches the bridge there.",
            "Leaves the other process alone and serves on the next free port (e.g. 8001).",
            "Do nothing and go back."
        )
        switch ($pick) {
            0 {
                foreach ($op in $owners) { try { & taskkill /PID $op /T /F *> $null } catch {} }
                Start-Sleep -Milliseconds 800
                if (-not (Test-PortFree $port)) {
                    Write-Host "  [X] Port $port is still busy after stopping those processes." -ForegroundColor Red
                    Pause-Any; return
                }
                Write-Host "  [OK] Port $port freed." -ForegroundColor Green
            }
            1 {
                $port = Get-FreePort ($port + 1)
                if ($port -eq 0) {
                    Write-Host "  [X] No free port found near 8000." -ForegroundColor Red
                    Pause-Any; return
                }
                Write-Host "  [OK] Using free port $port." -ForegroundColor Green
            }
            default { return }
        }
    }

    $script:BridgePort = $port
    $script:BridgeUrl = "http://${bhost}:${port}/sse"
    Write-Host "`n  Starting the bridge in the BACKGROUND so this menu stays usable." -ForegroundColor Gray
    Write-Host "  It serves Neuron over $script:BridgeUrl ." -ForegroundColor Gray

    $logDir = Join-Path $InstallDir "logs"
    try { New-Item -ItemType Directory -Path $logDir -Force | Out-Null } catch {}
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $script:BridgeLog = Join-Path $logDir "bridge-$stamp.log"
    $script:BridgeErr = Join-Path $logDir "bridge-$stamp.err.log"

    # When serving local, SUPPRESS cloud creds for the Neuron child so it never
    # tries the cloud tier — this makes the bridge work even with an OLDER installed
    # db.py that imports libsql_client whenever TURSO_* are present. NEURON_NO_DOTENV
    # stops the .env load; clearing the env vars covers real process-level creds too.
    # Env is inherited by the child at spawn time, so set it, launch, then restore.
    $savedUrl = $env:TURSO_DATABASE_URL; $savedTok = $env:TURSO_AUTH_TOKEN; $savedNoDot = $env:NEURON_NO_DOTENV
    if ($serveLocal) {
        $env:NEURON_NO_DOTENV = "1"
        Remove-Item Env:TURSO_DATABASE_URL -ErrorAction SilentlyContinue
        Remove-Item Env:TURSO_AUTH_TOKEN  -ErrorAction SilentlyContinue
    }
    try {
        $bridgeArgs = "`"$ScriptDir\bridge.py`" --port $port --host $bhost"
        $script:BridgeProc = Start-Process -FilePath $py -ArgumentList $bridgeArgs `
            -WorkingDirectory $Repo -PassThru -WindowStyle Hidden `
            -RedirectStandardOutput $script:BridgeLog `
            -RedirectStandardError  $script:BridgeErr
    } catch {
        Write-Host "  [X] Could not start the bridge: $_" -ForegroundColor Red
        $script:BridgeProc = $null
    } finally {
        if ($serveLocal) {
            if ($null -ne $savedUrl) { $env:TURSO_DATABASE_URL = $savedUrl }
            if ($null -ne $savedTok) { $env:TURSO_AUTH_TOKEN = $savedTok }
            if ($null -ne $savedNoDot) { $env:NEURON_NO_DOTENV = $savedNoDot } else { Remove-Item Env:NEURON_NO_DOTENV -ErrorAction SilentlyContinue }
        }
    }

    if (-not (Test-BridgeAlive)) {
        if ($script:BridgeProc) {
            # It started but exited fast — surface bridge.py's preflight error.
            Write-Host "`n  [X] The bridge exited during startup. Its output:" -ForegroundColor Red
            foreach ($f in @($script:BridgeLog, $script:BridgeErr)) {
                if ($f -and (Test-Path $f)) { Get-Content $f -Tail 15 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray } }
            }
            Stop-Bridge
        }
        Pause-Any; return
    }

    # bridge.py runs a ~3s preflight before mcp-proxy takes over; wait it out so a
    # fast failure is caught here instead of flashing the "running" screen.
    Write-Host "  Launching (running preflight)..." -ForegroundColor DarkGray
    for ($i = 0; $i -lt 20; $i++) {
        if ($script:BridgeProc.HasExited) { break }
        Start-Sleep -Milliseconds 300
    }
    if ($script:BridgeProc.HasExited) {
        Write-Host "`n  [X] The bridge exited during startup. Its output:" -ForegroundColor Red
        foreach ($f in @($script:BridgeLog, $script:BridgeErr)) {
            if ($f -and (Test-Path $f)) { Get-Content $f -Tail 15 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray } }
        }
        Stop-Bridge
        Pause-Any; return
    }

    Watch-Bridge
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

# Returns: a parsed object; a NEW empty object for a missing/empty file; or
# $null when the file EXISTS but can't be parsed (e.g. JSONC with // comments or
# trailing commas, common in VS Code settings.json). In that last case the caller
# MUST NOT overwrite the file - we'd clobber the user's real settings.
function Load-Json {
    param([string]$path)
    if (Test-Path $path) {
        $raw = Get-Content $path -Raw -ErrorAction SilentlyContinue
        if ($raw -and $raw.Trim()) {
            try { return ($raw | ConvertFrom-Json) }
            catch { return $null }   # exists but unparseable -> signal "hands off"
        }
    }
    return (New-Object psobject)
}

# Called when we refuse to touch an unparseable config: show the user exactly
# what to paste, so nothing is lost and they can still finish by hand.
function Show-CannotMerge {
    param([string]$Path, [string]$Vpy)
    Write-Host "  [!] Your config already exists but isn't plain JSON (it may use" -ForegroundColor DarkYellow
    Write-Host "      // comments or trailing commas): $Path" -ForegroundColor DarkYellow
    Write-Host "      To avoid wiping your settings, I did NOT modify it." -ForegroundColor DarkYellow
    Write-Host "      Add this 'neuron' entry to the MCP servers section by hand:" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "        `"neuron`": { `"command`": `"$Vpy`", `"args`": [`"-m`", `"neuron`"] }" -ForegroundColor Gray
    Write-Host ""
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
    $backup = $null
    if (Test-Path $path) { $backup = "$path.neuron-bak"; Copy-Item $path $backup -Force -ErrorAction SilentlyContinue }
    try {
        $obj | ConvertTo-Json -Depth 20 | Set-Content -Path $path -Encoding UTF8 -ErrorAction Stop
    } catch {
        Write-Host "  [X] Could not write $path : $_" -ForegroundColor Red
        return
    }
    # Verify what we wrote is valid JSON; roll back from the backup if not.
    try { Get-Content $path -Raw | ConvertFrom-Json | Out-Null }
    catch {
        if ($backup) { Copy-Item $backup $path -Force -ErrorAction SilentlyContinue }
        Write-Host "  [X] Write verification failed - restored your original file. No changes made." -ForegroundColor Red
        return
    }
    Write-Host "  [OK] Wrote $path" -ForegroundColor Green
    Write-Host "       (previous version, if any, saved as *.neuron-bak)" -ForegroundColor DarkGray
}

# Detailed, copy-paste tutorial per client. Printed AFTER we auto-write the
# config, so a non-technical user can (a) see exactly what was done, (b) redo it
# by hand on another machine, and (c) know how to verify it. No API key is ever
# needed for a local MCP server - that's called out explicitly.
function Show-ClientTutorial {
    param([string]$App, [string]$Path, [string]$Vpy)
    $folder = Split-Path -Parent $Path
    $file   = Split-Path -Leaf   $Path
    $vj     = $Vpy.Replace('\', '\\')   # backslashes doubled for valid JSON

    Write-Host ""
    Write-Host "  ============================================================" -ForegroundColor Green
    Write-Host "  [DONE] Neuron was added to your config AUTOMATICALLY." -ForegroundColor Green
    Write-Host "         Just RESTART the app. No API key needed (runs locally)." -ForegroundColor Green
    Write-Host "  ============================================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "  Reference only - how to do the same by hand / on another machine:" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "  1) Open this folder:" -ForegroundColor Yellow
    Write-Host "       $folder" -ForegroundColor White
    Write-Host "  2) Open (or create) this file:" -ForegroundColor Yellow
    Write-Host "       $file" -ForegroundColor White
    Write-Host "  3) Add the 'neuron' entry shown below (merge into what's there)." -ForegroundColor Yellow
    Write-Host ""

    switch ($App) {
        'claude-desktop' {
            Write-Host "     In Claude Desktop you can open this file via:" -ForegroundColor Gray
            Write-Host "       Settings (gear) -> Developer -> Edit Config" -ForegroundColor Gray
            Write-Host "     (The web 'Connectors' panel is for REMOTE servers; a local one" -ForegroundColor DarkGray
            Write-Host "      like Neuron goes in this config file.)" -ForegroundColor DarkGray
            Write-Host ""
            Write-Host "     {" -ForegroundColor White
            Write-Host "       `"mcpServers`": {" -ForegroundColor White
            Write-Host "         `"neuron`": { `"command`": `"$vj`", `"args`": [`"-m`", `"neuron`"] }" -ForegroundColor White
            Write-Host "       }" -ForegroundColor White
            Write-Host "     }" -ForegroundColor White
            Write-Host ""
            Write-Host "  4) FULLY quit Claude Desktop (right-click tray icon -> Quit) and reopen." -ForegroundColor Yellow
            Write-Host "     Neuron then appears under the tools/hammer icon in a chat." -ForegroundColor Gray
        }
        'claude-code' {
            Write-Host "     EASIEST - just run this command in a terminal:" -ForegroundColor Gray
            Write-Host "       claude mcp add neuron -- `"$Vpy`" -m neuron" -ForegroundColor White
            Write-Host "     Or edit the file by hand ($file) and add:" -ForegroundColor Gray
            Write-Host "       `"mcpServers`": { `"neuron`": { `"command`": `"$vj`", `"args`": [`"-m`",`"neuron`"], `"cwd`": `"$($InstallDir.Replace('\','\\'))`" } }" -ForegroundColor White
            Write-Host "  4) Verify with:  claude mcp list   (neuron should be listed)" -ForegroundColor Yellow
        }
        'cursor' {
            Write-Host "     `"mcpServers`": { `"neuron`": { `"command`": `"$vj`", `"args`": [`"-m`",`"neuron`"] } }" -ForegroundColor White
            Write-Host "  4) Cursor -> Settings -> MCP: toggle 'neuron' ON, then restart Cursor." -ForegroundColor Yellow
        }
        'vscode' {
            Write-Host "     `"mcp`": { `"servers`": { `"neuron`": { `"type`":`"stdio`", `"command`": `"$vj`", `"args`": [`"-m`",`"neuron`"] } } }" -ForegroundColor White
            Write-Host "  4) Restart VS Code. Needs an MCP client: Copilot agent mode or the" -ForegroundColor Yellow
            Write-Host "     'Continue' extension. Open it and Neuron's tools appear." -ForegroundColor Gray
        }
        'opencode' {
            Write-Host "     `"mcp`": { `"neuron`": { `"command`": [`"$vj`",`"-m`",`"neuron`"], `"type`":`"local`" } }" -ForegroundColor White
            Write-Host "  4) Restart OpenCode." -ForegroundColor Yellow
        }
        'zed' {
            Write-Host "     `"context_servers`": { `"neuron`": { `"command`": { `"path`": `"$vj`", `"args`": [`"-m`",`"neuron`"] } } }" -ForegroundColor White
            Write-Host "  4) Restart Zed." -ForegroundColor Yellow
        }
    }
    Write-Host "  ------------------------------------------------------------" -ForegroundColor DarkGray
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
            if ($null -eq $cfg) { Show-CannotMerge $path $vpy; break }
            $servers = Get-OrAddObject $cfg 'mcpServers'
            Set-Prop $servers 'neuron' ([pscustomobject]@{ command = $vpy; args = $nargs })
            Save-Json $cfg $path
            Show-ClientTutorial -App 'claude-desktop' -Path $path -Vpy $vpy
        }
        'claude-code' {
            $path = "$env:USERPROFILE\.claude.json"
            $cfg = Load-Json $path
            if ($null -eq $cfg) { Show-CannotMerge $path $vpy; break }
            $servers = Get-OrAddObject $cfg 'mcpServers'
            Set-Prop $servers 'neuron' ([pscustomobject]@{ command = $vpy; args = $nargs; cwd = $InstallDir })
            Save-Json $cfg $path
            Show-ClientTutorial -App 'claude-code' -Path $path -Vpy $vpy
        }
        'cursor' {
            $path = "$env:USERPROFILE\.cursor\mcp.json"
            $cfg = Load-Json $path
            if ($null -eq $cfg) { Show-CannotMerge $path $vpy; break }
            $servers = Get-OrAddObject $cfg 'mcpServers'
            Set-Prop $servers 'neuron' ([pscustomobject]@{ command = $vpy; args = $nargs })
            Save-Json $cfg $path
            Show-ClientTutorial -App 'cursor' -Path $path -Vpy $vpy
        }
        'vscode' {
            $path = "$env:APPDATA\Code\User\settings.json"
            $cfg = Load-Json $path
            if ($null -eq $cfg) { Show-CannotMerge $path $vpy; break }
            $mcp = Get-OrAddObject $cfg 'mcp'
            $servers = Get-OrAddObject $mcp 'servers'
            Set-Prop $servers 'neuron' ([pscustomobject]@{ type = 'stdio'; command = $vpy; args = $nargs })
            Save-Json $cfg $path
            Show-ClientTutorial -App 'vscode' -Path $path -Vpy $vpy
        }
        'opencode' {
            $path = "$env:USERPROFILE\.config\opencode\opencode.json"
            $cfg = Load-Json $path
            if ($null -eq $cfg) { Show-CannotMerge $path $vpy; break }
            $mcp = Get-OrAddObject $cfg 'mcp'
            Set-Prop $mcp 'neuron' ([pscustomobject]@{ command = @($vpy, '-m', 'neuron'); type = 'local' })
            Save-Json $cfg $path
            Show-ClientTutorial -App 'opencode' -Path $path -Vpy $vpy
        }
        'zed' {
            $path = "$env:APPDATA\Zed\settings.json"
            $cfg = Load-Json $path
            if ($null -eq $cfg) { Show-CannotMerge $path $vpy; break }
            $cs = Get-OrAddObject $cfg 'context_servers'
            Set-Prop $cs 'neuron' ([pscustomobject]@{ command = [pscustomobject]@{ path = $vpy; args = $nargs } })
            Save-Json $cfg $path
            Show-ClientTutorial -App 'zed' -Path $path -Vpy $vpy
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
        if ($null -eq $cfg) {
            Write-Host "  [!] Skipped $($t.app): its config isn't plain JSON - remove 'neuron' by hand." -ForegroundColor DarkYellow
            continue
        }
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

    Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction SilentlyContinue
    if (-not (Test-Path $target)) { Write-Host "  [OK] Removed $target" -ForegroundColor Green; return }

    # Still there -> files are locked, almost always by a running Neuron process
    # (an AI app keeping its stdio server alive). Find and offer to stop them.
    Write-Host "  [!] Some files are locked - a Neuron process is likely still running." -ForegroundColor DarkYellow
    $procs = @()
    try {
        $procs = Get-Process -ErrorAction SilentlyContinue |
                 Where-Object { $_.Path -and $_.Path.ToLower().StartsWith($target.ToLower()) }
    } catch {}
    if ($procs.Count -gt 0) {
        Write-Host ("      Holding it open: " + (($procs | ForEach-Object { "$($_.ProcessName)($($_.Id))" }) -join ", ")) -ForegroundColor DarkYellow
        if (Confirm-YesNo "Stop these Neuron processes and retry removal?") {
            foreach ($p in $procs) { try { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue } catch {} }
            Start-Sleep -Milliseconds 700
            Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction SilentlyContinue
        }
    } else {
        Write-Host "      No Neuron process found under the folder - the lock may be your AI app itself." -ForegroundColor DarkYellow
        if (Confirm-YesNo "Fully quit Claude Desktop / Cursor, then retry removal now?") {
            Start-Sleep -Milliseconds 500
            Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction SilentlyContinue
        }
    }

    if (Test-Path $target) {
        Write-Host "  [X] Could not fully remove it. Close every app using Neuron and delete manually:" -ForegroundColor Red
        Write-Host "        $target" -ForegroundColor DarkYellow
    } else {
        Write-Host "  [OK] Removed $target" -ForegroundColor Green
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
# Kill running Neuron MCP server processes
# ---------------------------------------------------------------------------
# AI apps (Claude Desktop, Cursor, ...) spawn Neuron as `python -m neuron` and keep
# it alive. Sometimes those linger — locking files, or after an app crash. Find
# them by command line and offer to stop them; the app relaunches Neuron when it
# next needs it, so stopping them is safe.
function Get-NeuronServerProcs {
    $me = $PID
    $procs = @()
    try {
        $procs = @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
            $_.ProcessId -ne $me -and $_.CommandLine -and
            ($_.CommandLine -match '(?i)(-m\s+neuron|\\run_mcp\.bat|Programs\\neuron\\\.venv)')
        })
    } catch {
        # No CIM available (rare) -> fall back to processes whose exe is in the install venv.
        try {
            $root = $InstallDir.ToLower()
            $procs = @(Get-Process -ErrorAction SilentlyContinue | Where-Object {
                $_.Id -ne $me -and $_.Path -and $_.Path.ToLower().StartsWith($root)
            } | ForEach-Object { [pscustomobject]@{ ProcessId = $_.Id; CommandLine = $_.Path } })
        } catch {}
    }
    return @($procs)
}

function Invoke-KillMcp {
    Clear-Host; Show-Banner
    Write-Host "`n  Stop running Neuron MCP server processes`n" -ForegroundColor Yellow
    Write-Host "  Your AI apps launch Neuron as 'python -m neuron' and keep it running." -ForegroundColor Gray
    Write-Host "  Stopping these is safe - the app relaunches Neuron when it next needs it." -ForegroundColor DarkGray

    $procs = Get-NeuronServerProcs
    if ($procs.Count -eq 0) {
        Write-Host "`n  No running Neuron server process found." -ForegroundColor Green
        Pause-Any; return
    }
    Write-Host "`n  Found $($procs.Count) process(es):" -ForegroundColor Cyan
    foreach ($p in $procs) {
        $pn = (Get-Process -Id $p.ProcessId -ErrorAction SilentlyContinue).ProcessName
        $cl = [string]$p.CommandLine
        if ($cl.Length -gt 100) { $cl = $cl.Substring(0, 100) + "..." }
        Write-Host ("    {0}({1})  {2}" -f $pn, $p.ProcessId, $cl) -ForegroundColor Gray
    }
    if (Test-BridgeAlive) {
        Write-Host "  Note: the HTTP bridge is running; its Neuron backend may be in this list." -ForegroundColor DarkYellow
    }
    Write-Host ""
    if (-not (Confirm-YesNo "Stop these $($procs.Count) process(es) now?")) {
        Write-Host "  Cancelled - nothing was stopped." -ForegroundColor DarkYellow
        Pause-Any; return
    }
    $killed = 0
    foreach ($p in $procs) {
        try { & taskkill /PID $p.ProcessId /T /F *> $null; if ($LASTEXITCODE -eq 0) { $killed++ } } catch {}
    }
    Start-Sleep -Milliseconds 600
    $left = Get-NeuronServerProcs
    if ($left.Count -eq 0) {
        Write-Host "  [OK] Stopped $killed Neuron process(es)." -ForegroundColor Green
    } else {
        Write-Host "  [!] $($left.Count) still running - an AI app is probably relaunching them." -ForegroundColor DarkYellow
        Write-Host "      Fully quit the app (Claude Desktop / Cursor) first, then retry." -ForegroundColor DarkYellow
    }
    Pause-Any
}

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
function Main {
    while ($true) {
        # Recompute install status once per menu entry (cheap: a single import probe).
        $status = if (Test-NeuronReady $InstallVenvPy) { "Neuron: INSTALLED" } else { "Neuron: not installed yet" }
        $bridgeActive = Test-BridgeAlive
        $bridgeTag = if ($bridgeActive) { "   |   Bridge: RUNNING" } else { "" }

        # The STOP/open-bridge item exists ONLY while the bridge is alive. It sits
        # at index 0 and pushes every numbered item down by one (handled by $offset).
        $options = @()
        $descs   = @()
        if ($bridgeActive) {
            $options += "[#] HTTP bridge RUNNING  ->  open / stop  (Ctrl+D)"
            $descs   += "The bridge is running in the background. Open its screen to stop it (Ctrl+D) or go back (Esc)."
        }
        $options += @(
            "1) Check my system",
            "2) Install / Update Neuron...",
            "3) Add Neuron to your AI",
            "4) Bridge & Cloud Turso...",
            "5) Seed knowledge DB (what & how)",
            "6) Run the test suite",
            "7) Live Graph Console",
            "-  Stop Neuron MCP processes (kill server)",
            "-  Clean install / Uninstall Neuron",
            "Exit"
        )
        $descs += @(
            "Diagnose Python, deps and (only if needed) the Rust/MSVC toolchain; auto-repair.",
            "FULL install / update, or just Dependencies / PyTurso. All runs are logged.",
            "Wire Neuron into Claude, Cursor, VS Code, OpenCode, Zed or ChatGPT (with a paste-by-hand tutorial).",
            "Connect a Turso Cloud DB and/or launch the HTTP bridge for remote clients.",
            "What the optional seed knowledge base is and how to build/import your own.",
            "Run the pytest suite (core-only or full).",
            "Live graph view (nodes/links/health) - refreshes only when it changes.",
            "Find and stop lingering 'python -m neuron' servers your AI apps left running.",
            "Remove the install (venv, shortcut, app registrations); optionally reinstall fresh.",
            "Close the Configuration Center."
        )

        $idx = Show-Menu -Title "What would you like to do?    [$status]$bridgeTag" -Options $options -Descriptions $descs

        if ($idx -eq -1) { break }                                   # Esc
        if ($bridgeActive -and $idx -eq 0) { Watch-Bridge; continue } # the STOP/open-bridge item
        $offset = if ($bridgeActive) { 1 } else { 0 }
        $real = $idx - $offset

        switch ($real) {
            0 { Invoke-Check }
            1 { Show-InstallMenu }
            2 { Invoke-AddToAI }
            3 { Show-BridgeCloudMenu }
            4 { Invoke-SeedGuide }
            5 { Invoke-Tests }
            6 { Invoke-Console }
            7 { Invoke-KillMcp }
            8 { Invoke-CleanUninstall }
            9       { break }
            default { break }
        }
        if ($real -eq 9) { break }
    }
    # Housekeeping: don't silently orphan a background bridge on exit.
    if (Test-BridgeAlive) {
        if (Confirm-YesNo "The HTTP bridge is still running in the background. Stop it before exiting?") {
            Stop-Bridge
            Write-Host "  [OK] Bridge stopped." -ForegroundColor Green
        } else {
            Write-Host "  Leaving the bridge running (PID $($script:BridgeProc.Id))." -ForegroundColor DarkYellow
            Start-Sleep -Milliseconds 900
        }
    }
    Clear-Host
    Write-Host "`n  Thanks for using Neuron. Bye!`n" -ForegroundColor Cyan
}

Main
