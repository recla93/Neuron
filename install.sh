#!/usr/bin/env sh
# Neuron installer (macOS/Linux) — installs Neuron standalone.
# Gray Matter is RECOMMENDED (not required): with consent it installs
# the GM control center + Neuron, registers the gateway, deploys hooks and opens
# the GUI — one logic, defined once in gray_matter/install.sh. If GM is absent
# it bootstraps it (safest source first). Decline GM (--no-gm / GM_OPTIN=0 /
# answer 'n') → Neuron installs STANDALONE and registers itself. §6 opt-out.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)

# 0) GM choice (informed consent). The deficit without GM: you LOSE only the
#    cross-store links (bridges) and the neighbor auto-surface; you KEEP memory,
#    knowledge, and every native stimulus (piggyback, flash, spreading).
# --force: repair mode — reinstall forzato del PROPRIO pacchetto anche a
# versione invariata (pip --force-reinstall --no-deps); "$@" lo inoltra anche
# al GM installer che ha lo stesso pattern (keep-in-sync con gray_matter/install.sh).
WANT_GM=1; ASSUME_YES=0; FORCE=0
for a in "$@"; do case "$a" in
    --no-gm) WANT_GM=0 ;;
    -y|--yes) ASSUME_YES=1 ;;
    -f|--force) FORCE=1 ;;
esac; done
FORCE_ARGS=""
[ "$FORCE" = "1" ] && FORCE_ARGS="--force-reinstall --no-deps"
[ "${GM_OPTIN:-1}" = "0" ] && WANT_GM=0
if [ "$WANT_GM" = "1" ] && [ "$ASSUME_YES" = "0" ] && [ -t 0 ]; then
    echo ""
    echo "Neuron works standalone; Gray Matter adds cross-store links"
    echo "and neighbor auto-surface. Without GM you keep memory and"
    echo "all native stimuli. Recommended: install it."
    echo ""
    echo "  [S]i — install Neuron + Gray Matter (gateway)"
    echo "  [N]o — standalone (checks if GM is already installed)"
    echo "  [D]etails — what you lose without GM"
    printf "Choice: "; read -r ans
    case "$ans" in
        d|D|details|DETAILS)
            echo ""
            echo "Without GM you lose:"
            echo "  - Cross-store bridges (Neuron <-> NeuRAG)"
            echo "  - Neighbor auto-surface"
            echo "  - Unified GUI control center"
            echo "  - Auto-registration in MCP clients"
            printf "Install GM? [S/n] "; read -r ans2
            case "$ans2" in n|N|no|NO) WANT_GM=0 ;; esac
            ;;
        n|N|no|NO) WANT_GM=0 ;;
    esac
fi

# STANDALONE: only Neuron, its own venv, registers itself in the clients.
# Reversible: re-run without --no-gm and GM takes over (gateway + bridges).
# Also the safety net when GM cannot be obtained (§6: degrade, don't exit).
standalone_install() {
    echo "Installing Neuron STANDALONE (no Gray Matter — add it any time by re-running)."
    PY=$(command -v python3 || command -v python || true)
    [ -z "$PY" ] && { echo "ERROR: need Python 3.10+ — https://www.python.org/downloads/"; exit 1; }
    VENV="${NEURON_HOME:-$HOME/.local/share/neuron}/.venv"
    [ -d "$VENV" ] || "$PY" -m venv "$VENV" \
        || { echo "ERROR: could not create a venv at $VENV — check disk space and permissions"; exit 1; }
    VPY="$VENV/bin/python"
    "$VPY" -m pip install --upgrade pip >/dev/null 2>&1 || true
    [ "$FORCE" = "1" ] && echo "Repair: reinstalling Neuron (forced)..."
    FL=""; [ -d "$HERE/vendor" ] && FL="--find-links $HERE/vendor"
    # shellcheck disable=SC2086
    "$VPY" -m pip install $FL $FORCE_ARGS "$HERE" || "$VPY" -m pip install $FORCE_ARGS "$HERE" \
        || { echo "ERROR: Neuron install failed — check network, or try: pip install --upgrade pip"; exit 1; }
    "$VENV/bin/neuron" register --client all || true
    "$VENV/bin/neuron" doctor 2>/dev/null || true
    # Desktop icon "Neuron" → apre il control center (bootstrappa GM al 1° click).
    "$VPY" -m neuron gui --shortcut-only 2>/dev/null || true
    NEURON_VER=$("$VENV/bin/neuron" --version 2>/dev/null || echo "?")
    echo ""
    echo "  Neuron $NEURON_VER — standalone"
    echo "  Restart your AI apps to load the server."
    echo "  Desktop icon 'Neuron' opens the control center (installs Gray Matter on first click)."
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
GM_VERSION="${GM_VERSION:-1.1.2}"
GM_REPO="${GM_REPO:-recla93/gray-matter}"
GM_SHA256="${GM_SHA256:-}"          # optional: pin the release tarball checksum
CACHE="${GM_CACHE:-$HERE/.gm-bootstrap}"
PY=$(command -v python3 || command -v python || true)
echo "Gray Matter not found locally — bootstrapping it (GM is the required gateway)."
mkdir -p "$CACHE"

# 2) Primary remote: pinned GitHub release of the GM repo (immutable tag, TLS,
#    optional SHA256). Reuses the exact same tested install.sh pipeline.
URL="https://github.com/$GM_REPO/archive/refs/tags/v$GM_VERSION.tar.gz"
TGZ="$CACHE/gm-$GM_VERSION.tgz"
if command -v curl >/dev/null 2>&1; then curl -fsSL "$URL" -o "$TGZ" || rm -f "$TGZ"
elif command -v wget >/dev/null 2>&1; then wget -qO "$TGZ" "$URL" || rm -f "$TGZ"
fi
if [ -f "$TGZ" ]; then
    if [ -n "$GM_SHA256" ] && command -v sha256sum >/dev/null 2>&1; then
        echo "$GM_SHA256  $TGZ" | sha256sum -c - || { echo "ERROR: GM checksum mismatch — re-download or set GM_SHA256 to skip"; exit 1; }
    fi
    tar -xzf "$TGZ" -C "$CACHE"
    gm=$(find "$CACHE" -maxdepth 1 -type d -name 'gray-matter*' | head -1)
    [ -n "$gm" ] && [ -f "$gm/install.sh" ] && { GM_PEER_DIR="$HERE" sh "$gm/install.sh" "$@"; gm_exit=$?; [ $gm_exit -eq 0 ] && exit 0; echo "WARNING: GM installer failed (exit $gm_exit), continuing standalone."; }
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
