#!/usr/bin/env sh
# Neuron installer/bootstrap for macOS & Linux.
#
# Thin front-door: find (or offer to install) Python 3.10+, install Neuron, then
# hand off to `neuron setup` — the single cross-platform source of truth for
# client registration. Everything past "Python exists" is the Python CLI's job.
#
#   sh install.sh                 # interactive
#   sh install.sh --yes           # non-interactive (assume yes; forwards to setup)
#
# The Windows counterpart is install.ps1 (adds Store-Python handling + winget).
set -eu

SLUG="${NEURON_SLUG:-neuron5}"
ASSUME_YES=0
for a in "$@"; do
    case "$a" in
        -y|--yes) ASSUME_YES=1 ;;
    esac
done

ask() {  # ask "question"  -> 0 for yes. Auto-yes under --yes / non-tty.
    [ "$ASSUME_YES" = "1" ] && return 0
    [ -t 0 ] || return 1
    printf '%s [Y/n] ' "$1"
    read -r ans
    case "$ans" in ""|y|Y|yes|YES|s|si) return 0 ;; *) return 1 ;; esac
}

find_python() {
    for c in python3.12 python3.11 python3.13 python3.10 python3.14 python3; do
        if command -v "$c" >/dev/null 2>&1; then
            v=$("$c" -c 'import sys;print("%d%02d"%sys.version_info[:2])' 2>/dev/null || echo 0)
            if [ "$v" -ge 310 ] 2>/dev/null; then command -v "$c"; return 0; fi
        fi
    done
    return 1
}

install_python() {  # best-effort per-OS
    if command -v brew >/dev/null 2>&1; then
        echo "Installing Python via Homebrew..."; brew install python@3.12
    elif command -v apt-get >/dev/null 2>&1; then
        echo "Installing Python via apt (sudo)..."; sudo apt-get update && sudo apt-get install -y python3 python3-venv python3-pip
    elif command -v dnf >/dev/null 2>&1; then
        echo "Installing Python via dnf (sudo)..."; sudo dnf install -y python3 python3-pip
    elif command -v pacman >/dev/null 2>&1; then
        echo "Installing Python via pacman (sudo)..."; sudo pacman -S --noconfirm python python-pip
    else
        return 1
    fi
}

PY=$(find_python || true)
if [ -z "${PY:-}" ]; then
    echo "No Python 3.10+ was found on this machine."
    if ask "Install Python now?"; then
        install_python || echo "  (no supported package manager found)"
        PY=$(find_python || true)
    fi
fi
if [ -z "${PY:-}" ]; then
    echo "ERROR: no usable Python 3.10+ and none could be installed."
    echo "       Install it from https://python.org/downloads (or your package manager) and re-run."
    exit 1
fi
echo "Using: $PY ($("$PY" --version 2>&1))"

# Locate a shipped wheel + vendored wheels next to this script (optional).
HERE=$(cd "$(dirname "$0")" && pwd)
WHEEL=$(ls "$HERE"/neuron-*.whl 2>/dev/null | head -n1 || true)
TARGET=${WHEEL:-neuron}                 # local wheel if present, else the PyPI name
FINDLINKS=""
[ -d "$HERE/vendor" ] && FINDLINKS="--find-links $HERE/vendor"

if command -v pipx >/dev/null 2>&1; then
    echo "Installing Neuron with pipx..."
    # shellcheck disable=SC2086
    pipx install --force $FINDLINKS "$TARGET"
    NEURON="neuron"
else
    VENV="${NEURON_HOME:-$HOME/.local/share/neuron}/.venv"
    echo "pipx not found — installing into a venv at $VENV ..."
    "$PY" -m venv "$VENV"
    "$VENV/bin/python" -m pip install --upgrade pip >/dev/null
    # shellcheck disable=SC2086
    "$VENV/bin/python" -m pip install $FINDLINKS "$TARGET"
    NEURON="$VENV/bin/python -m neuron"
fi

echo "Registering Neuron in your AI clients..."
# shellcheck disable=SC2086
$NEURON setup --register-all --slug "$SLUG" $([ "$ASSUME_YES" = "1" ] && echo --yes)
echo "Done. Restart your AI apps to load Neuron. Manage it any time with: neuron manage"
echo "To uninstall later: sh $(dirname "$0")/uninstall.sh"

# Create a launcher so the Control Center is always reachable without reinstalling.
BIN_DIR="${NEURON_HOME:-$HOME/.local/share/neuron}"
mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/neuron-gui" <<LAUNCHER
#!/usr/bin/env sh
exec $NEURON gui "\$@"
LAUNCHER
chmod +x "$BIN_DIR/neuron-gui"
echo "Launcher created: $BIN_DIR/neuron-gui"

# Linux .desktop file for the application menu.
if [ "$(uname)" = "Linux" ] && command -v desktop-file-install >/dev/null 2>&1; then
    DESK_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
    mkdir -p "$DESK_DIR"
    cat > "$DESK_DIR/neuron-control-center.desktop" <<DESKTOP
[Desktop Entry]
Name=Neuron Control Center
Comment=Neuron semantic memory — control center
Exec=$BIN_DIR/neuron-gui
Icon=neuron-logo
Terminal=false
Type=Application
Categories=Utility;Development;
DESKTOP
    echo "Desktop entry: $DESK_DIR/neuron-control-center.desktop"
fi

if ask "Open the Control Center now?"; then
    echo "Launching Control Center..."
    $NEURON gui &>/dev/null &
    disown
fi
