<#
.SYNOPSIS
    Neuron v3.3 - Installer Windows (dependency-first, wheel-based)
.DESCRIPTION
    Installs Neuron as a real Python package into a dedicated venv.

    Strategy (Option B - hybrid):
      1. Verify Python (3.10-3.14, the versions we ship pyturso wheels for).
      2. Create a venv under %LOCALAPPDATA%\Programs\neuron.
      3. pip install the Neuron wheel, using --find-links to point pip at the
         PRE-BUILT pyturso win_amd64 wheel shipped alongside this installer
         (folder .\vendor). This means NO compiler is needed on this machine.
      4. FALLBACK ONLY: if step 3 fails (e.g. unsupported Python, no matching
         pyturso wheel), install the MINIMAL MSVC C++ build tools (NOT the full
         Visual Studio) plus Rust, then pip install again so pyturso can compile.
      5. Register the MCP server with detected clients + Start Menu shortcut.

    If anything goes wrong, see INSTALL.md ("Manual installation" /
    "Troubleshooting") for a fully manual, step-by-step procedure.
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File install.ps1
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File install.ps1 -ForceCompile
#>

param(
    [switch]$skipLlmProviders,
    [switch]$ForceCompile,      # skip the prebuilt-wheel path, go straight to compiling
    [string]$Slug = 'neuron5',  # install identity (v5 "Synapse"); use 'neuron' for the v4 line
    [switch]$Yes                # non-interactive: assume defaults, no prompts
)

# Debug: set $true to see detailed operations (dir creation, file copies) instead of
# suppressing them with | Out-Null. Uncomment the line below before running.
# $NeuronDebug = $true

# Self-reinvoke with ExecutionPolicy Bypass, using the CURRENT PowerShell host
# so it works whether launched via Windows PowerShell (powershell.exe) OR
# PowerShell 7 (pwsh.exe). Machines with only pwsh don't have `powershell` on
# PATH, which used to crash here ("'powershell' non riconosciuto / not recognized").
if ($MyInvocation.MyCommand.Path -and -not ($env:__NEURON_BYPASS)) {
    $env:__NEURON_BYPASS = '1'
    $psExe = (Get-Process -Id $PID).Path                       # the host running this script
    if (-not $psExe) { $psExe = (Get-Command pwsh -ErrorAction SilentlyContinue).Source }
    if (-not $psExe) { $psExe = (Get-Command powershell -ErrorAction SilentlyContinue).Source }
    if ($psExe) {
        # Rebuild args by hand: splatting @PSBoundParameters into -File renders a
        # [switch] as "-Name True", which -File mode can't bind back to a switch.
        # Forward switches as bare flags and value params as "-Name Value".
        $fwd = @()
        foreach ($kv in $PSBoundParameters.GetEnumerator()) {
            if ($kv.Value -is [System.Management.Automation.SwitchParameter]) {
                if ($kv.Value.IsPresent) { $fwd += "-$($kv.Key)" }
            } else { $fwd += "-$($kv.Key)"; $fwd += "$($kv.Value)" }
        }
        & $psExe -ExecutionPolicy Bypass -File $MyInvocation.MyCommand.Path @fwd
        exit $LASTEXITCODE
    }
    # No separate host found - continue in this process (we're already running, so
    # the execution policy clearly allowed it).
}


$ErrorActionPreference = "Continue"
$SrcDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
# %LOCALAPPDATA% with a fallback: if it's ever empty (stripped service env),
# don't collapse to "\Programs\<slug>" at a drive root.
$LocalApp = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { "$env:USERPROFILE\AppData\Local" }
$DestDir = Join-Path $LocalApp "Programs\$Slug"   # install identity from -Slug (default neuron5)
$Vendor  = Join-Path $SrcDir "vendor"     # pre-built pyturso wheels live here
. (Join-Path $SrcDir "scripts\_neuron_paths.ps1")                                                # paths + RegistrationTargets

Write-Host "Neuron installer (wheel-based) - slug '$Slug'" -ForegroundColor Cyan
Write-Host "Source: $SrcDir  ->  Destination: $DestDir`n"

# ---------------------------------------------------------------
# Helper: stop a running Neuron server before we touch its venv/files,
# so the install can't fail on locked files (or half-write and corrupt).
# ---------------------------------------------------------------
function Stop-NeuronServices {
    param([string]$InstallDir)
    $pat = '(?i)(-m\s+neuron\b|\\run_mcp\.bat|' + [regex]::Escape($InstallDir) + ')'
    $procs = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and ($_.CommandLine -match $pat) -and $_.ProcessId -ne $PID })
    if (-not $procs -or $procs.Count -eq 0) { return }
    Write-Host "   Found running Neuron process(es):" -ForegroundColor Yellow
    $procs | ForEach-Object { Write-Host ("     PID {0}: {1}" -f $_.ProcessId, $_.CommandLine) -ForegroundColor DarkGray }
    $stop = if ($Yes) { $true } else { (Read-Host "   Stop them before installing (avoids locked files)? [Y/n]") -notmatch '^\s*(n|no)\s*$' }
    if ($stop) {
        foreach ($p in $procs) {
            try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop; Write-Host "     stopped PID $($p.ProcessId)" -ForegroundColor Green }
            catch { Write-Host "     could not stop PID $($p.ProcessId): $_" -ForegroundColor Red }
        }
        Start-Sleep -Seconds 1
    } else {
        Write-Host "   Continuing without stopping - install may fail on locked files." -ForegroundColor DarkYellow
    }
}

# ---------------------------------------------------------------
# Helper: which of our deps are already importable in a given interpreter.
# Lets us skip re-installing on a re-run and gives the user control.
# ---------------------------------------------------------------
function Get-DepsPresent {
    param([string]$Py)
    if (-not (Test-Path $Py)) { return @{} }
    $probe = 'import importlib.util as u,json; print(json.dumps({m:(u.find_spec(m) is not None) for m in ["neuron","mcp","fastembed","turso"]}))'
    $out = & $Py -c $probe 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $out) { return @{} }
    try { $h=@{}; ($out | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $h[$_.Name]=$_.Value }; return $h }
    catch { return @{} }
}

# ---------------------------------------------------------------
# Helper: download with URL fallback (each URL: 3 attempts)
# ---------------------------------------------------------------
function Download-File {
    param([string[]]$Urls, [string]$OutFile, [string]$Name)
    foreach ($url in $Urls) {
        Write-Host "   URL: $url"
        for ($a = 1; $a -le 3; $a++) {
            try {
                Invoke-WebRequest -Uri $url -OutFile $OutFile -UseBasicParsing -ErrorAction Stop
                return $true
            } catch {
                if ($a -lt 3) { Write-Host "   Attempt $a/3 failed, retrying..." -ForegroundColor DarkYellow; Start-Sleep -Seconds 3 }
            }
        }
        Write-Host "   URL exhausted, trying alternative..." -ForegroundColor DarkYellow
    }
    return $false
}

# ---------------------------------------------------------------
# Helper: pip retry (3 attempts). Returns $true/$false, never exits,
# so callers can decide whether to hard-fail or fall back.
# FIX (A1): invoke pip directly and trust ONLY $LASTEXITCODE -eq 0.
# ---------------------------------------------------------------
function Invoke-Pip {
    param([string]$Pip, [string[]]$PipArgs, [string]$Name)
    for ($a = 1; $a -le 3; $a++) {
        if ($a -gt 1) { Write-Host "   Attempt $a/3..." -ForegroundColor DarkYellow; Start-Sleep -Seconds 3 }
        & $Pip @PipArgs
        if ($LASTEXITCODE -eq 0) { Write-Host "   $Name OK" -ForegroundColor Green; return $true }
    }
    Write-Host "   $Name FAILED after 3 attempts" -ForegroundColor Red
    return $false
}

# ===============================================================
# 1. PYTHON  (FIX A2: integer major/minor compare, locale-proof)
# ===============================================================
Write-Host "1. Python 3.10 - 3.14..." -ForegroundColor Yellow
# Resolve a WORKING interpreter first, then version-check THAT one.
# Two field failures the old code hit:
#   - 'python' on PATH is the Microsoft Store App-Execution-Alias STUB: it
#     prints a Store hint to stderr and exits without output, so the version
#     parse crashed with "Cannot index into a null array" instead of a clear
#     message.
#   - 'python' missing but the py launcher present: install failed although a
#     perfectly good Python was on the machine.
$basePy = $null
$pyCmd = Get-Command python -ErrorAction SilentlyContinue
if ($pyCmd) {
    $exeOut = (& $pyCmd.Source -c "import sys; print(sys.executable)" 2>$null)
    if ($LASTEXITCODE -eq 0 -and $exeOut) { $basePy = ([string]$exeOut).Trim() }
}
if (-not $basePy -or $basePy -like '*\WindowsApps\*') {
    # Try the py launcher: it never resolves to the Store alias.
    $pyl = Get-Command py -ErrorAction SilentlyContinue
    if ($pyl) {
        $exeOut = (& $pyl.Source -3 -c "import sys; print(sys.executable)" 2>$null)
        if ($LASTEXITCODE -eq 0 -and $exeOut -and (([string]$exeOut).Trim() -notlike '*\WindowsApps\*')) {
            $basePy = ([string]$exeOut).Trim()
            Write-Host "   'python' on PATH is unusable (missing or Store alias) - using the py launcher instead:" -ForegroundColor DarkYellow
            Write-Host "   $basePy" -ForegroundColor DarkYellow
        }
    }
}
if (-not $basePy) { Write-Host "ERROR: no working Python found in PATH. Install Python 3.10-3.14 from python.org (check 'Add python.exe to PATH')." -ForegroundColor Red; exit 1 }
$verOut = (& $basePy -c "import sys; print(sys.version_info.major, sys.version_info.minor)" 2>$null)
if (-not $verOut) { Write-Host "ERROR: '$basePy' did not report a version - the interpreter looks broken. Reinstall Python from python.org." -ForegroundColor Red; exit 1 }
$parts  = ([string]$verOut).Trim().Split()
$maj = [int]$parts[0]; $min = [int]$parts[1]
Write-Host "   Detected Python $maj.$min : $basePy"

# The Microsoft Store build of Python (the "python" alias Windows offers when no
# real interpreter is on PATH) is not fit for this install: it runs under a
# per-package virtualized filesystem, so writes that look like they land under
# $DestDir/the venv from ITS point of view can be invisible or redirected when
# any other process (this script's own later steps, the MCP client launching
# venvPy, etc.) looks at the same path - "installs fine, then nothing can find
# its own folders". There is no reliable way to "force a destination folder"
# around that; the fix is to not build on it at all.
if ($basePy -like '*\WindowsApps\*') {
    Write-Host "ERROR: this is the Microsoft Store build of Python:" -ForegroundColor Red
    Write-Host "       $basePy" -ForegroundColor Red
    Write-Host "       It runs in a virtualized filesystem sandbox that silently breaks" -ForegroundColor Red
    Write-Host "       venvs and installed packages (files written by it can be invisible" -ForegroundColor Red
    Write-Host "       to every other program, including Neuron itself once launched)." -ForegroundColor Red
    Write-Host "" -ForegroundColor Red
    Write-Host "       Fix (pick one):" -ForegroundColor Yellow
    Write-Host "       1) Install real Python 3.10-3.14 from https://python.org/downloads" -ForegroundColor Yellow
    Write-Host "          (check 'Add python.exe to PATH' during setup), then re-run this" -ForegroundColor Yellow
    Write-Host "          installer in a NEW terminal window." -ForegroundColor Yellow
    Write-Host "       2) If you already have a real Python installed elsewhere, disable the" -ForegroundColor Yellow
    Write-Host "          Store alias: Settings > Apps > Advanced app settings >" -ForegroundColor Yellow
    Write-Host "          App execution aliases > turn OFF 'python.exe' and 'python3.exe'." -ForegroundColor Yellow
    exit 1
}
if ($maj -lt 3 -or ($maj -eq 3 -and $min -lt 10)) {
    Write-Host "ERROR: Python $maj.$min is too old (need >= 3.10)." -ForegroundColor Red; exit 1
}
# We ship prebuilt pyturso wheels for 3.10-3.14 (see .github/workflows/release.yml).
# Newer is allowed but will take the compile fallback if no matching wheel is found.
$inWheelMatrix = ($maj -eq 3 -and $min -ge 10 -and $min -le 14)
if (-not $inWheelMatrix) {
    Write-Host "   NOTE: Python $maj.$min is outside the prebuilt-wheel range (3.10-3.14)." -ForegroundColor DarkYellow
    Write-Host "         pyturso will be COMPILED (the toolchain fallback will run)." -ForegroundColor DarkYellow
}

# Preflight: report base tooling and detect uv (the pip-free fallback used below).
function Test-Cmd($n){ [bool](Get-Command $n -ErrorAction SilentlyContinue) }
$HasUv  = Test-Cmd uv
$HasUvx = Test-Cmd uvx
Write-Host "   Tooling: python $maj.$min | uv=$HasUv | uvx=$HasUvx"

# ===============================================================
# 2. VENV  (pip by default; fall back to uv when the venv has no pip)
# ===============================================================
Write-Host "`n2. Virtual env..." -ForegroundColor Yellow
Stop-NeuronServices -InstallDir $DestDir   # don't install over a running server (locked files)
if ($NeuronDebug) { Write-Host "  [..] Creating: $DestDir" -ForegroundColor DarkGray }; New-Item -ItemType Directory -Path $DestDir -Force | Out-Null
$venv = "$DestDir\.venv"
if (-not (Test-Path "$venv\Scripts\python.exe")) {
    Write-Host "   Creating virtual env..." -ForegroundColor Yellow
    # Use the interpreter VALIDATED in step 1 ($basePy), never the bare
    # 'python' alias - after the py-launcher fallback they can differ, and the
    # bare alias may be the Store stub.
    & $basePy -m venv $venv
    if (-not (Test-Path "$venv\Scripts\python.exe")) {
        Write-Host "   venv creation failed - retrying once after cleaning up a partial venv..." -ForegroundColor DarkYellow
        Remove-Item -LiteralPath $venv -Recurse -Force -ErrorAction SilentlyContinue
        & $basePy -m venv $venv
    }
}
$venvPy = "$venv\Scripts\python.exe"
$pip    = "$venv\Scripts\pip.exe"
& $pip --version 2>$null; $pipOk = ($LASTEXITCODE -eq 0) -and (Test-Path $pip)
$UseUv  = $false
if (-not (Test-Path $venvPy) -or -not $pipOk) {
    if ($HasUv) {
        Write-Host "   pip missing/broken -> creating the venv with uv instead." -ForegroundColor DarkYellow
        uv venv --python $basePy $venv   # pin to the interpreter validated in step 1 (matches vendored wheels)
        $venvPy = "$venv\Scripts\python.exe"
        $UseUv  = $true
    } else {
        Write-Host "ERROR: the venv has no working pip and 'uv' is not installed." -ForegroundColor Red
        Write-Host "  Fix ONE of:" -ForegroundColor Red
        Write-Host "    python -m ensurepip --upgrade                 # repair pip"
        Write-Host "    irm https://astral.sh/uv/install.ps1 | iex    # install uv (no pip), then re-run"
        exit 1
    }
}
if ($pipOk) { & $pip install --upgrade pip --quiet 2>$null }

# ===============================================================
# 3. LOCATE THE NEURON WHEEL
# ===============================================================
Write-Host "`n3. Locating Neuron wheel..." -ForegroundColor Yellow
$wheel = Get-ChildItem -Path $SrcDir -Filter "neuron-*.whl" -ErrorAction SilentlyContinue | Select-Object -First 1
$installTarget = if ($wheel) { $wheel.FullName } else { $SrcDir }   # fall back to pip install . (sdist/source)
if ($wheel) { Write-Host "   Using wheel: $($wheel.Name)" } else { Write-Host "   No wheel found, installing from source tree ($SrcDir)" -ForegroundColor DarkYellow }
if (Test-Path $Vendor) {
    $vWheels = (Get-ChildItem $Vendor -Filter "*.whl" -ErrorAction SilentlyContinue).Count
    Write-Host "   Vendor wheels (pyturso, prebuilt): $vWheels in $Vendor"
} else {
    Write-Host "   No vendor\ folder - pyturso will come from PyPI (compiles on Windows)." -ForegroundColor DarkYellow
}

# ===============================================================
# 4. INSTALL  (prebuilt path first; compile fallback second)
# ===============================================================
Write-Host "`n4. Installing Neuron + dependencies..." -ForegroundColor Yellow

# Install via pip OR uv (when the venv has no working pip). Keeps the
# prebuilt-pyturso --find-links path working under both. Retries 3x.
# Optional dependency pins the user controls: a constraints file caps the majors
# of unpinned deps (mcp/fastembed) so a future breaking release can't silently
# land. Ship it beside install.ps1; delete it to always take latest.
$Constraints = Join-Path $SrcDir "constraints.txt"
function Invoke-Install {
    param([string[]]$Target, [switch]$AllowVendor, [switch]$Upgrade, [switch]$ForceReinstall, [string]$Name = "Neuron")
    $extra = @()
    if (Test-Path $Constraints) { $extra += @("-c", $Constraints) }
    if ($Upgrade)        { $extra += "--upgrade" }
    if ($ForceReinstall) { $extra += "--force-reinstall" }
    if ($UseUv) {
        $a = @("pip", "install", "--python", $venvPy)
        if ($AllowVendor -and (Test-Path $Vendor)) { $a += @("--find-links", $Vendor) }
        $a += $extra; $a += $Target
        for ($t = 1; $t -le 3; $t++) {
            if ($t -gt 1) { Write-Host "   Attempt $t/3..." -ForegroundColor DarkYellow; Start-Sleep -Seconds 3 }
            & uv @a
            if ($LASTEXITCODE -eq 0) { Write-Host "   $Name OK" -ForegroundColor Green; return $true }
        }
        Write-Host "   $Name FAILED after 3 attempts (uv)" -ForegroundColor Red
        return $false
    }
    $a = @("install", "--timeout", "180", "--retries", "3")
    if ($AllowVendor -and (Test-Path $Vendor)) { $a += @("--find-links", $Vendor) }
    $a += $extra; $a += $Target
    return (Invoke-Pip -Pip $pip -PipArgs $a -Name $Name)
}

# --- User control: report deps already present, let the user skip/reinstall/upgrade.
$present  = Get-DepsPresent -Py $venvPy
$allThere = $present.Count -gt 0 -and $present["neuron"] -and $present["mcp"] -and $present["fastembed"] -and $present["turso"]
$instUpgrade = $false; $instForce = $false
Write-Host "   Dependencies already in the venv: " -NoNewline
if ($present.Count -eq 0) { Write-Host "none (fresh venv)" -ForegroundColor DarkGray }
else { Write-Host (($present.GetEnumerator() | ForEach-Object { "$($_.Key)=$(if($_.Value){'yes'}else{'no'})" }) -join ' ') -ForegroundColor DarkGray }

$skipInstall = $false
if ($allThere) {
    & $venvPy -c "import neuron; print('   Neuron ' + neuron.__version__ + ' already installed here.')" 2>$null
    $ans = if ($Yes) { 'S' } else { Read-Host "   [S]kip install / [R]einstall / [U]pgrade? (default S)" }
    switch -Regex ($ans) {
        '^[Rr]' { $instForce = $true;   Write-Host "   -> reinstalling." -ForegroundColor Yellow }
        '^[Uu]' { $instUpgrade = $true; Write-Host "   -> upgrading." -ForegroundColor Yellow }
        default { $skipInstall = $true; Write-Host "   -> keeping existing install (skipping)." -ForegroundColor Green }
    }
}

# --- Preview + confirm what will be installed (unless -Yes / skipping).
if (-not $skipInstall) {
    Write-Host "   Will install: $installTarget" -ForegroundColor Gray
    Write-Host "     + runtime deps from pyproject (mcp, fastembed, pyturso$(if (Test-Path $Constraints) {'; capped by constraints.txt'}))" -ForegroundColor Gray
    Write-Host "     into venv: $venv" -ForegroundColor Gray
    if (-not $Yes) {
        $go = Read-Host "   Proceed with install? [Y/n]"
        if ($go -match '^\s*(n|no)\s*$') { Write-Host "   Install cancelled by user." -ForegroundColor DarkYellow; exit 0 }
    }
}

$installed = $skipInstall
if ($skipInstall) { Write-Host "   [skip] using existing dependencies." -ForegroundColor Green }
if (-not $installed -and -not $ForceCompile) {
    Write-Host "   [a] Prebuilt path (no compiler needed)..." -ForegroundColor Yellow
    $installed = Invoke-Install -Target @($installTarget) -AllowVendor -Upgrade:$instUpgrade -ForceReinstall:$instForce
}

if (-not $installed) {
    # -----------------------------------------------------------
    # FALLBACK: minimal toolchain, then compile pyturso.
    # FIX A4: correct MSVC component ids (VC.Tools + Windows 11 SDK),
    #         NEVER the full VS suite / workloads.
    # -----------------------------------------------------------
    Write-Host "`n   Prebuilt install unavailable - falling back to compiling pyturso." -ForegroundColor Yellow
    Write-Host "   Installing MINIMAL build toolchain (not full Visual Studio)..." -ForegroundColor Yellow

    # Rust (needed to compile pyturso)
    if (-not (Get-Command rustc -ErrorAction SilentlyContinue)) {
        $ok = Download-File -Urls @(
            "https://win.rustup.rs/x86_64",
            "https://static.rust-lang.org/rustup/dist/x86_64-pc-windows-msvc/rustup-init.exe"
        ) -OutFile "$env:TEMP\rustup-init.exe" -Name "rustup"
        if ($ok) {
            Start-Process -Wait "$env:TEMP\rustup-init.exe" -ArgumentList @("-y","--default-toolchain","stable","--profile","minimal")
        }
        # FIX A5: also add cargo's bin to THIS session's PATH, then re-probe.
        $cargoBin = Join-Path $env:USERPROFILE ".cargo\bin"
        $machinePath = [Environment]::GetEnvironmentVariable("Path","Machine")
        $userPath    = [Environment]::GetEnvironmentVariable("Path","User")
        $env:Path = "$cargoBin;$machinePath;$userPath"
    }

    # Minimal MSVC C++ build tools (compiler + Windows SDK only)
    $vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
    $msvcOk = $false
    if (Test-Path $vswhere) {
        $vsInfo = & $vswhere -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -format json 2>$null | ConvertFrom-Json
        if ($vsInfo) { $msvcOk = $true }
    }
    if (-not $msvcOk) {
        $ok = Download-File -Urls @("https://aka.ms/vs/17/release/vs_BuildTools.exe") -OutFile "$env:TEMP\vs_BuildTools.exe" -Name "MSVC Build Tools"
        if ($ok) {
            Write-Host "   Installing minimal MSVC components..." -ForegroundColor Yellow
            Start-Process "$env:TEMP\vs_BuildTools.exe" -Wait -NoNewWindow -ArgumentList `
                "--quiet","--wait","--norestart",
                "--add","Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                "--add","Microsoft.VisualStudio.Component.Windows11SDK.22621"
        }
        $env:Path = "{0};{1}" -f ([Environment]::GetEnvironmentVariable("Path","Machine")), ([Environment]::GetEnvironmentVariable("Path","User"))
    }

    Write-Host "   [b] Compile path (pyturso from source)..." -ForegroundColor Yellow
    $installed = Invoke-Install -Target @($installTarget) -Upgrade:$instUpgrade -ForceReinstall:$instForce   # no vendor: build pyturso from source
}

if (-not $installed) {
    Write-Host "`nERROR: Neuron installation failed (both prebuilt and compile paths)." -ForegroundColor Red
    Write-Host "       See INSTALL.md > Troubleshooting for a manual procedure." -ForegroundColor Red
    exit 1
}

# ===============================================================
# 5. OPTIONAL LLM PROVIDERS (standalone chat only, not the MCP server)
# ===============================================================
if (-not $skipLlmProviders) {
    Write-Host "`n5. Optional LLM providers (standalone chat only)..." -ForegroundColor Yellow
    Write-Host "     [0] None (recommended for MCP-only use)"
    Write-Host "     [1] Ollama  [2] OpenAI  [3] Anthropic  [4] Gemini"
    $c = Read-Host "   Choose (0 = default, comma for multiple)"
    if ($c -ne "" -and $c -ne "0") {
        $pkgs = @("ollama","openai","anthropic","google-generativeai")
        foreach ($idx in ($c -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' })) {
            $n = [int]$idx - 1
            if ($n -ge 0 -and $n -lt $pkgs.Count) {
                if ($NeuronDebug) { Invoke-Install -Target @($pkgs[$n]) -Name $pkgs[$n] } else { Invoke-Install -Target @($pkgs[$n]) -Name $pkgs[$n] | Out-Null }
            }
        }
    }
} else { Write-Host "`n5. LLM providers skipped (-skipLlmProviders)" -ForegroundColor DarkYellow }

# ===============================================================
# 6. MCP REGISTRATION
# ===============================================================
Write-Host "`n6. MCP Registration (key '$Slug')..." -ForegroundColor Yellow
$runCmd = "$venv\Scripts\python.exe"
$mcpEntryStd = @{ command = $runCmd; args = @("-m","neuron") }   # module stays 'neuron'; only the registration key is the slug

# Register under mcpServers with a backup + full-depth write (never a blind, shallow
# overwrite that could truncate a deep config or lose it if it wasn't valid JSON).
function Register-Mcp {
    param([string]$App, [string]$Path, [object]$Entry, [string]$Key)
    if (-not (Test-Path $Path)) { Write-Host "   [ ] $App - config not found" -ForegroundColor DarkYellow; return }

    # 0-byte / whitespace-only file: nothing to merge into, but that is NOT the
    # same as "invalid JSON". Feeding an empty string to ConvertFrom-Json is
    # inconsistent across PowerShell versions - some throw, some silently return
    # $null - and a silent $null used to sail through untouched, get piped into
    # ConvertTo-Json, and overwrite the file with the literal text "null". Start
    # from a fresh object instead so an empty file is treated like "no config yet".
    $raw = Get-Content $Path -Raw -ErrorAction SilentlyContinue
    if (-not $raw -or -not $raw.Trim()) {
        $cfg = New-Object psobject
    } else {
        try { $cfg = $raw | ConvertFrom-Json -ErrorAction Stop }
        catch { Write-Host "   [!] $App - not plain JSON; add '$Key' by hand ($Path)" -ForegroundColor Red; return }
        if ($null -eq $cfg) {
            # Parsed fine but isn't an object (e.g. the file already contains the
            # literal `null`, or a bare array/number) - refuse rather than guess.
            Write-Host "   [!] $App - config isn't a JSON object; add '$Key' by hand ($Path)" -ForegroundColor Red
            return
        }
    }

    $backup = "$Path.neuron-bak"
    Copy-Item $Path $backup -Force -ErrorAction SilentlyContinue

    if (-not $cfg.mcpServers) { $cfg | Add-Member -NotePropertyName "mcpServers" -NotePropertyValue (New-Object PSObject) -Force }

    # Pre-5.0 installs registered under the plain key 'neuron'. If that is still
    # sitting next to the key we're about to (re)write, the client ends up
    # running BOTH servers - duplicate tools (mcp__neuron__* and mcp__$Key`__*).
    # Flag it (never delete silently); Uninstall or hand-editing $Path fixes it.
    if ($Key -ne 'neuron') {
        $legacy = $cfg.mcpServers.PSObject.Properties['neuron']
        if ($legacy) {
            $legacyCmd = $legacy.Value.command
            if ($legacyCmd -is [array]) { $legacyCmd = $legacyCmd -join ' ' }
            Write-Host "   [!] $App also has an older 'neuron' entry (pre-5.0): $legacyCmd" -ForegroundColor DarkYellow
            Write-Host "       Both will show up as separate MCP servers until you remove one" -ForegroundColor DarkYellow
            Write-Host "       (by hand from $Path, or via Uninstall)." -ForegroundColor DarkYellow
        }
    }

    $cfg.mcpServers | Add-Member -Force -MemberType NoteProperty -Name $Key -Value $Entry

    # Depth 100 (was 32): Claude Codes ~/.claude.json can be deeply nested
    # (GrowthBook flags + per-project state), and a too-low depth truncates
    # real data to the literal string "System.Collections.Hashtable", which
    # parses fine so verification passes but the entry we tried to add can
    # end up missing.
    # UTF-8 without a BOM. `-Encoding utf8NoBOM` is PS 7+ only and errors out on
    # Windows PowerShell 5.1; [IO.File]::WriteAllText with a UTF8Encoding($false)
    # produces the same BOM-less bytes on both hosts. Claude Code's JSON.parse
    # chokes on a leading BOM byte.
    try {
        $json = ($cfg | ConvertTo-Json -Depth 100)
        [System.IO.File]::WriteAllText($Path, [string]$json, [System.Text.UTF8Encoding]::new($false))
    } catch { Write-Host "   [X] $App - could not write $Path : $_" -ForegroundColor Red; return }

    # Verify (a) the file is still valid JSON and (b) our entry is actually
    # present on disk after the write. (b) catches PowerShell JSON-roundtrip
    # failures that dont produce invalid JSON but strip our addition -
    # previously reported "OK" while leaving the client with no MCP entry.
    $verifyOk = $true
    $verifyErr = ""
    try {
        $reread = Get-Content $Path -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
        if (-not $reread.mcpServers -or -not $reread.mcpServers.PSObject.Properties[$Key]) {
            $verifyOk = $false
            $verifyErr = "mcpServers.$Key is missing from $Path after the write (silent JSON-roundtrip data loss)."
        }
    } catch {
        $verifyOk = $false
        $verifyErr = $_.Exception.Message
    }
    if (-not $verifyOk) {
        $failCopy = "$Path.neuron-failed-write"
        try { Copy-Item $Path $failCopy -Force -ErrorAction SilentlyContinue } catch {}
        Copy-Item $backup $Path -Force -ErrorAction SilentlyContinue
        Write-Host "   [X] $App - write verification failed, restored the previous file." -ForegroundColor Red
        Write-Host "       Reason: $verifyErr" -ForegroundColor Red
        Write-Host "       Failed output saved for inspection: $failCopy" -ForegroundColor DarkYellow
        Write-Host "       Add the entry by hand, or re-run scripts\configuration.ps1 (Add to your AI)." -ForegroundColor DarkYellow
        return
    }

    Write-Host "   [OK] $App (key '$Key'; backup: $backup)"
}

# Generic JSON MCP writer for apps with non-mcpServers parent keys (VS Code, OpenCode, Zed).
function Register-McpNested {
    param([string]$App, [string]$Path, [string[]]$ParentKeys, [object]$Entry, [string]$Key)
    if (-not (Test-Path $Path)) { Write-Host "   [ ] $App - config not found" -ForegroundColor DarkYellow; return }
    $raw = Get-Content $Path -Raw -ErrorAction SilentlyContinue
    if (-not $raw -or -not $raw.Trim()) { $cfg = New-Object psobject }
    else { try { $cfg = $raw | ConvertFrom-Json -ErrorAction Stop } catch { Write-Host "   [!] $App - not plain JSON ($Path)" -ForegroundColor Red; return }
    if ($null -eq $cfg) { Write-Host "   [!] $App - not a JSON object ($Path)" -ForegroundColor Red; return } }
    $backup = "$Path.neuron-bak"
    Copy-Item $Path $backup -Force -ErrorAction SilentlyContinue
    $cur = $cfg
    foreach ($pk in $ParentKeys) {
        if (-not $cur.PSObject.Properties[$pk]) { $cur | Add-Member -NotePropertyName $pk -NotePropertyValue (New-Object PSObject) -Force }
        $cur = $cur.$pk
    }
    $cur | Add-Member -Force -MemberType NoteProperty -Name $Key -Value $Entry
    try { $json = ($cfg | ConvertTo-Json -Depth 100); [System.IO.File]::WriteAllText($Path, $json, [System.Text.UTF8Encoding]::new($false)) }
    catch { Write-Host "   [X] $App - could not write $Path : $_" -ForegroundColor Red; return }
    # B5 (Piano 05): verify-after-write + rollback (same guarantee Register-Mcp
    # already had — a JSON-roundtrip failure must never leave a broken config).
    $verifyOk = $true
    try {
        $reread = Get-Content $Path -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
        $walk = $reread
        foreach ($pk in $ParentKeys) { $walk = if ($walk) { $walk.$pk } else { $null } }
        if (-not $walk -or -not $walk.PSObject.Properties[$Key]) { $verifyOk = $false }
    } catch { $verifyOk = $false }
    if (-not $verifyOk) {
        Copy-Item $backup $Path -Force -ErrorAction SilentlyContinue
        Write-Host "   [X] $App - write verification failed, restored the previous file." -ForegroundColor Red
        return
    }
    Write-Host "   [OK] $App (key '$Key'; backup: $backup)"
}

# Codex CLI uses TOML instead of JSON for MCP config.
# B5 (Piano 05): section-aware MERGE. The old version wrote the file with ONLY
# the neuron block, silently DESTROYING every other server in the user's
# config.toml. Now: replace our [mcp_servers.<slug>] section if present, append
# it otherwise, preserve everything else, backup + verify + rollback.
function Register-CodexMcp {
    param([string]$Vpy, [string]$Slug)
    $tomlPath = "$env:USERPROFILE\.codex\config.toml"
    $tomlDir = Split-Path -Parent $tomlPath
    if (-not (Test-Path $tomlDir)) { if ($NeuronDebug) { Write-Host "       Creating: $tomlDir" -ForegroundColor DarkGray }; New-Item -ItemType Directory -Path $tomlDir -Force | Out-Null }
    $escaped = $Vpy -replace '\\', '\\'
    $block = "[mcp_servers.$Slug]`r`ncommand = `"$escaped`"`r`nargs = ['-m', 'neuron']`r`n"
    $old = ""
    if (Test-Path $tomlPath) {
        $old = Get-Content $tomlPath -Raw -ErrorAction SilentlyContinue
        if ($null -eq $old) { $old = "" }
        Copy-Item $tomlPath "$tomlPath.neuron-bak" -Force -ErrorAction SilentlyContinue
    }
    $header = "[mcp_servers." + $Slug + "]"
    if ($old.Contains($header)) {
        # Replace ONLY our section (up to the next [section] or EOF).
        $pattern = "(?ms)^\[mcp_servers\." + [regex]::Escape($Slug) + "\]\s*?\r?\n.*?(?=^\[|\z)"
        $safeBlock = $block.Replace('$', '$$')   # literal $ in a regex replacement
        $new = [regex]::Replace($old, $pattern, $safeBlock)
    } else {
        $new = $old
        if ($new -and -not $new.EndsWith("`n")) { $new += "`r`n" }
        $new += $block
    }
    try {
        [System.IO.File]::WriteAllText($tomlPath, $new, [System.Text.UTF8Encoding]::new($false))
        $after = Get-Content $tomlPath -Raw
        if (-not $after.Contains($header)) { throw "section missing after write" }
        Write-Host "   [OK] Codex CLI (TOML section merge; backup: $tomlPath.neuron-bak)"
    }
    catch {
        if (Test-Path "$tomlPath.neuron-bak") { Copy-Item "$tomlPath.neuron-bak" $tomlPath -Force }
        Write-Host "   [X] Codex CLI - write failed, restored backup: $_" -ForegroundColor Red
    }
}

# --- MCP registrations ---
# B1 (Piano 05): the Python engine (`neuron register` in src/neuron/clients.py)
# is the single source of truth — JSONC-safe (valid manual snippets), MSIX-aware
# for Claude Desktop, `claude mcp add` for Claude Code (never edits the live
# state file when the CLI is available), verify+rollback, install manifest.
# The legacy PS functions below run only if the engine can't import.
$engineOk = $false
$prevEap = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
try { & $runCmd -c "import neuron.clients" *> $null; if ($LASTEXITCODE -eq 0) { $engineOk = $true } } catch {}
$ErrorActionPreference = $prevEap
if ($engineOk) {
    & $runCmd -m neuron register --client all --slug $Slug --python $runCmd --install-dir $DestDir
    if ($LASTEXITCODE -ne 0) {
        Write-Host "   [!] Some clients need a manual step - see the snippets above (they are valid JSON, safe to paste)." -ForegroundColor DarkYellow
    }
} else {
    Write-Host "   [!] Python register engine not importable - using legacy in-script registration." -ForegroundColor DarkYellow
    # B2: Claude Desktop config may live under the MSIX/Store package instead of %APPDATA%.
    $cdPath = "$env:APPDATA\Claude\claude_desktop_config.json"
    if (-not (Test-Path $cdPath)) {
        $msix = Get-ChildItem "$env:LOCALAPPDATA\Packages\Claude_*" -Directory -ErrorAction SilentlyContinue |
            ForEach-Object { Join-Path $_.FullName 'LocalCache\Roaming\Claude\claude_desktop_config.json' } |
            Where-Object { Test-Path $_ } | Select-Object -First 1
        if ($msix) { $cdPath = $msix; Write-Host "   [i] Claude Desktop: using the MSIX/Store config path" -ForegroundColor DarkGray }
    }
    Register-Mcp -App "Claude Desktop" -Path $cdPath -Entry $mcpEntryStd -Key $Slug
    Register-Mcp -App "Claude Code"    -Path "$env:USERPROFILE\.claude.json"                   -Entry $mcpEntryStd -Key $Slug
    Register-Mcp -App "Cursor"         -Path "$env:USERPROFILE\.cursor\mcp.json"               -Entry $mcpEntryStd -Key $Slug
    Register-McpNested -App "VS Code"  -Path "$env:APPDATA\Code\User\settings.json"            -ParentKeys @('mcp','servers') -Entry @{ type='stdio'; command=$runCmd; args=@('-m','neuron') } -Key $Slug
    $ocPathLegacy = "$env:USERPROFILE\.config\opencode\opencode.json"
    if (Test-Path "$env:USERPROFILE\.config\opencode\opencode.jsonc") { $ocPathLegacy = "$env:USERPROFILE\.config\opencode\opencode.jsonc" }
    Register-McpNested -App "OpenCode" -Path $ocPathLegacy -ParentKeys @('mcp') -Entry @{ command=@($runCmd, '-m','neuron'); type='local' } -Key $Slug
    Register-McpNested -App "Zed"      -Path "$env:APPDATA\Zed\settings.json"                  -ParentKeys @('context_servers') -Entry @{ command=$runCmd; args=@('-m','neuron') } -Key $Slug
    Register-CodexMcp -Vpy $runCmd -Slug $Slug
}
# B6: converge-and-verify — the doctor scans every client config and flags
# stale/duplicate/broken entries (e.g. a leftover key pointing at a dead venv).
if ($engineOk) {
    & $runCmd -m neuron doctor --slug $Slug --python $runCmd --install-dir $DestDir
    if ($LASTEXITCODE -ne 0) {
        Write-Host "   [!] Doctor found problems (above). Repair with:" -ForegroundColor DarkYellow
        Write-Host "       `"$runCmd`" -m neuron doctor --fix --slug $Slug --python `"$runCmd`"" -ForegroundColor White
    }
}
$ocPath = "$env:USERPROFILE\.config\opencode\opencode.json"
if (Test-Path "$env:USERPROFILE\.config\opencode\opencode.jsonc") { $ocPath = "$env:USERPROFILE\.config\opencode\opencode.jsonc" }
Write-Host "   Restart your AI app(s) to activate Neuron." -ForegroundColor DarkGray

# --- OpenCode handshake plugin (deploy + register) ---
$ocDir = Split-Path -Parent $ocPath
$ocPluginSrc = Join-Path $SrcDir "clients\opencode-plugin\neuron-handshake.mjs"
$ocPluginDst = Join-Path $ocDir "plugins\neuron-handshake.mjs"
if ((Test-Path $ocPath) -and (Test-Path $ocPluginSrc)) {
    try {
        if (-not (Test-Path (Split-Path -Parent $ocPluginDst))) { if ($NeuronDebug) { Write-Host "       Creating plugin dir" -ForegroundColor DarkGray }; New-Item -ItemType Directory -Path (Split-Path -Parent $ocPluginDst) -Force | Out-Null }
        Copy-Item $ocPluginSrc $ocPluginDst -Force -ErrorAction Stop
        $raw = Get-Content $ocPath -Raw -ErrorAction SilentlyContinue
        if ($raw -and $raw.Trim()) { $ocCfg = $raw | ConvertFrom-Json -ErrorAction SilentlyContinue }
        if (-not $ocCfg) { $ocCfg = New-Object psobject }
        $plugs = @()
        if ($ocCfg.PSObject.Properties['plugin'] -and $null -ne $ocCfg.plugin) { $plugs = @($ocCfg.plugin) }
        if ($plugs -notcontains $ocPluginDst) { $plugs += $ocPluginDst }
        if ($ocCfg.plugin -ne $plugs) {
            $ocCfg | Add-Member -Force -NotePropertyName 'plugin' -NotePropertyValue $plugs
            [System.IO.File]::WriteAllText($ocPath, ($ocCfg | ConvertTo-Json -Depth 100), [System.Text.UTF8Encoding]::new($false))
        }
        Write-Host "   [OK] OpenCode handshake plugin: $ocPluginDst" -ForegroundColor Green
    } catch { Write-Host "   [!] OpenCode plugin install failed: $_" -ForegroundColor DarkYellow }
}

# --- Claude Code SessionStart hook (deploy + register in ~/.claude/settings.json) ---
$ccHookSrc = Join-Path $SrcDir "clients\claude-code-hook\neuron_sessionstart_hook.py"
$ccHookDir = Join-Path $DestDir "hooks"
$ccHookDst = Join-Path $ccHookDir "neuron_sessionstart_hook.py"
if (Test-Path "$env:USERPROFILE\.claude.json") {
    try {
        if (-not (Test-Path $ccHookDir)) { if ($NeuronDebug) { Write-Host "       Creating hook dir: $ccHookDir" -ForegroundColor DarkGray }; New-Item -ItemType Directory -Path $ccHookDir -Force | Out-Null }
        Copy-Item $ccHookSrc $ccHookDst -Force -ErrorAction Stop
        $hookCmd = "`"$runCmd`" `"$ccHookDst`""
        $settingsPath = "$env:USERPROFILE\.claude\settings.json"
        $sDir = Split-Path -Parent $settingsPath
        if (-not (Test-Path $sDir)) { if ($NeuronDebug) { Write-Host "       Creating settings dir: $sDir" -ForegroundColor DarkGray }; New-Item -ItemType Directory -Path $sDir -Force | Out-Null }
        $sRaw = if (Test-Path $settingsPath) { Get-Content $settingsPath -Raw -ErrorAction SilentlyContinue } else { "" }
        if ($sRaw -and $sRaw.Trim()) { $sCfg = $sRaw | ConvertFrom-Json -ErrorAction SilentlyContinue } else { $sCfg = New-Object psobject }
        if (-not $sCfg) { $sCfg = New-Object psobject }
        if (-not $sCfg.PSObject.Properties['hooks']) { $sCfg | Add-Member -NotePropertyName 'hooks' -NotePropertyValue (New-Object psobject) -Force }
        $sHooks = $sCfg.hooks
        $sStart = @()
        if ($sHooks.PSObject.Properties['SessionStart'] -and $null -ne $sHooks.SessionStart) { $sStart = @($sHooks.SessionStart) }
        foreach ($matcher in @('startup', 'resume', 'clear', 'compact')) {
            $group = $sStart | Where-Object { $_.matcher -eq $matcher } | Select-Object -First 1
            if ($null -eq $group) {
                $sStart += [pscustomobject]@{ matcher = $matcher; hooks = @([pscustomobject]@{ type = 'command'; command = $hookCmd; timeout = 30 }) }
            } else {
                $existing = @($group.hooks)
                $already = $existing | Where-Object { $_.command -eq $hookCmd }
                if (-not $already) { $existing += [pscustomobject]@{ type = 'command'; command = $hookCmd; timeout = 30 }; $group | Add-Member -Force -NotePropertyName 'hooks' -NotePropertyValue $existing }
            }
        }
        $sHooks | Add-Member -Force -NotePropertyName 'SessionStart' -NotePropertyValue $sStart
        $json = ($sCfg | ConvertTo-Json -Depth 100)
        [System.IO.File]::WriteAllText($settingsPath, $json, [System.Text.UTF8Encoding]::new($false))
        Write-Host "   [OK] Claude Code SessionStart hook: $ccHookDst (startup/resume/clear/compact)" -ForegroundColor Green
    } catch { Write-Host "   [!] Claude Code hook install failed: $_" -ForegroundColor DarkYellow }
}

# ===============================================================
# 7. SHORTCUT
# ===============================================================
Write-Host "`n7. Start Menu shortcut..." -ForegroundColor Yellow
$sd = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\$Slug"
if ($NeuronDebug) { Write-Host "  [..] Creating: $sd" -ForegroundColor DarkGray }; New-Item -ItemType Directory -Path $sd -Force | Out-Null
$w = New-Object -ComObject WScript.Shell
$s = $w.CreateShortcut("$sd\$Slug.lnk")
$s.TargetPath = "$venv\Scripts\python.exe"
$s.Arguments = "-m neuron"
$s.WorkingDirectory = $DestDir; $s.Save()

# ===============================================================
# 8. FINAL VERIFICATION
# ===============================================================
Write-Host "`n8. Final verification..." -ForegroundColor Yellow
$vpy = "$venv\Scripts\python.exe"
& $vpy -c "import turso; print('   pyturso OK')";      if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: pyturso not importable" -ForegroundColor Red; exit 1 }
& $vpy -c "from fastembed import TextEmbedding; print('   fastembed OK')"; if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: fastembed missing" -ForegroundColor Red; exit 1 }
& $vpy -c "import mcp; print('   mcp OK')";            if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: mcp missing" -ForegroundColor Red; exit 1 }
& $vpy -c "import neuron; print('   neuron', neuron.__version__)"; if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: neuron not importable" -ForegroundColor Red; exit 1 }

Write-Host "`n=============================================================" -ForegroundColor Green
Write-Host "  Neuron installed into $DestDir" -ForegroundColor Green
Write-Host "  Run:  $venv\Scripts\python.exe -m neuron" -ForegroundColor Green
Write-Host "=============================================================" -ForegroundColor Green
Write-Host "Restart your MCP client (Claude Desktop, Cursor, ...) to activate Neuron."
Write-Host "Manual / emergency install + troubleshooting: INSTALL.md"
