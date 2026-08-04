#!/usr/bin/env sh
# Neuron installer (macOS/Linux) — click-and-go, default: Neuron + Gray Matter
# (gateway). One shared venv, registers the gateway, deploys hooks, opens GUI.
#
# Modes:
#   default           → install Neuron + GM (recommended, click-and-go)
#   --no-gm           → standalone (Neuron only, registers directly in clients)
#   -f / --force      → repair mode (pip --force-reinstall --no-deps)
#   -c / --clear      → last resort: delete the venv and rebuild (implies --force).
#                       CODE only — graphs/knowledge.db/bridges are never touched.
#   --embed-model <n> → embedding model to install (skips the prompt).
#                       Default: the 384-dim multilingual MiniLM (EN+IT).
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)

# 0) Parse flags. Default: install with GM (gateway mode). --no-gm = standalone.
WANT_GM=1; FORCE=0; CLEAR=0; MODE="gateway"; EMBED_MODEL=""
_next_is_model=0
for a in "$@"; do
    if [ "$_next_is_model" = "1" ]; then EMBED_MODEL="$a"; _next_is_model=0; continue; fi
    case "$a" in
    --no-gm) WANT_GM=0; MODE="standalone" ;;
    -f|--force) FORCE=1 ;;
    -c|--clear) CLEAR=1; FORCE=1 ;;   # clear is a stronger force
    --embed-model) _next_is_model=1 ;;
    --embed-model=*) EMBED_MODEL="${a#--embed-model=}" ;;
esac; done
FORCE_ARGS=""
[ "$FORCE" = "1" ] && FORCE_ARGS="--force-reinstall --no-deps"
[ "${GM_OPTIN:-1}" = "0" ] && WANT_GM=0 && MODE="standalone"

# Mode selector: click-and-go (Enter = full suite) or explicit --no-gm.
# Only shows in interactive terminals; non-interactive defaults to gateway.
# Picking [N] is NOT the same question as "standalone forever": Neuron can run
# orchestrated by a GM that isn't on disk yet. So [N] asks a second question —
# stay alone, or fetch GM — instead of silently choosing the first for you.
# Returns 0 for [G]: leave WANT_GM=1 and let the normal gateway path run (local
# GM -> fetch -> PyPI). GM's coupled mode then asks about NeuRAG itself, so
# "Neuron + GM without NeuRAG" needs no extra flag here.
read_neuron_only_mode() {
    echo ""
    echo "  Neuron only — which one?"
    echo "    [S] Full standalone — Neuron alone, own venv, registers itself in the clients"
    echo "    [G] Get Gray Matter — download GM next to Neuron, then install orchestrated"
    echo ""
    printf "  Choice [S]: "; read -r sub
    case "$sub" in g|G|gm|GM|get|gray|graymatter|gray-matter|orchestrated) return 0 ;; esac
    return 1
}
if [ "$WANT_GM" = "1" ] && [ -t 0 ] && [ "$FORCE" != "1" ]; then
    echo ""
    echo "  Installation mode:"
    echo "    [F] Full suite — GM + Neuron + NeuRAG (recommended)"
    echo "    [N] Neuron only — standalone, or with GM fetched for you"
    echo "    [D] Details — what you lose without GM"
    echo ""
    printf "  Choice [F]: "; read -r ans
    case "$ans" in
        n|N|no|standalone)
            if read_neuron_only_mode; then MODE="gateway"    # [G]: keep WANT_GM
            else WANT_GM=0; MODE="standalone"; fi ;;
        d|D|details|DETAILS)
            echo ""
            echo "  Without GM you lose:"
            echo "    - Cross-store bridges (Neuron <-> NeuRAG)"
            echo "    - Neighbor auto-surface"
            echo "    - Unified GUI control center"
            echo "    - Auto-registration in MCP clients"
            echo ""
            printf "  Install Full suite? [Y/n] "; read -r ans2
            case "$ans2" in n|N|no|NO)
                if read_neuron_only_mode; then MODE="gateway"    # [G]: keep WANT_GM
                else WANT_GM=0; MODE="standalone"; fi ;;
            esac
            ;;
    esac
fi

# Embedding model choice. The store is EMPTY at install time, which is the only
# moment this is free: vectors from different models are not comparable, so
# changing it later means a full re-embed (scripts/reembed.py). Hence the prompt
# lives here and not only in the GUI.
# dim MUST match the model — models.py guards VECTOR_DIM on the first embed.
# Names/dims/sizes verified against fastembed's list_supported_models().
# Keep in sync with $EmbedModels in install.ps1.
EM_1="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2|384|220 MB|multilingual (EN+IT) - default, best size/quality"
EM_2="sentence-transformers/all-MiniLM-L6-v2|384|90 MB|English only - smallest and fastest"
EM_3="sentence-transformers/paraphrase-multilingual-mpnet-base-v2|768|1.0 GB|multilingual, stronger - 2x storage per vector"
EM_4="intfloat/multilingual-e5-large|1024|2.2 GB|multilingual, best quality - heavy (RAM + disk)"
CHOSEN_MODEL=""; CHOSEN_DIM=""; CHOSEN_SIZE=""
select_embed_model() {
    if [ -n "$EMBED_MODEL" ]; then
        for e in "$EM_1" "$EM_2" "$EM_3" "$EM_4"; do
            [ "${e%%|*}" = "$EMBED_MODEL" ] && { _set_chosen "$e"; return; }
        done
        # Unknown name is not an error: fastembed supports more than we list.
        # dim 0 => discovered from the model itself at write time.
        CHOSEN_MODEL="$EMBED_MODEL"; CHOSEN_DIM=0; CHOSEN_SIZE="?"; return
    fi
    if [ ! -t 0 ] || [ "$FORCE" = "1" ]; then _set_chosen "$EM_1"; return; fi
    echo ""
    echo "  Embedding model (downloaded once, defines the memory's vector space):"
    i=1
    for e in "$EM_1" "$EM_2" "$EM_3" "$EM_4"; do
        _rest="${e#*|}"; _dim="${_rest%%|*}"
        _rest="${_rest#*|}"; _size="${_rest%%|*}"; _note="${_rest#*|}"
        echo "    [$i] $_note"
        echo "        ${e%%|*}  (${_dim}-dim, ${_size})"
        i=$((i + 1))
    done
    echo ""
    echo "  Changing this later requires re-embedding the whole store."
    printf "  Choice [1]: "; read -r mc
    case "$mc" in
        2) _set_chosen "$EM_2" ;;
        3) _set_chosen "$EM_3" ;;
        4) _set_chosen "$EM_4" ;;
        *) _set_chosen "$EM_1" ;;
    esac
}
_set_chosen() {
    CHOSEN_MODEL="${1%%|*}"; _r="${1#*|}"
    CHOSEN_DIM="${_r%%|*}"; _r="${_r#*|}"; CHOSEN_SIZE="${_r%%|*}"
}
# Python bootstrap. No silent-install story on macOS/Linux the way there is on
# Windows (python.org ships no unattended package here, and installing one
# system-wide behind the user's back is not ours to do) — so: accept anything
# cp310..cp314, and when nothing fits print the exact command for THIS machine
# instead of a bare link.
py_ok() {  # $1 = candidate interpreter
    [ -n "$1" ] || return 1
    "$1" -c 'import sys; raise SystemExit(0 if (3,10) <= sys.version_info[:2] <= (3,14) else 1)' >/dev/null 2>&1
}
find_python() {
    for c in python3.14 python3.13 python3.12 python3.11 python3.10 python3 python; do
        _p=$(command -v "$c" 2>/dev/null) || continue
        py_ok "$_p" && { echo "$_p"; return; }
    done
}
ensure_python() {
    _p=$(find_python)
    [ -n "$_p" ] && { echo "$_p"; return; }
    echo "ERROR: Neuron needs Python 3.10-3.14 and none was found." >&2
    if command -v brew >/dev/null 2>&1; then       echo "  Install it with:  brew install python@3.14" >&2
    elif command -v apt-get >/dev/null 2>&1; then  echo "  Install it with:  sudo apt-get install -y python3 python3-venv" >&2
    elif command -v dnf >/dev/null 2>&1; then      echo "  Install it with:  sudo dnf install -y python3" >&2
    elif command -v pacman >/dev/null 2>&1; then   echo "  Install it with:  sudo pacman -S python" >&2
    else                                            echo "  Download it from: https://www.python.org/downloads/" >&2
    fi
    echo "  Then re-run this installer." >&2
    exit 1
}

save_embed_model() {  # $1 = venv python
    # Persisted to the per-user .env (neuron.config.user_env_file), NOT to this
    # shell's env: the MCP client respawns the server from an arbitrary cwd, so
    # a variable here would be gone by the time it matters.
    _vpy="$1"; _dim="$CHOSEN_DIM"
    if [ "$_dim" = "0" ]; then
        # Custom model: ask fastembed for the real width instead of guessing.
        _dim=$("$_vpy" -c "from fastembed import TextEmbedding
print(next((m['dim'] for m in TextEmbedding.list_supported_models() if m['model']=='$CHOSEN_MODEL'), 384))" 2>/dev/null || echo 384)
    fi
    "$_vpy" -c "from neuron.config import set_user_env
print(set_user_env(NS_EMBED_MODEL='$CHOSEN_MODEL', NS_EMBED_DIM='$_dim'))" || {
        echo "  WARNING: could not save the model choice - the multilingual default stays active."; return 0; }
    echo ""
    echo "  Downloading the embedding model ($CHOSEN_SIZE, one-time)."
    echo "  Large models take several minutes - this is NOT frozen."
    # Never fatal (set -e is on): the model is re-fetched lazily on first use,
    # so a failure here costs a delay, not a broken install. HF's tqdm bar
    # redraws with \r and emits no newline, so a piped/logged install shows
    # nothing for the whole download and looks softlocked — bar off, explicit
    # lines instead.
    if HF_HUB_DISABLE_PROGRESS_BARS=1 "$_vpy" -W ignore -c "from neuron.server import _get_embedder
_get_embedder()
print('EMBED_MODEL_READY')" 2>&1 | sed 's/^/    /'; then
        echo "  [OK] $CHOSEN_MODEL cached."
    else
        echo "  [!] download failed - Neuron will retry it on first use (install continues)."
    fi
}

# STANDALONE: only Neuron, its own venv, registers itself in the clients.
# Reversible: re-run without --no-gm and GM takes over (gateway + bridges).
# Also the safety net when GM cannot be obtained (§6: degrade, don't exit).
# Un venv "c'e'" solo se il suo interprete PARTE. `[ -d ]` sulla cartella non e'
# quel test: una rimozione interrotta lascia lib/ e bin/ senza pyvenv.cfg, la
# creazione viene saltata e il primo pip muore con "failed to locate pyvenv.cfg".
# Stessa guardia di Test-VenvHealthy in install.ps1.
venv_healthy() {  # $1 = venv
    [ -f "$1/pyvenv.cfg" ] || return 1
    [ -x "$1/bin/python" ] || return 1
    "$1/bin/python" -c "import sys" >/dev/null 2>&1
}

standalone_install() {
    echo "Installing Neuron STANDALONE (no Gray Matter — add it any time by re-running)."
    # Ask before the long pip phase, write after it (needs the venv's python).
    select_embed_model
    # `exit 1` inside $( ) only kills the subshell — propagate it explicitly
    # rather than relying on set -e to notice the assignment failed.
    PY=$(ensure_python) || exit 1
    VENV="${NEURON_HOME:-$HOME/.local/share/neuron}/.venv"
    # INSTALLER-UX §5.3 — stop what runs from this venv before pip writes to it.
    # POSIX unlinks mapped files happily, so this is not the Windows lock, but a
    # stale server writing to the same store during an upgrade is its own hazard.
    if command -v pkill >/dev/null 2>&1; then
        pkill -f "$VENV" 2>/dev/null || true
        sleep 1
    fi
    if [ "$CLEAR" = "1" ] && [ -d "$VENV" ]; then
        echo "Clear: removing the venv and rebuilding from scratch ($VENV)"
        echo "  (user memory is NOT touched — it lives outside the venv)"
        rm -rf "$VENV"
        [ -d "$VENV" ] && { echo "ERROR: could not remove $VENV — stop any running Neuron process and re-run."; exit 1; }
    fi
    if [ -d "$VENV" ] && ! venv_healthy "$VENV"; then
        echo "Damaged venv detected (pyvenv.cfg missing or interpreter dead) - rebuilding"
        rm -rf "$VENV"
    fi
    [ -d "$VENV" ] || "$PY" -m venv "$VENV" 2>/dev/null || true
    venv_healthy "$VENV" || { echo "ERROR: could not create a working venv at $VENV - check disk space and permissions"; exit 1; }
    VPY="$VENV/bin/python"
    "$VPY" -m pip install --upgrade pip >/dev/null 2>&1 || true
    [ "$FORCE" = "1" ] && echo "Repair: reinstalling Neuron (forced)..."
    FL=""; [ -d "$HERE/vendor" ] && FL="--find-links $HERE/vendor"
    CONS=""; [ -f "$HERE/constraints.txt" ] && CONS="-c $HERE/constraints.txt"  # caps the majors
    # shellcheck disable=SC2086
    "$VPY" -m pip install $FL $CONS $FORCE_ARGS "$HERE" || "$VPY" -m pip install $CONS $FORCE_ARGS "$HERE" \
        || { echo "ERROR: Neuron install failed — check network, or try: pip install --upgrade pip"; exit 1; }
    save_embed_model "$VPY"
    # Handshake assets (standalone has no GM to deploy them). Idempotent.
    _hooks=$("$VPY" -c "import neuron,os;print(os.path.join(os.path.dirname(neuron.__file__),'clients','deploy_hooks.py'))" 2>/dev/null || true)
    [ -n "$_hooks" ] && [ -f "$_hooks" ] && "$VPY" "$_hooks" || true
    # Let the user choose WHERE this registers (see install.ps1).
    # Not a tty => "detected", which never touches an absent client.
    if [ -t 0 ]; then CLIENT_SEL="ask"; else CLIENT_SEL="detected"; fi
    "$VENV/bin/neuron" register --client "$CLIENT_SEL" || true
    "$VENV/bin/neuron" doctor 2>/dev/null || true
    
    # --- GME Registry ---
    # One line instead of ~35 of hand-written JSON: gray_matter/gme.py is the
    # single writer (and the reader). Six shell copies in two languages is what
    # let the PowerShell BOM and the macOS path divergence ship unnoticed.
    # Best-effort — standalone means Gray Matter may be absent.
    "$VPY" -m gray_matter.gme register "$HERE" 2>/dev/null || true
    
    # Desktop icon "Neuron" → apre il control center (bootstrappa GM al 1° click).
    "$VPY" -m neuron gui --shortcut-only 2>/dev/null || true
    NEURON_VER=$("$VENV/bin/neuron" --version 2>/dev/null || echo "?")
    # An explicit, affirmative terminator. Without it the script just stopped
    # producing output and callers (CI, wrappers, a watching user) could not
    # tell "finished successfully" from "still working" or "died quietly".
    echo ""
    echo "  ============================================================"
    echo "  [OK] INSTALL COMPLETE - Neuron $NEURON_VER (standalone)"
    echo "  ============================================================"
    echo "  Embedding model: $CHOSEN_MODEL"
    echo "  Restart your AI apps to load the server."
    echo "  Desktop icon 'Neuron' opens the control center (installs Gray Matter on first click)."
    echo ""
    exit 0
}
[ "$WANT_GM" = "0" ] && standalone_install

# 1) Local GM (bundled or sibling) — zero-network, always the safest path.
for gm in "$HERE/gray_matter" "$HERE/../gray_matter"; do
    [ -f "$gm/install.sh" ] && { GM_PEER_DIR="$HERE" sh "$gm/install.sh" "$@"; gm_exit=$?; [ $gm_exit -eq 0 ] && exit 0; echo "WARNING: GM installer failed (exit $gm_exit), continuing standalone."; }
done

# GM is the required gateway: if missing, fetch it. Safest source first. These
# remote paths activate once Gray Matter is published (GitHub release / PyPI);
# until then they fail cleanly and we print guidance below.
# Bump with every GM release (RELEASE-CHECKLIST) — see the note in install.ps1.
GM_VERSION="${GM_VERSION:-1.4.0}"
GM_REPO="${GM_REPO:-recla93/gray-matter}"
GM_SHA256="${GM_SHA256:-}"          # optional: pin the release tarball checksum
PY=$(find_python)

# 2) Fetch GM as a real SIBLING of this repo — the suite folder that already
#    holds neuron/ (and usually neurag/). Not a private cache inside neuron/:
#    GM discovers its peers as siblings of GM_PEER_DIR's parent, so a GM living
#    under neuron/.gm-bootstrap/ can never see neurag/, and the next re-run
#    re-downloads instead of reusing what is already on disk.
#    git first (updatable, and what a dev wants), tarball as the no-git fallback.
get_gray_matter() {  # echoes the GM dir, or nothing
    _suite=$(cd "$HERE/.." && pwd)
    _target="$_suite/gray_matter"
    [ -f "$_target/install.sh" ] && { echo "$_target"; return; }

    echo "Gray Matter not found locally — fetching it into $_suite (GM is the required gateway)." >&2
    if command -v git >/dev/null 2>&1; then
        echo "  git clone https://github.com/$GM_REPO.git" >&2
        git clone --depth 1 --branch "v$GM_VERSION" "https://github.com/$GM_REPO.git" "$_target" >&2 2>/dev/null || {
            # No such tag (or offline): try the default branch before giving up.
            rm -rf "$_target"
            git clone --depth 1 "https://github.com/$GM_REPO.git" "$_target" >&2 2>/dev/null || true
        }
        [ -f "$_target/install.sh" ] && { echo "$_target"; return; }
        rm -rf "$_target"
        echo "  git clone did not produce a usable checkout — falling back to the release tarball." >&2
    fi

    _tmp="${TMPDIR:-/tmp}/gm-fetch-$$"
    mkdir -p "$_tmp"
    _tgz="$_tmp/gm-$GM_VERSION.tgz"
    _url="https://github.com/$GM_REPO/archive/refs/tags/v$GM_VERSION.tar.gz"
    if command -v curl >/dev/null 2>&1; then curl -fsSL "$_url" -o "$_tgz" || rm -f "$_tgz"
    elif command -v wget >/dev/null 2>&1; then wget -qO "$_tgz" "$_url" || rm -f "$_tgz"
    fi
    [ -f "$_tgz" ] || { rm -rf "$_tmp"; return; }
    if [ -n "$GM_SHA256" ] && command -v sha256sum >/dev/null 2>&1; then
        echo "$GM_SHA256  $_tgz" | sha256sum -c - >/dev/null 2>&1 || {
            rm -rf "$_tmp"
            echo "ERROR: GM checksum mismatch — re-download or unset GM_SHA256 to skip" >&2; exit 1; }
    fi
    tar -xzf "$_tgz" -C "$_tmp"
    _gm=$(find "$_tmp" -maxdepth 1 -type d -name 'gray-matter*' | head -1)
    [ -n "$_gm" ] && mv "$_gm" "$_target" 2>/dev/null || true
    rm -rf "$_tmp"
    [ -f "$_target/install.sh" ] && echo "$_target"
}

GM_DIR=$(get_gray_matter || true)
if [ -n "$GM_DIR" ]; then
    echo "  Gray Matter ready at $GM_DIR"
    GM_PEER_DIR="$HERE" sh "$GM_DIR/install.sh" "$@"; gm_exit=$?
    [ $gm_exit -eq 0 ] && exit 0
    echo "WARNING: GM installer failed (exit $gm_exit), continuing standalone."
fi

# 3) Fallback: PyPI. Install GM into the venv, then drive the gateway install.
if [ -n "$PY" ] && "$PY" -m pip install "gray-matter==$GM_VERSION" >/dev/null 2>&1; then
    "$PY" -m pip install --find-links "$HERE/vendor" "$HERE" >/dev/null 2>&1 || true
    # no exec: a failed gateway install must fall through to the standalone
    # degrade below (§6), not strand the user (keep-in-sync with .ps1 audit fix).
    if command -v gray-matter >/dev/null 2>&1; then
        gray-matter install "$@" && exit 0
    fi
fi

# GM unobtainable → degrade to standalone (§6), don't strand the user.
echo "WARNING: could not obtain Gray Matter (offline, or not yet published)."
echo "Falling back to a STANDALONE Neuron install — re-run this script later to add GM."
standalone_install
