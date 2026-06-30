<#
.SYNOPSIS
    Neuron v3.3 — Installer Windows
.DESCRIPTION
    Python -> Rust -> Windows SDK -> MSVC Build Tools (minimal) -> pip (3 retry, hard fail).
    Each download tries 3 different URLs before giving up.
    If MSVC fails after 3 attempts, activates GNU toolchain (MinGW).
    No fallback on Python dependencies: if mcp/fastembed/pyturso fails, it exits.
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File install.ps1
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File install.ps1 -skipLlmProviders
#>

# Self-reinvoke with ExecutionPolicy Bypass
if ($MyInvocation.MyCommand.Path -and -not ($env:__NEURON_BYPASS)) { $env:__NEURON_BYPASS='1'; powershell -ExecutionPolicy Bypass -File $MyInvocation.MyCommand.Path @PSBoundParameters; exit $LASTEXITCODE }

param([switch]$skipLlmProviders)

$ErrorActionPreference = "Continue"
$SrcDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$DestDir = "$env:LOCALAPPDATA\Programs\neuron"

Write-Host "Neuron v3.3 — Installer" -ForegroundColor Cyan
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
# Helper: pip retry (3 attempts, hard fail)
# ---------------------------------------------------------------
function Invoke-PipRetry {
    param([scriptblock]$ScriptBlock, [string]$Name)
    for ($a = 1; $a -le 3; $a++) {
        if ($a -gt 1) { Write-Host "   Attempt $a/3..." -ForegroundColor DarkYellow; Start-Sleep -Seconds 3 }
        $null = & $ScriptBlock 2>$null
        if ($LASTEXITCODE -eq 0 -or $LASTEXITCODE -eq $null) { Write-Host "   $Name OK" -ForegroundColor Green; return }
    }
    Write-Host "ERROR: $Name after 3 attempts" -ForegroundColor Red
    exit 1
}

# ===============================================================
# 1. PYTHON
# ===============================================================
Write-Host "1. Python >= 3.10..." -ForegroundColor Yellow
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { Write-Host "ERROR: Python not found" -ForegroundColor Red; exit 1 }
$ver = python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ([double]$ver -lt 3.10) { Write-Host "ERROR: Python $ver < 3.10" -ForegroundColor Red; exit 1 }
Write-Host "   Python $ver : $(python -c 'import sys; print(sys.executable)')"

# ===============================================================
# 2. RUST (3 URL fallback)
# ===============================================================
Write-Host "`n2. Rust toolchain..." -ForegroundColor Yellow
if (Get-Command rustc -ErrorAction SilentlyContinue) {
    Write-Host "   Already installed: $(rustc --version)"
    Write-Host "   Toolchain: $(rustup default 2>$null)"
} else {
    Write-Host "   Download rustup-init.exe..." -ForegroundColor Yellow
    $ok = Download-File -Urls @(
        "https://win.rustup.rs/x86_64",
        "https://static.rust-lang.org/rustup/dist/x86_64-pc-windows-msvc/rustup-init.exe",
        "https://github.com/rust-lang/rustup/raw/main/rustup-init.sh"
    ) -OutFile "$env:TEMP\rustup-init.exe" -Name "rustup"
    if (-not $ok) { Write-Host "ERROR: rustup not downloadable" -ForegroundColor Red; exit 1 }
    Write-Host "   Running rustup..." -ForegroundColor Yellow
    Start-Process -Wait "$env:TEMP\rustup-init.exe" -ArgumentList @("-y", "--default-toolchain", "stable", "--profile", "minimal")
    $env:Path = "{0};{1}" -f ([Environment]::GetEnvironmentVariable("Path","Machine")), ([Environment]::GetEnvironmentVariable("Path","User"))
    if (-not (Get-Command rustc -ErrorAction SilentlyContinue)) { Write-Host "ERROR: Rust not found after installation" -ForegroundColor Red; exit 1 }
    Write-Host "   Installed: $(rustc --version)"
}

# ===============================================================
# 3. WINDOWS SDK + MSVC BUILD TOOLS (con GNU fallback)
# ===============================================================
Write-Host "`n3. Windows SDK + MSVC Build Tools..." -ForegroundColor Yellow

$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$msvcOk = $false
if (Test-Path $vswhere) {
    $vsInfo = & $vswhere -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -format json 2>$null | ConvertFrom-Json
    if ($vsInfo) { $msvcOk = $true }
}

if ($msvcOk) {
    Write-Host "   VS Build Tools already present." -ForegroundColor Green
} else {
    Write-Host "   VS Build Tools not found. Downloading Windows SDK..." -ForegroundColor Yellow
    $ok = Download-File -Urls @(
        "https://go.microsoft.com/fwlink/?linkid=2120843",
        "https://download.microsoft.com/download/2/7/f/27f0fc56-0ca3-465a-92a5-477f4c1cf509/windowssdk/winsdksetup.exe"
    ) -OutFile "$env:TEMP\winsdksetup.exe" -Name "Windows SDK"
    if ($ok) {
        Write-Host "   Installing Windows SDK..." -ForegroundColor Yellow
        Start-Process "$env:TEMP\winsdksetup.exe" -ArgumentList "/q", "/norestart" -Wait
        Write-Host "   Windows SDK installed." -ForegroundColor Green
    } else {
        Write-Host "   Windows SDK not downloadable, proceeding without..." -ForegroundColor DarkYellow
    }

    $msvcInstalled = $false
    for ($msvcAttempt = 1; $msvcAttempt -le 3; $msvcAttempt++) {
        if ($msvcAttempt -gt 1) { Write-Host "   MSVC attempt $msvcAttempt/3..." -ForegroundColor DarkYellow }
        Write-Host "   Downloading MSVC Build Tools (attempt $msvcAttempt)..." -ForegroundColor Yellow
        $ok = Download-File -Urls @(
            "https://aka.ms/vs/17/release/vs_BuildTools.exe",
            "https://download.visualstudio.microsoft.com/download/pr/neuron/vs_BuildTools.exe"
        ) -OutFile "$env:TEMP\vs_BuildTools.exe" -Name "MSVC Build Tools"
        if (-not $ok) { continue }

        Write-Host "   Installing MSVC C++ tools (compiler + UCRT only)..." -ForegroundColor Yellow
        Start-Process "$env:TEMP\vs_BuildTools.exe" -ArgumentList "--quiet", "--wait", "--norestart",
            "--add", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
            "--add", "Microsoft.VisualStudio.Component.VC.Runtime.UCRTSDK" -Wait -NoNewWindow

        $env:Path = "{0};{1}" -f ([Environment]::GetEnvironmentVariable("Path","Machine")), ([Environment]::GetEnvironmentVariable("Path","User"))
        if (Test-Path $vswhere) {
            $vsCheck = & $vswhere -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -format json 2>$null | ConvertFrom-Json
            if ($vsCheck) { $msvcInstalled = $true; Write-Host "   MSVC Build Tools installed." -ForegroundColor Green; break }
        }
        Write-Host "   MSVC installation failed (attempt $msvcAttempt/3)" -ForegroundColor DarkYellow
    }

    if (-not $msvcInstalled) {
        Write-Host "   MSVC not available after 3 attempts. Falling back to GNU toolchain (MinGW)..." -ForegroundColor Yellow
        rustup toolchain install stable-gnu 2>$null
        rustup default stable-gnu 2>$null
        $default = rustup default 2>$null
        if ($default -notmatch "gnu") {
            Write-Host "ERROR: neither MSVC nor GNU available. Install manually:" -ForegroundColor Red
            Write-Host "  https://visualstudio.microsoft.com/visual-cpp-build-tools/" -ForegroundColor DarkYellow
            exit 1
        }
        Write-Host "   GNU toolchain activated: $default" -ForegroundColor Green
    }
}

# ===============================================================
# 4. VENV + PIP (order: MCP -> fastembed -> pyturso)
# ===============================================================
Write-Host "`n4. Virtual env..." -ForegroundColor Yellow
New-Item -ItemType Directory -Path $DestDir -Force | Out-Null
$venv = "$DestDir\.venv"
if (-not (Test-Path "$venv\Scripts\python.exe")) {
    Write-Host "   Creating virtual env..." -ForegroundColor Yellow
    python -m venv $venv
}
$pip = "$venv\Scripts\pip.exe"

Write-Host "`n5. Python dependencies (3 attempts, hard fail)..." -ForegroundColor Yellow

# pip index fallback: try PyPI, then official EU mirror
$pipBase = "& `"$pip`" install --timeout 60 --retries 3"
Write-Host "   [a] MCP SDK..." -ForegroundColor Yellow
Invoke-PipRetry -ScriptBlock { & cmd /c "`"$pip`" install --timeout 60 --retries 3 `"mcp>=1.28.0`" 2>&1" } -Name "MCP SDK"

Write-Host "   [b] fastembed (384-dim)..." -ForegroundColor Yellow
Invoke-PipRetry -ScriptBlock { & cmd /c "`"$pip`" install --timeout 120 --retries 3 `"fastembed>=0.5.0`" 2>&1" } -Name "fastembed"

Write-Host "   [c] pyturso (Turso DB, Rust compilation)..." -ForegroundColor Yellow
Invoke-PipRetry -ScriptBlock { & cmd /c "`"$pip`" install --timeout 180 --retries 3 `"pyturso>=0.6.1`" 2>&1" } -Name "pyturso"

# ===============================================================
# 6. LLM PROVIDERS (optional)
# ===============================================================
if (-not $skipLlmProviders) {
    Write-Host "`n6. Optional LLM providers..." -ForegroundColor Yellow
    Write-Host "   Note: these packages are ONLY needed for standalone chat"
    Write-Host "   (python -m scripts.run_interactive). NOT required for the MCP server"
    Write-Host "   used by OpenCode."
    Write-Host ""
    Write-Host "   Legend:"
    Write-Host "     [0] None  — install only the MCP server (recommended if using OpenCode)"
    Write-Host "     [1] Ollama   — local LLMs (no API key, requires Ollama installed)"
    Write-Host "     [2] OpenAI   — API key via OPENAI_API_KEY or ~/.neuron/config.json"
    Write-Host "     [3] Anthropic — API key via ANTHROPIC_API_KEY or ~/.neuron/config.json"
    Write-Host "     [4] Gemini   — API key via GEMINI_API_KEY or ~/.neuron/config.json"
    Write-Host ""
    $c = Read-Host "   Choose (0 = default, comma for multiple)"
    if ($c -ne "" -and $c -ne "0") {
        $pkgs = @([ordered]@{L="Ollama (local, no key)";P="ollama"},@{L="OpenAI (API key)";P="openai"},@{L="Anthropic (API key)";P="anthropic"},@{L="Gemini (API key)";P="google-generativeai"})
        foreach ($idx in ($c -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' })) {
            $n = [int]$idx - 1
            if ($n -ge 0 -and $n -lt $pkgs.Count) {
                Write-Host "   $($pkgs[$n].L)..."
                Invoke-PipRetry -ScriptBlock { & cmd /c "`"$pip`" install --timeout 60 --retries 3 `"$($pkgs[$n].P)`" 2>&1" } -Name $pkgs[$n].L
            }
        }
    }
} else { Write-Host "`n6. LLM providers skipped (-skipLlmProviders)" -ForegroundColor DarkYellow }

# ===============================================================
# 7. COPY FILES
# ===============================================================
Write-Host "`n7. Copying files..." -ForegroundColor Yellow
Get-ChildItem "$SrcDir\*" -Recurse -File | Where-Object {
    $r = $_.FullName.Substring($SrcDir.Length + 1)
    $r -notlike "opencode.example.json" -and $r -notlike ".git*" -and $r -notlike ".idea*" -and $r -notlike "*.pyc" -and $r -notlike "__pycache__*"
} | ForEach-Object {
    $d = Join-Path $DestDir $_.FullName.Substring($SrcDir.Length + 1)
    $p = Split-Path $d -Parent
    if (-not (Test-Path $p)) { New-Item -ItemType Directory -Path $p -Force | Out-Null }
    Copy-Item $_.FullName $d -Force
}
Write-Host "   Copied to $DestDir"

# ===============================================================
# 8. MCP REGISTRATION (all supported clients)
# ===============================================================
Write-Host "`n8. MCP Registration..." -ForegroundColor Yellow
$mcpEntry = @{ command = @("cmd", "/c", "$DestDir\scripts\run_mcp.bat"); type = "local" }
$mcpEntryStd = @{ command = "cmd"; args = @("/c", "$DestDir\scripts\run_mcp.bat") }

# --- OpenCode ---
$oc = "$env:USERPROFILE\.config\opencode\opencode.json"
if (Test-Path $oc) {
    $cfg = Get-Content $oc -Raw | ConvertFrom-Json
    if (-not $cfg.mcp) { $cfg | Add-Member -NotePropertyName "mcp" -NotePropertyValue @{} }
    $cfg.mcp | Add-Member -Force -MemberType NoteProperty -Name "neuron" -Value $mcpEntry
    # Add skill instruction
    if (-not $cfg.instructions) { $cfg | Add-Member -NotePropertyName "instructions" -NotePropertyValue @() }
    $skillPath = "$DestDir\skills\auto-context.md"
    if ($cfg.instructions -notcontains $skillPath) {
        $cfg.instructions += $skillPath
    }
    $cfg | ConvertTo-Json -Depth 10 | Set-Content $oc -Encoding UTF8
    Write-Host "   [OK] OpenCode (opencode.json)"
} else { Write-Host "   [ ] OpenCode — opencode.json not found" -ForegroundColor DarkYellow }

# --- Claude Desktop ---
$cd = "$env:APPDATA\Claude\claude_desktop_config.json"
if (Test-Path $cd) {
    try {
        $cfg = Get-Content $cd -Raw | ConvertFrom-Json
        if (-not $cfg.mcpServers) { $cfg | Add-Member -NotePropertyName "mcpServers" -NotePropertyValue @{} }
        $cfg.mcpServers | Add-Member -Force -MemberType NoteProperty -Name "neuron" -Value $mcpEntryStd
        $cfg | ConvertTo-Json -Depth 10 | Set-Content $cd -Encoding UTF8
        Write-Host "   [OK] Claude Desktop (claude_desktop_config.json) — restart Claude Desktop to activate"
    } catch { Write-Host "   [!] Claude Desktop config parse error: $_" -ForegroundColor Red }
} else { Write-Host "   [ ] Claude Desktop — config not found (install Claude Desktop first)" -ForegroundColor DarkYellow }

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
} else { Write-Host "   [ ] Cursor — mcp.json not found (run Cursor once first)" -ForegroundColor DarkYellow }

Write-Host "   Auto-registered: OpenCode, Claude Desktop, Cursor (restart them to activate)."
Write-Host "   Manual setup (one-time):"
Write-Host "     - Claude Code, Cline/Roocode, VS Code, Windsurf, Zed, Continue.dev, Cody, Amazon Q"
Write-Host "       -> see clients\ folder for ready-made config snippets, or DEVELOPER.md."
Write-Host "     - Perplexity (macOS only): Settings > Connectors > local MCP (command 'python3 -m neuron')."
Write-Host "     - ChatGPT / OpenAI: needs a stdio->HTTP bridge (mcp-remote) + a remote connector URL."
Write-Host "   Full per-client instructions: DEVELOPER.md (MCP Client Configuration)."

# ===============================================================
# 9. SHORTCUT
# ===============================================================
Write-Host "`n9. Start Menu shortcut..." -ForegroundColor Yellow
$sd = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Neuron"
New-Item -ItemType Directory -Path $sd -Force | Out-Null
$w = New-Object -ComObject WScript.Shell
$s = $w.CreateShortcut("$sd\Neuron.lnk")
$s.TargetPath = "$venv\Scripts\python.exe"
$s.Arguments = "-m neuron"
$s.WorkingDirectory = $DestDir; $s.Save()

# ===============================================================
# 10. FINAL VERIFICATION
# ===============================================================
Write-Host "`n10. Final verification..." -ForegroundColor Yellow
& "$venv\Scripts\python.exe" -c "import turso; print('   pyturso OK')" 2>$null
if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: pyturso not installed" -ForegroundColor Red; exit 1 }
& "$venv\Scripts\python.exe" -c "from fastembed import TextEmbedding; print('   fastembed OK')" 2>$null
if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: fastembed not installed" -ForegroundColor Red; exit 1 }
& "$venv\Scripts\python.exe" -c "import mcp; print('   mcp OK')" 2>$null
if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: mcp not installed" -ForegroundColor Red; exit 1 }

Write-Host "`n=============================================================" -ForegroundColor Green
Write-Host "  Neuron v3.3 installed!" -ForegroundColor Green
Write-Host "  Engine: Turso DB | Embedding: 384-dim semantic" -ForegroundColor Green
Write-Host "  Path: $DestDir" -ForegroundColor Green
Write-Host "=============================================================" -ForegroundColor Green
Write-Host "`nCommands:"
Write-Host "  Server MCP:  scripts\run_mcp.bat"
Write-Host "  Check:       powershell -ExecutionPolicy Bypass -File scripts\check.ps1"
Write-Host "  Repair:      powershell -ExecutionPolicy Bypass -File scripts\check.ps1 -Repair"
Write-Host "Restart any registered MCP client (Claude Desktop, OpenCode, Cursor) to activate Neuron."
Write-Host "Other clients (incl. Perplexity macOS, ChatGPT via bridge): see DEVELOPER.md > MCP Client Configuration."
