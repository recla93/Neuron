# Neuron installer (Windows) — click-and-go, default: Neuron + Gray Matter
# (gateway). One shared venv, registers the gateway, deploys hooks, opens GUI.
#
# Modes:
#   default           → install Neuron + GM (recommended, click-and-go)
#   --no-gm           → standalone (Neuron only, registers directly in clients)
#   -Force / --force  → repair mode (pip --force-reinstall --no-deps)
#   -Clear / --clear  → last resort: delete the venv and rebuild (implies -Force).
#                       CODE only — graphs/knowledge.db/bridges are never touched.
#   -EmbedModel <name>  → embedding model to install (skips the prompt).
#                       Default: the 384-dim multilingual MiniLM (EN+IT).
param([switch]$Force, [switch]$Clear, [string]$EmbedModel = "")
$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path

# 0) Parse flags. Default: install with GM (gateway mode). --no-gm = standalone.
$WantGm = $true; $Mode = "gateway"
foreach ($a in $args) {
    if ($a -eq "--no-gm") { $WantGm = $false; $Mode = "standalone" }
    if ($a -eq "-f" -or $a -eq "--force") { $Force = $true }
    if ($a -eq "-c" -or $a -eq "--clear") { $Clear = $true }
}
# Args da inoltrare al GM installer: quelli ricevuti meno le forme -f/--force,
# più il -Force nativo se in repair mode.
$Fwd = @(); foreach ($a in $args) {
    if ($a -notin @("-f", "--force", "-c", "--clear")) { $Fwd += $a }
}
if ($Clear) { $Force = $true }          # clear is a stronger force
if ($Force) { $Fwd += "-Force" }
if ($Clear) { $Fwd += "-Clear" }        # forwarded: GM owns the shared venv
# --no-deps is only safe in repair mode once the shared deps are already in the
# venv. On a fresh venv (first install, -Clear, "clean" repair) the deps are
# missing and --no-deps ships an unusable install: mcp fails to import at first
# run. mcp is the one hard shared dep, so its presence is the gate.
function Test-HasMCP {
    & $Vpy -c "import importlib.util,sys;sys.exit(0 if importlib.util.find_spec('mcp') else 1)"
    return ($LASTEXITCODE -eq 0)
}
function Get-RepairArgs([switch]$Always) {
    if (($Force -or $Always) -and (Test-HasMCP)) { return @("--force-reinstall", "--no-deps") }
    return @()
}
# La versione e' un'ETICHETTA e puo' mentire: un install andato a meta' lascia il
# dist-info nuovo sui file vecchi, e da li' pip risponde "already satisfied" per
# sempre — un fix spedito senza bump non arriva a chi reinstalla. Visto dal vivo:
# 72 file diversi dal sorgente a versione identica. Si chiede al CODICE. Il
# confronto completo lo fa gray_matter quando c'e' (una implementazione sola,
# condivisa con install.sh); standalone si ripiega su etichetta-contro-codice.
function Test-CodeMatches([string]$module, [string]$srcDir) {
    $probe = Join-Path $env:TEMP "gm_drift_$PID.py"
    @"
import sys
mod, src = sys.argv[1], sys.argv[2]
try:
    from gray_matter.executor import install_drift
    sys.exit(0 if install_drift(mod, src)['state'] == 'same' else 1)
except ImportError:
    pass
try:
    import importlib, importlib.metadata as md
    label = md.version(mod.replace('_', '-'))
    body = getattr(importlib.import_module(mod), '__version__', '')
    sys.exit(0 if (not label or not body or label == body) else 1)
except Exception:
    sys.exit(0)
"@ | Set-Content $probe -Encoding ASCII
    & $Vpy -I "$probe" $module $srcDir
    $ok = ($LASTEXITCODE -eq 0)
    Remove-Item -Force $probe -ErrorAction SilentlyContinue
    return $ok
}
if ($env:GM_OPTIN -eq "0") { $WantGm = $false; $Mode = "standalone" }
# -Yes = "don't ask me anything": one gate for EVERY prompt below. Needed by any
# caller without a usable stdin — CI, a scheduled task, a parent process that
# redirects streams. UserInteractive cannot carry this: it describes the session,
# not the console, so it stays TRUE exactly when Read-Host would hang forever.
# GM_YES compared to "1", never truthiness-tested: in PowerShell the string "0"
# is TRUE, so `-not $env:GM_YES` silenced the prompts for whoever set GM_YES=0
# to ask for them (the sh side reads `[ "${GM_YES:-0}" = "1" ]`).
$Ask = ([Environment]::UserInteractive -and -not $Force -and ($env:GM_YES -ne "1") -and ($args -notcontains "-Yes"))

# Mode selector: click-and-go (Enter = full suite) or explicit --no-gm.
# Only shows in interactive sessions; non-interactive defaults to gateway.
# Picking [N] is NOT the same question as "standalone forever": Neuron can run
# orchestrated by a GM that isn't on disk yet. So [N] asks a second question —
# stay alone, or fetch GM — instead of silently choosing the first for you.
# Returns $true for [G]: leave $WantGm set and let the normal gateway path run
# (local GM -> fetch -> PyPI). GM's coupled mode then asks about NeuRAG itself,
# so "Neuron + GM without NeuRAG" needs no extra flag here.
function Read-NeuronOnlyMode {
    Write-Host "`n  Neuron only — which one?"
    Write-Host "    [S] Full standalone — Neuron alone, own venv, registers itself in the clients"
    Write-Host "    [G] Get Gray Matter — download GM next to Neuron, then install orchestrated"
    Write-Host ""
    $a = Read-Host "  Choice [S]"
    return ($a -match '^(g|gm|get|gray|graymatter|gray-matter|orchestrated)$')
}
if ($WantGm -and $Ask) {
    Write-Host "`n  Installation mode:"
    Write-Host "    [F] Full suite — GM + Neuron + NeuRAG (recommended)"
    Write-Host "    [N] Neuron only — standalone, or with GM fetched for you"
    Write-Host "    [D] Details — what you lose without GM"
    Write-Host ""
    $ans = Read-Host "  Choice [F]"
    switch -Regex ($ans) {
        '^(n|no|standalone)$' {
            if (Read-NeuronOnlyMode) { $Mode = "gateway" }   # [G]: keep $WantGm, fall through
            else { $WantGm = $false; $Mode = "standalone" }
        }
        '^(d|details)$' {
            Write-Host "`n  Without GM you lose:"
            Write-Host "    - Cross-store bridges (Neuron <--> NeuRAG)"

            Write-Host "    - Neighbor auto-surface"

            Write-Host "    - Unified GUI control center"

            Write-Host "    - Auto-registration in MCP clients"

            Write-Host ""

            $ans2 = Read-Host "  Install Full suite? [Y/n]"

            if ($ans2 -match '^(n|no)$') {
                if (Read-NeuronOnlyMode) { $Mode = "gateway" }   # [G]: keep $WantGm, fall through
                else { $WantGm = $false; $Mode = "standalone" }
            }
        }
    }
}

# Python bootstrap. A missing interpreter used to end the install with a link
# and exit 1 — the one dependency the installer refused to handle, on the one
# platform where fixing it is easy. Accepts what the vendored pyturso wheels
# support (cp310..cp314); installs the newest 3.14 from python.org otherwise.
$PyMin = [Version]"3.10"; $PyMax = [Version]"3.14"
function Test-PythonOk([string]$exe) {
    if (-not $exe) { return $false }
    # No 2>$null on a native exe: in PS 5.1 that wraps every stderr line in an
    # ErrorRecord and turns it fatal under EAP=Stop, even at exit code 0 — the
    # rule test_no_native_stderr_redirect_in_powershell enforces. Scope the
    # preference instead; a broken candidate just fails the version match.
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $v = & $exe -c "import sys;print('%d.%d'%sys.version_info[:2])"
        if ($LASTEXITCODE -ne 0 -or -not ("$v".Trim() -match '^\d+\.\d+$')) { return $false }
        $ver = [Version]("$v".Trim())
        return ($ver -ge $PyMin -and $ver -le $PyMax)
    } catch { return $false } finally { $ErrorActionPreference = $prevEap }
}
function Find-Python {
    foreach ($c in @("python", "python3")) {
        $g = Get-Command $c -ErrorAction SilentlyContinue
        # Windows ships App Execution Aliases that are 0-byte stubs opening the
        # Store; they resolve via Get-Command but are not an interpreter.
        if ($g -and (Test-PythonOk $g.Source)) { return $g.Source }
    }
    # The py launcher knows about installs that never touched PATH.
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($tag in @("-3.14", "-3.13", "-3.12", "-3.11", "-3.10", "-3")) {
            try {
                $p = & py $tag -c "import sys;print(sys.executable)" 2>$null
                if ($LASTEXITCODE -eq 0 -and (Test-PythonOk $p)) { return $p.Trim() }
            } catch { }
        }
    }
    # Default per-user install location (PrependPath does not affect THIS process).
    foreach ($n in @("Python314", "Python313", "Python312", "Python311", "Python310")) {
        $p = Join-Path $env:LOCALAPPDATA "Programs\Python\$n\python.exe"
        if ((Test-Path $p) -and (Test-PythonOk $p)) { return $p }
    }
    return $null
}
function Install-Python {
    # Resolve the newest 3.14.x from python.org rather than pinning a patch
    # number that may not exist yet (or any more).
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $ver = $env:NEURON_PYTHON_VERSION
    if (-not $ver) {
        try {
            $list = Invoke-WebRequest -Uri "https://www.python.org/ftp/python/" -UseBasicParsing
            $ver = ($list.Links.href | ForEach-Object { $_.TrimEnd('/') } |
                    Where-Object { $_ -match '^3\.14\.\d+$' } |
                    Sort-Object { [Version]$_ } | Select-Object -Last 1)
        } catch { }
    }
    if (-not $ver) { $ver = "3.14.0" }      # offline listing: try the .0 anyway
    $arch = if ([Environment]::Is64BitOperatingSystem) { "amd64" } else { "win32" }
    $url  = "https://www.python.org/ftp/python/$ver/python-$ver-$arch.exe"
    $dst  = Join-Path $env:TEMP "python-$ver-$arch.exe"
    Write-Host "Python not found - installing Python $ver for your user from python.org."
    Write-Host "  $url"
    try { Invoke-WebRequest -Uri $url -OutFile $dst -UseBasicParsing }
    catch { Write-Host "  ERROR: download failed: $($_.Exception.Message)"; return $null }
    Write-Host "  Running the installer (per-user, no admin needed)..."
    # InstallAllUsers=0 keeps it admin-free; Include_pip/tcltk are what Neuron
    # and the control center actually need.
    $p = Start-Process -FilePath $dst -Wait -PassThru -ArgumentList @(
        "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_pip=1",
        "Include_tcltk=1", "Include_test=0", "AssociateFiles=0")
    Remove-Item -Force $dst -ErrorAction SilentlyContinue
    if ($p.ExitCode -ne 0) { Write-Host "  ERROR: the Python installer exited with $($p.ExitCode)."; return $null }
    Write-Host "  Python $ver installed."
    return Find-Python
}
function Ensure-Python {
    $found = Find-Python
    if ($found) { return $found }
    $found = Install-Python
    if ($found) { return $found }
    Write-Host ""
    Write-Host "ERROR: Neuron needs Python $PyMin - $PyMax and it could not be installed automatically."
    Write-Host "  Install it manually, then re-run this installer:"
    Write-Host "  https://www.python.org/downloads/"
    exit 1
}

# Embedding model choice. The store is EMPTY at install time, which is the only
# moment this is free: vectors from different models are not comparable, so
# changing it later means a full re-embed (scripts/reembed.py). Hence the prompt
# lives here and not only in the GUI.
# dim MUST match the model — models.py guards VECTOR_DIM on the first embed.
# Names/dims/sizes verified against fastembed's list_supported_models().
$EmbedModels = @(
    @{ name = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"; dim = 384;  size = "220 MB"; note = "multilingual (EN+IT) - default, best size/quality" },
    @{ name = "sentence-transformers/all-MiniLM-L6-v2";                      dim = 384;  size = "90 MB";  note = "English only - smallest and fastest" },
    @{ name = "sentence-transformers/paraphrase-multilingual-mpnet-base-v2"; dim = 768;  size = "1.0 GB"; note = "multilingual, stronger - 2x storage per vector" },
    @{ name = "intfloat/multilingual-e5-large";                              dim = 1024; size = "2.2 GB"; note = "multilingual, best quality - heavy (RAM + disk)" }
)
function Select-EmbedModel {
    # Explicit -EmbedModel wins; otherwise prompt, defaulting to the multilingual.
    if ($EmbedModel) {
        foreach ($m in $EmbedModels) { if ($m.name -eq $EmbedModel) { return $m } }
        # Unknown name is not an error: fastembed supports more than we list.
        # dim is discovered from the model itself at write time (below).
        return @{ name = $EmbedModel; dim = 0; size = "?"; note = "custom" }
    }
    if (-not $Ask) { return $EmbedModels[0] }
    Write-Host "`n  Embedding model (downloaded once, defines the memory's vector space):"
    for ($i = 0; $i -lt $EmbedModels.Count; $i++) {
        $m = $EmbedModels[$i]
        Write-Host ("    [{0}] {1}" -f ($i + 1), $m.note)
        Write-Host ("        {0}  ({1}-dim, {2})" -f $m.name, $m.dim, $m.size)
    }
    Write-Host ""
    Write-Host "  Changing this later requires re-embedding the whole store."
    $a = Read-Host "  Choice [1]"
    if ($a -match '^[1-9][0-9]*$' -and [int]$a -le $EmbedModels.Count) { return $EmbedModels[[int]$a - 1] }
    return $EmbedModels[0]
}
function Save-EmbedModel([string]$Vpy, $Model) {
    # Persisted to the per-user .env (neuron.config.user_env_file), NOT to this
    # process's env: the MCP client respawns the server from an arbitrary cwd,
    # so a shell variable here would be gone by the time it matters.
    $dim = $Model.dim
    if ($dim -eq 0) {
        # Custom model: ask fastembed for the real width instead of guessing.
        # No 2>$null here: redirecting a native exe's stderr in PS 5.1 wraps
        # each line in an ErrorRecord and turns it fatal under EAP=Stop (the
        # rule test_no_native_stderr_redirect_in_powershell enforces).
        $prevProbeEap = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            $probe = & $Vpy -c "from fastembed import TextEmbedding
print(next((m['dim'] for m in TextEmbedding.list_supported_models() if m['model']=='$($Model.name)'), 384))"
        } catch { $probe = "" } finally { $ErrorActionPreference = $prevProbeEap }
        $dim = if ("$probe".Trim() -match '^\d+$') { [int]"$probe".Trim() } else { 384 }
    }
    # Through the environment, not string-interpolated into the source: a model
    # name with an apostrophe used to close the Python literal and the choice was
    # silently lost (same fix as gray_matter/install.ps1).
    $env:NS_EMBED_NAME_SAVE = $Model.name
    $env:NS_EMBED_DIM_SAVE  = "$dim"
    & $Vpy -c "import os
from neuron.config import set_user_env
print(set_user_env(NS_EMBED_MODEL=os.environ['NS_EMBED_NAME_SAVE'], NS_EMBED_DIM=os.environ['NS_EMBED_DIM_SAVE']))"
    if ($LASTEXITCODE -ne 0) { Write-Host "  WARNING: could not save the model choice - the multilingual default stays active."; return }

    Write-Host "`n  Downloading the embedding model ($($Model.size), one-time)."
    Write-Host "  Large models take several minutes - this is NOT frozen."
    # The download must never take the installer down with it:
    #  * the model is re-fetched lazily on first use anyway, so a failure here
    #    costs a delay, not a broken install;
    #  * fastembed/onnxruntime emit UserWarnings on stderr, and under
    #    ErrorActionPreference=Stop a native command's stderr can surface as a
    #    terminating NativeCommandError — hence the ErrorAction override and
    #    the try/catch, not just an exit-code check;
    #  * HF's tqdm bar redraws with \r and never emits a newline, so the GUI
    #    installer (line-buffered reader) shows NOTHING for the whole download
    #    and looks softlocked. Bar off, explicit lines instead.
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $prevBars = $env:HF_HUB_DISABLE_PROGRESS_BARS
    $env:HF_HUB_DISABLE_PROGRESS_BARS = "1"
    $ok = $false
    try {
        # No redirect at all: output flows straight to the console / the GUI's
        # reader, which is what makes a long download visibly alive.
        & $Vpy -W "ignore" -c "from neuron.server import _get_embedder
_get_embedder()
print('EMBED_MODEL_READY')"
        $ok = ($LASTEXITCODE -eq 0)
    } catch {
        Write-Host "    $($_.Exception.Message)"
    } finally {
        $env:HF_HUB_DISABLE_PROGRESS_BARS = $prevBars
        $ErrorActionPreference = $prevEap
    }
    if ($ok) { Write-Host "  [OK] $($Model.name) cached." }
    else { Write-Host "  [!] download failed - Neuron will retry it on first use (install continues)." }
}

# The console script is a convenience wrapper around `python -m <module>`; it
# is NOT guaranteed to exist. pip can install the package and skip it (a
# --no-deps repair over a half-removed venv, a Scripts/ dir the AV quarantined).
# `& <missing path>` is a TERMINATING error under ErrorActionPreference=Stop, so
# the install died right after pip succeeded: code on disk, nothing registered,
# no shortcut, no manifest. The module form always works if the package
# imports, so fall back to it instead of taking the whole install down.
function Invoke-Tool {
    param([string]$Venv, [string]$Exe, [string]$Module, [Parameter(ValueFromRemainingArguments = $true)]$Rest)
    $path = Join-Path $Venv "Scripts\$Exe"
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        if (Test-Path $path) { & $path @Rest }
        else {
            Write-Host "  ($Exe not found in the venv - using python -m $Module)"
            & (Join-Path (Join-Path $Venv "Scripts") "python.exe") -m $Module @Rest
        }
    } catch {
        Write-Host "  WARNING: $Exe $Rest failed: $($_.Exception.Message)"
    } finally { $ErrorActionPreference = $prevEap }
}

# STANDALONE: only Neuron, its own venv, registers itself in the clients.
# Reversible: re-run without --no-gm and GM takes over (gateway + bridges).
# Also the safety net when GM cannot be obtained (§6: degrade, don't exit).
function Install-Standalone {
    Write-Host "Installing Neuron STANDALONE (no Gray Matter - add it any time by re-running)."
    # Ask before the long pip phase, write after it (needs the venv's python).
    $Chosen = Select-EmbedModel
    $PyExe = Ensure-Python      # installs it from python.org if absent
    # Anche lo standalone vive nella radice UNICA della suite: se domani si
    # aggiunge Gray Matter, i dati sono gia' dove la suite li cerca e non serve
    # traslocare niente. Un'install esistente nella posizione piatta pre-suite
    # continua a essere usata (un venv non e' spostabile).
    $NBase = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $env:USERPROFILE "AppData\Local" }
    $Home_ = if ($env:NEURON_HOME) { $env:NEURON_HOME }
             else { Join-Path (Join-Path $NBase "GrayMatterEnvironment") "neuron" }
    $NLegacy = Join-Path $NBase "neuron"
    if ((Test-Path (Join-Path $NLegacy ".venv")) -and -not (Test-Path (Join-Path $Home_ ".venv"))) {
        $Home_ = $NLegacy
    }
    $Venv = Join-Path $Home_ ".venv"
    # INSTALLER-UX §5.3 — kill what runs from this venv BEFORE pip writes to it.
    # A loaded .pyd cannot be replaced on Windows: pip dies with
    #   [WinError 5] Accesso negato: <venv>/Lib/site-packages/rpds/rpds.cp314-win_amd64.pyd
    # The reap inside `gray_matter.cli install` runs long after every pip.
    # Win32_Process, not Get-Process: `.Path` is null for processes this token
    # cannot open, which hid half of them on a live machine.
    $VenvPids = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.ProcessId -ne $PID -and (
            ($_.ExecutablePath -and $_.ExecutablePath.StartsWith($Venv, [StringComparison]::OrdinalIgnoreCase)) -or
            ($_.CommandLine    -and $_.CommandLine.IndexOf($Venv, [StringComparison]::OrdinalIgnoreCase) -ge 0)
        )
    } | Select-Object -ExpandProperty ProcessId)
    if ($VenvPids.Count -gt 0) {
        Write-Host "Stopping $($VenvPids.Count) running process(es) from this venv (they hold the files pip must replace)..."
        foreach ($p in $VenvPids) { Stop-Process -Id $p -Force -ErrorAction SilentlyContinue }
        Start-Sleep -Milliseconds 800
    }
    # Un venv "c'è" solo se il suo interprete PARTE. Test-Path sulla cartella non
    # è quel test: una Remove-Item che cancella pyvenv.cfg e poi inciampa in un
    # .pyd bloccato lascia Lib\ e Scripts\, la cartella esiste ancora, la
    # creazione viene saltata e il primo pip muore con
    #   python.exe : failed to locate pyvenv.cfg
    # come NativeCommandError grezzo. Visto su una macchina vera.
    function Test-VenvHealthy([string]$VenvPath) {
        if (-not (Test-Path (Join-Path $VenvPath "pyvenv.cfg"))) { return $false }
        $p = Join-Path $VenvPath "Scripts\python.exe"
        if (-not (Test-Path $p)) { return $false }
        & $p -c "import sys" | Out-Null
        return ($LASTEXITCODE -eq 0)
    }
    function Remove-Venv([string]$VenvPath, [string]$why) {
        Write-Host "$why ($VenvPath)"
        Write-Host "  (user memory is NOT touched - it lives outside the venv)"
        Remove-Item -Recurse -Force $VenvPath -ErrorAction SilentlyContinue
        if (Test-Path $VenvPath) {
            Write-Host "ERROR: could not fully remove $VenvPath."
            Write-Host "  Close your AI apps (they respawn the servers) and re-run with -Clear."
            exit 1
        }
    }
    if ($Clear -and (Test-Path $Venv)) {
        Remove-Venv $Venv "Clear: removing the venv and rebuilding from scratch"
    }
    # Un mezzo-venv si ripara, non si eredita: e' tutto il punto.
    if ((Test-Path $Venv) -and -not (Test-VenvHealthy $Venv)) {
        Remove-Venv $Venv "Damaged venv detected (pyvenv.cfg missing or interpreter dead) - rebuilding"
    }
    if (-not (Test-Path $Venv)) {
        & $PyExe -m venv $Venv
        if (-not (Test-VenvHealthy $Venv)) {
            Write-Host "ERROR: could not create a working venv at $Venv - check disk space and permissions"
            exit 1
        }
    }
    $Vpy = Join-Path $Venv "Scripts\python.exe"
    & $Vpy -m pip install --upgrade pip | Out-Null
    if ($Force) { Write-Host "Repair: reinstalling Neuron (forced)..." }
    $Drifted = (-not $Force) -and (-not (Test-CodeMatches "neuron" $Here))
    if ($Drifted) { Write-Host "Neuron: the installed code is NOT this source - forcing a refresh." }
    $Vendor = Join-Path $Here "vendor"
    $Cons = @(); $cf = Join-Path $Here "constraints.txt"
    if (Test-Path $cf) { $Cons = @("-c", $cf) }   # caps the majors — see constraints.txt
    $Repair = if ($Drifted) { Get-RepairArgs -Always } else { Get-RepairArgs }
    if (Test-Path $Vendor) { & $Vpy -m pip install --find-links $Vendor @Cons @Repair $Here }
    else { & $Vpy -m pip install @Cons @Repair $Here }
    if ($LASTEXITCODE -ne 0) {
        & $Vpy -m pip install @Cons @Repair $Here
        if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: Neuron install failed — check network, or try: pip install --upgrade pip"; exit 1 }
    }
    Save-EmbedModel $Vpy $Chosen
    # Handshake assets. GM deploys these when it is the gateway; standalone has
    # no GM, so the tool deploys them itself — otherwise the ONLY channel left is
    # the MCP `instructions` field, which hosts are free to ignore. Idempotent:
    # same files, same paths, and the hook resolves the owner at runtime.
    try {
        $HookSrc = Join-Path $Venv "Lib\site-packages\neuron\clients\deploy_hooks.py"
        if (Test-Path $HookSrc) { & $Vpy $HookSrc }
    } catch { Write-Host "  (handshake assets not deployed: $($_.Exception.Message))" }
    # Let the user choose WHERE this registers. "ask" prompts (detected
    # clients pre-selected, Enter accepts); with no console to ask on it
    # falls back to "detected", which never touches a client the user
    # does not have. -Yes / non-interactive never prompts.
    $ClientSel = if ($Ask) { "ask" } else { "detected" }
    Invoke-Tool $Venv "neuron.exe" "neuron" register --client $ClientSel
    Invoke-Tool $Venv "neuron.exe" "neuron" doctor
    
    # --- GME Registry ---
    # One line instead of ~30 of hand-written JSON: gray_matter/gme.py is the
    # single writer (and the reader). Six shell copies in two languages is what
    # let the PowerShell BOM and the macOS path divergence ship unnoticed.
    # Best-effort — standalone means Gray Matter may be absent, and then there
    # is no registry to write and nothing that would read it.
    try { & $Vpy -m gray_matter.gme register "$Here" } catch { }
    
    # Desktop icon "Neuron" → doppio click apre il control center (bootstrappa GM
    # al primo click). Best-effort: non blocca l'install se fallisce.
    try { & $Vpy -m neuron gui --shortcut-only } catch {}
    $NeuronVer = (Invoke-Tool $Venv "neuron.exe" "neuron" --version | Select-Object -Last 1)
    if (-not "$NeuronVer".Trim()) { $NeuronVer = "?" }   # never print a blank version
    # An explicit, affirmative terminator. Without it the script just stopped
    # producing output and callers (GUI installer, install.cmd, CI) could not
    # tell "finished successfully" from "still working" or "died quietly".
    Write-Host ""
    Write-Host "  ============================================================"
    Write-Host "  [OK] INSTALL COMPLETE - Neuron $NeuronVer (standalone)"
    Write-Host "  ============================================================"
    Write-Host "  Embedding model: $($Chosen.name)"
    Write-Host "  Restart your AI apps to load the server."
    Write-Host "  Desktop icon 'Neuron' opens the control center (installs Gray Matter on first click)."
    Write-Host ""
    exit 0
}
if (-not $WantGm) { Install-Standalone }

# 1) Local GM (bundled or sibling) — zero-network, always the safest path.
foreach ($gm in @((Join-Path $Here "gray_matter"), (Join-Path (Split-Path -Parent $Here) "gray_matter"))) {
    $inst = Join-Path $gm "install.ps1"
    if (Test-Path $inst) {
        $env:GM_PEER_DIR = $Here
        & powershell -ExecutionPolicy Bypass -File $inst @Fwd
        if ($LASTEXITCODE -eq 0) { exit 0 }
        Write-Host "WARNING: GM installer failed (exit $LASTEXITCODE), continuing with the fallback paths."
        break
    }
}

# GM is the required gateway: if missing, fetch it. Safest source first. These
# remote paths activate once Gray Matter is published (GitHub release / PyPI).
# Bump with every GM release (RELEASE-CHECKLIST): a stale default here clones an
# old GM whose pins may not match this Neuron, which is exactly the venv skew the
# pip check in GM's installer now reports.
$GmVersion = if ($env:GM_VERSION) { $env:GM_VERSION } else { "1.4.2" }
$GmRepo    = if ($env:GM_REPO)    { $env:GM_REPO }    else { "recla93/gray-matter" }
$GmSha256  = $env:GM_SHA256          # optional: pin the release zip checksum

# 2) Fetch GM as a real SIBLING of this repo — the suite folder that already
#    holds neuron/ (and usually neurag/). Not a private cache inside neuron/:
#    GM discovers its peers as siblings of GM_PEER_DIR's parent, so a GM living
#    under neuron/.gm-bootstrap/ can never see neurag/, and the next re-run
#    re-downloads instead of reusing what is already on disk.
#    git first (updatable, and what a dev wants), zip as the no-git fallback.
function Get-GrayMatter {
    $suite  = Split-Path -Parent $Here
    $target = Join-Path $suite "gray_matter"
    if (Test-Path (Join-Path $target "install.ps1")) { return $target }

    Write-Host "Gray Matter not found locally - fetching it into $suite (GM is the required gateway)."
    if (Get-Command git -ErrorAction SilentlyContinue) {
        Write-Host "  git clone https://github.com/$GmRepo.git"
        # NO `2>&1` on git: it writes normal progress ("Cloning into...") to
        # stderr, and PS 5.1 turns each redirected stderr line into a scary
        # NativeCommandError on a clone that actually succeeded. Unredirected,
        # it just prints. Success is decided by $LASTEXITCODE, not by stderr.
        & git clone --depth 1 --branch "v$GmVersion" "https://github.com/$GmRepo.git" $target
        if ($LASTEXITCODE -ne 0) {
            # No such tag (or offline): try the default branch before giving up.
            Remove-Item -Recurse -Force $target -ErrorAction SilentlyContinue
            & git clone --depth 1 "https://github.com/$GmRepo.git" $target
        }
        if (Test-Path (Join-Path $target "install.ps1")) { return $target }
        Remove-Item -Recurse -Force $target -ErrorAction SilentlyContinue
        Write-Host "  git clone did not produce a usable checkout - falling back to the release zip."
    }

    $tmp = Join-Path $env:TEMP "gm-fetch-$PID"
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null
    $Zip = Join-Path $tmp "gm-$GmVersion.zip"
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri "https://github.com/$GmRepo/archive/refs/tags/v$GmVersion.zip" -OutFile $Zip -UseBasicParsing
    } catch { Remove-Item $Zip -Force -ErrorAction SilentlyContinue }
    if (-not (Test-Path $Zip)) { Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue; return $null }
    if ($GmSha256) {
        $h = (Get-FileHash -Algorithm SHA256 $Zip).Hash
        if ($h -ne $GmSha256) {
            Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
            Write-Host "ERROR: GM checksum mismatch — re-download or unset `$env:GM_SHA256 to skip"; exit 1
        }
    }
    Expand-Archive -Path $Zip -DestinationPath $tmp -Force
    $gm = Get-ChildItem -Directory $tmp -Filter "gray-matter*" | Select-Object -First 1
    if ($gm) { Move-Item -Path $gm.FullName -Destination $target -Force -ErrorAction SilentlyContinue }
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    if (Test-Path (Join-Path $target "install.ps1")) { return $target }
    return $null
}

$GmDir = Get-GrayMatter
if ($GmDir) {
    Write-Host "  Gray Matter ready at $GmDir"
    $env:GM_PEER_DIR = $Here
    & powershell -ExecutionPolicy Bypass -File (Join-Path $GmDir "install.ps1") @Fwd
    if ($LASTEXITCODE -eq 0) { exit 0 }
    Write-Host "WARNING: GM installer failed (exit $LASTEXITCODE), continuing with the fallback paths."
}

# 3) Fallback: PyPI. Install GM into the venv, then drive the gateway install.
$PyExe = Find-Python        # PyPI fallback: use it only if already present
if ($PyExe) {
    & $PyExe -m pip install "gray-matter==$GmVersion"
    if ($LASTEXITCODE -eq 0) {
        & $PyExe -m pip install --find-links (Join-Path $Here "vendor") $Here
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


