# Neuron installer (Windows) — installs Neuron standalone.
# Gray Matter is RECOMMENDED (not required): with consent it installs the GM
# control center + Neuron, registers the gateway, deploys hooks and opens the
# GUI — one logic, defined once in gray_matter\install.ps1. Decline GM
# (--no-gm / GM_OPTIN=0 / answer 'n') → Neuron installs STANDALONE. §6 opt-out.
#
# Repair mode:  -Force (o -f/--force) → reinstall forzato del PROPRIO pacchetto
# anche a versione invariata (pip --force-reinstall --no-deps); il flag viene
# inoltrato anche al GM installer, che ha lo stesso pattern (keep-in-sync con
# gray_matter\install.ps1).
param([switch]$Force)
$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path

# 0) GM choice (informed consent). Deficit without GM: you LOSE only the
#    cross-store links (bridges) and the neighbor auto-surface; you KEEP memory
#    and every native stimulus (piggyback, flash, spreading).
$WantGm = $true; $AssumeYes = $false
foreach ($a in $args) {
    if ($a -eq "--no-gm") { $WantGm = $false }
    if ($a -eq "-y" -or $a -eq "--yes") { $AssumeYes = $true }
    if ($a -eq "-f" -or $a -eq "--force") { $Force = $true }
}
# Args da inoltrare al GM installer: quelli ricevuti meno le forme -f/--force,
# più il -Force nativo se in repair mode.
$Fwd = @(); foreach ($a in $args) { if ($a -ne "-f" -and $a -ne "--force") { $Fwd += $a } }
if ($Force) { $Fwd += "-Force" }
$ForceArgs = @(); if ($Force) { $ForceArgs = @("--force-reinstall", "--no-deps") }
if ($env:GM_OPTIN -eq "0") { $WantGm = $false }
if ($WantGm -and -not $AssumeYes -and [Environment]::UserInteractive) {
    Write-Host "`nNeuron works standalone; Gray Matter adds cross-store links"
    Write-Host "and neighbor auto-surface. Without GM you keep memory and"
    Write-Host "all native stimuli. Recommended: install it.`n"
    Write-Host "  [S]i — install Neuron + Gray Matter (gateway)"
    Write-Host "  [N]o — standalone (checks if GM is already installed)"
    Write-Host "  [D]etails — what you lose without GM"
    $ans = Read-Host "Choice"
    switch -Regex ($ans) {
        '^(s|si|y|yes|$)' { $WantGm = $true }
        '^(d|details)$' {
            Write-Host "`nWithout GM you lose:"
            Write-Host '  - Cross-store bridges (Neuron <-> NeuRAG)'
            Write-Host "  - Neighbor auto-surface"
            Write-Host "  - Unified GUI control center"
            Write-Host "  - Auto-registration in MCP clients"
            $ans2 = Read-Host "`nInstall GM? [S/n]"
            if ($ans2 -match '^(n|no)$') { $WantGm = $false }
        }
        default { $WantGm = $false }
    }
}

# STANDALONE: only Neuron, its own venv, registers itself in the clients.
# Reversible: re-run without --no-gm and GM takes over (gateway + bridges).
# Also the safety net when GM cannot be obtained (§6: degrade, don't exit).
function Install-Standalone {
    Write-Host "Installing Neuron STANDALONE (no Gray Matter - add it any time by re-running)."
    $py = Get-Command python -ErrorAction SilentlyContinue
    if (-not $py) { $py = Get-Command python3 -ErrorAction SilentlyContinue }
    if (-not $py) { Write-Host "ERROR: need Python 3.10+ - https://www.python.org/downloads/"; exit 1 }
    $Home_ = if ($env:NEURON_HOME) { $env:NEURON_HOME } else { Join-Path $env:LOCALAPPDATA "neuron" }
    $Venv = Join-Path $Home_ ".venv"
    if (-not (Test-Path $Venv)) {
        & $py.Source -m venv $Venv 2>$null
        if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: could not create a venv at $Venv — check disk space and permissions"; exit 1 }
    }
    $Vpy = Join-Path $Venv "Scripts\python.exe"
    & $Vpy -m pip install --upgrade pip 2>$null | Out-Null
    if ($Force) { Write-Host "Repair: reinstalling Neuron (forced)..." }
    $Vendor = Join-Path $Here "vendor"
    if (Test-Path $Vendor) { & $Vpy -m pip install --find-links $Vendor @ForceArgs $Here 2>$null }
    else { & $Vpy -m pip install @ForceArgs $Here 2>$null }
    if ($LASTEXITCODE -ne 0) {
        & $Vpy -m pip install @ForceArgs $Here 2>$null
        if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: Neuron install failed — check network, or try: pip install --upgrade pip"; exit 1 }
    }
    & (Join-Path $Venv "Scripts\neuron.exe") register --client all
    & (Join-Path $Venv "Scripts\neuron.exe") doctor 2>$null
    # Desktop icon "Neuron" → doppio click apre il control center (bootstrappa GM
    # al primo click). Best-effort: non blocca l'install se fallisce.
    try { & $Vpy -m neuron gui --shortcut-only } catch {}
    $NeuronVer = & (Join-Path $Venv "Scripts\neuron.exe") --version 2>$null
    Write-Host "`n  Neuron $NeuronVer — standalone"
    Write-Host "  Restart your AI apps to load the server."
    Write-Host "  Desktop icon 'Neuron' opens the control center (installs Gray Matter on first click)."
    exit 0
}
if (-not $WantGm) { Install-Standalone }

# 1) Local GM (bundled or sibling) — zero-network, always the safest path.
foreach ($gm in @((Join-Path $Here "gray_matter"), (Join-Path (Split-Path -Parent $Here) "gray_matter"))) {
    $inst = Join-Path $gm "install.ps1"
    if (Test-Path $inst) {
        $env:GM_PEER_DIR = $Here
        & powershell -ExecutionPolicy Bypass -File $inst @Fwd
        exit $LASTEXITCODE
    }
}

# GM is the required gateway: if missing, fetch it. Safest source first. These
# remote paths activate once Gray Matter is published (GitHub release / PyPI).
$GmVersion = if ($env:GM_VERSION) { $env:GM_VERSION } else { "1.1.2" }
$GmRepo    = if ($env:GM_REPO)    { $env:GM_REPO }    else { "recla93/gray-matter" }
$GmSha256  = $env:GM_SHA256          # optional: pin the release zip checksum
$Cache     = if ($env:GM_CACHE)   { $env:GM_CACHE }  else { Join-Path $Here ".gm-bootstrap" }
Write-Host "Gray Matter not found locally - bootstrapping it (GM is the required gateway)."
New-Item -ItemType Directory -Force -Path $Cache | Out-Null

# 2) Primary remote: pinned GitHub release zip of the GM repo (immutable tag,
#    TLS, optional SHA256). Reuses the exact same tested install.ps1 pipeline.
$Url = "https://github.com/$GmRepo/archive/refs/tags/v$GmVersion.zip"
$Zip = Join-Path $Cache "gm-$GmVersion.zip"
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $Url -OutFile $Zip -UseBasicParsing
} catch { Remove-Item $Zip -Force -ErrorAction SilentlyContinue }
if (Test-Path $Zip) {
    if ($GmSha256) {
        $h = (Get-FileHash -Algorithm SHA256 $Zip).Hash
        if ($h -ne $GmSha256) { Write-Host "ERROR: GM checksum mismatch — re-download or set `$env:GM_SHA256 to skip"; exit 1 }
    }
    Expand-Archive -Path $Zip -DestinationPath $Cache -Force
    $gm = Get-ChildItem -Directory $Cache -Filter "gray-matter*" | Select-Object -First 1
    if ($gm) {
        $inst = Join-Path $gm.FullName "install.ps1"
        if (Test-Path $inst) {
            $env:GM_PEER_DIR = $Here
            & powershell -ExecutionPolicy Bypass -File $inst @args
            exit $LASTEXITCODE
        }
    }
}

# 3) Fallback: PyPI. Install GM into the venv, then drive the gateway install.
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command python3 -ErrorAction SilentlyContinue }
if ($py) {
    & $py.Source -m pip install "gray-matter==$GmVersion" 2>$null
    if ($LASTEXITCODE -eq 0) {
        & $py.Source -m pip install --find-links (Join-Path $Here "vendor") $Here 2>$null
        $gmcli = Get-Command gray-matter -ErrorAction SilentlyContinue
        # exit only on success: a failed gateway install must fall through to
        # the standalone degrade below (§6), not strand the user (audit fix).
        if ($gmcli) { & gray-matter install @args; if ($LASTEXITCODE -eq 0) { exit 0 } }
    }
}

# GM unobtainable → degrade to standalone (§6), don't strand the user.
Write-Host "WARNING: could not obtain Gray Matter (offline, or not yet published)."
Write-Host "Falling back to a STANDALONE Neuron install - re-run this script later to add GM."
Install-Standalone
