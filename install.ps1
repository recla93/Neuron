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
    [switch]$ForceCompile   # skip the prebuilt-wheel path, go straight to compiling
)

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
        & $psExe -ExecutionPolicy Bypass -File $MyInvocation.MyCommand.Path @PSBoundParameters
        exit $LASTEXITCODE
    }
    # No separate host found - continue in this process (we're already running, so
    # the execution policy clearly allowed it).
}


$ErrorActionPreference = "Continue"
$SrcDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$DestDir = "$env:LOCALAPPDATA\Programs\neuron"
$Vendor  = Join-Path $SrcDir "vendor"     # pre-built pyturso wheels live here

Write-Host "Neuron v3.3 - Installer (wheel-based)" -ForegroundColor Cyan
Write-Host "Source: $SrcDir  ->  Destination: $DestDir`n"

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
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { Write-Host "ERROR: Python not found in PATH. Install Python 3.10-3.14 from python.org." -ForegroundColor Red; exit 1 }
$verOut = python -c "import sys; print(sys.version_info.major, sys.version_info.minor)"
$parts  = $verOut.Trim().Split()
$maj = [int]$parts[0]; $min = [int]$parts[1]
$basePy = (python -c "import sys; print(sys.executable)").Trim()   # the interpreter we validated
Write-Host "   Detected Python $maj.$min : $basePy"
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
New-Item -ItemType Directory -Path $DestDir -Force | Out-Null
$venv = "$DestDir\.venv"
if (-not (Test-Path "$venv\Scripts\python.exe")) {
    Write-Host "   Creating virtual env..." -ForegroundColor Yellow
    python -m venv $venv 2>$null
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
function Invoke-Install {
    param([string[]]$Target, [switch]$AllowVendor, [string]$Name = "Neuron")
    if ($UseUv) {
        $a = @("pip", "install", "--python", $venvPy)
        if ($AllowVendor -and (Test-Path $Vendor)) { $a += @("--find-links", $Vendor) }
        $a += $Target
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
    $a += $Target
    return (Invoke-Pip -Pip $pip -PipArgs $a -Name $Name)
}

$installed = $false
if (-not $ForceCompile) {
    Write-Host "   [a] Prebuilt path (no compiler needed)..." -ForegroundColor Yellow
    $installed = Invoke-Install -Target @($installTarget) -AllowVendor
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
    $installed = Invoke-Install -Target @($installTarget)   # no vendor: build pyturso from source
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
                Invoke-Install -Target @($pkgs[$n]) -Name $pkgs[$n] | Out-Null
            }
        }
    }
} else { Write-Host "`n5. LLM providers skipped (-skipLlmProviders)" -ForegroundColor DarkYellow }

# ===============================================================
# 6. MCP REGISTRATION
# ===============================================================
Write-Host "`n6. MCP Registration..." -ForegroundColor Yellow
$runCmd = "$venv\Scripts\python.exe"
$mcpEntryStd = @{ command = $runCmd; args = @("-m","neuron") }

# --- Claude Desktop ---
$cd = "$env:APPDATA\Claude\claude_desktop_config.json"
if (Test-Path $cd) {
    try {
        $cfg = Get-Content $cd -Raw | ConvertFrom-Json
        if (-not $cfg.mcpServers) { $cfg | Add-Member -NotePropertyName "mcpServers" -NotePropertyValue @{} }
        $cfg.mcpServers | Add-Member -Force -MemberType NoteProperty -Name "neuron" -Value $mcpEntryStd
        $cfg | ConvertTo-Json -Depth 10 | Set-Content $cd -Encoding UTF8
        Write-Host "   [OK] Claude Desktop - restart it to activate"
    } catch { Write-Host "   [!] Claude Desktop config parse error: $_" -ForegroundColor Red }
} else { Write-Host "   [ ] Claude Desktop - config not found" -ForegroundColor DarkYellow }

# --- Cursor ---
$cur = "$env:USERPROFILE\.cursor\mcp.json"
if (Test-Path $cur) {
    try {
        $cfg = Get-Content $cur -Raw | ConvertFrom-Json
        if (-not $cfg.mcpServers) { $cfg | Add-Member -NotePropertyName "mcpServers" -NotePropertyValue @{} }
        $cfg.mcpServers | Add-Member -Force -MemberType NoteProperty -Name "neuron" -Value $mcpEntryStd
        $cfg | ConvertTo-Json -Depth 10 | Set-Content $cur -Encoding UTF8
        Write-Host "   [OK] Cursor (mcp.json)"
    } catch { Write-Host "   [!] Cursor config parse error: $_" -ForegroundColor Red }
} else { Write-Host "   [ ] Cursor - mcp.json not found" -ForegroundColor DarkYellow }

Write-Host "   Other clients (OpenCode, Claude Code, VS Code, Zed, ...): see clients\ and INSTALL.md."

# ===============================================================
# 7. SHORTCUT
# ===============================================================
Write-Host "`n7. Start Menu shortcut..." -ForegroundColor Yellow
$sd = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Neuron"
New-Item -ItemType Directory -Path $sd -Force | Out-Null
$w = New-Object -ComObject WScript.Shell
$s = $w.CreateShortcut("$sd\Neuron.lnk")
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
