#!/usr/bin/env sh
# Neuron uninstaller for macOS & Linux.
#
# Removes the Control Center launcher, .desktop entry (Linux), and optionally
# the venv + memory data. Client deregistration is handled by `neuron setup
# --uninstall` (called automatically if `neuron` is on PATH).
#
#   sh uninstall.sh                 # interactive (asks before each step)
#   sh uninstall.sh --yes           # non-interactive (skip prompts, keep data)
#   sh uninstall.sh --purge-data    # also delete the memory store
set -eu

SLUG="${NEURON_SLUG:-neuron5}"
ASSUME_YES=0
PURGE_DATA=0
for a in "$@"; do
    case "$a" in
        -y|--yes)        ASSUME_YES=1 ;;
        --purge-data)    PURGE_DATA=1 ;;
    esac
done

ask() {
    [ "$ASSUME_YES" = "1" ] && return 0
    [ -t 0 ] || return 1
    printf '%s [y/N] ' "$1"
    read -r ans
    case "$ans" in y|Y|yes|YES|s|si|S) return 0 ;; *) return 1 ;; esac
}

NEURON_HOME="${NEURON_HOME:-$HOME/.local/share/neuron}"
VENV="$NEURON_HOME/.venv"
LAUNCHER="$NEURON_HOME/neuron-gui"
DESK_FILE="${XDG_DATA_HOME:-$HOME/.local/share}/applications/neuron-control-center.desktop"
GRAPHS_DIR="$NEURON_HOME/graphs"

echo "Neuron uninstaller"
echo "=================="
echo ""
echo "The following will be checked for removal:"
[ -d "$VENV" ]       && echo "  - venv:              $VENV"
[ -f "$LAUNCHER" ]   && echo "  - launcher:          $LAUNCHER"
[ -f "$DESK_FILE" ]  && echo "  - desktop entry:     $DESK_FILE"
[ -d "$GRAPHS_DIR" ] && echo "  - memory store:      $GRAPHS_DIR"
echo ""

# 1) Deregister from AI clients (via neuron setup --uninstall).
NEURON=""
for c in neuron "$VENV/bin/neuron" "$VENV/bin/python -m neuron"; do
    if command -v "$c" >/dev/null 2>&1 || [ -x "${c%% *}" ]; then
        NEURON="$c"; break
    fi
done
if [ -n "$NEURON" ]; then
    echo "Deregistering from AI clients..."
    # shellcheck disable=SC2086
    $NEURON setup --uninstall --slug "$SLUG" $([ "$ASSUME_YES" = "1" ] && echo --yes) || true
else
    echo "  (neuron not found on PATH — skipping client deregistration)"
    echo "  To deregister manually: pipx run neuron setup --uninstall"
fi

# 2) Remove launcher script.
if [ -f "$LAUNCHER" ]; then
    if ask "Remove launcher ($LAUNCHER)?"; then
        rm -f "$LAUNCHER" && echo "  [OK] removed launcher" || echo "  [!] could not remove launcher"
    else
        echo "  Launcher kept."
    fi
fi

# 3) Remove .desktop file (Linux).
if [ -f "$DESK_FILE" ]; then
    if ask "Remove desktop entry ($DESK_FILE)?"; then
        rm -f "$DESK_FILE" && echo "  [OK] removed desktop entry" || echo "  [!] could not remove desktop entry"
    else
        echo "  Desktop entry kept."
    fi
fi

# 4) Remove memory store.
if [ -d "$GRAPHS_DIR" ]; then
    if [ "$PURGE_DATA" = "1" ]; then
        if ask "DELETE memory store ($GRAPHS_DIR)? This is irreversible!"; then
            rm -rf "$GRAPHS_DIR" && echo "  [OK] removed memory store" || echo "  [!] could not remove memory store"
        else
            echo "  Memory store kept."
        fi
    else
        echo "  Memory store kept: $GRAPHS_DIR"
        echo "  To delete it: sh uninstall.sh --purge-data"
    fi
fi

# 5) Remove venv + Neuron home directory.
if [ -d "$VENV" ]; then
    if ask "Remove the entire venv ($VENV)?"; then
        rm -rf "$NEURON_HOME" && echo "  [OK] removed $NEURON_HOME" || echo "  [!] could not remove $NEURON_HOME"
    else
        echo "  Venv kept."
    fi
fi

echo ""
echo "Uninstall complete. Restart your AI apps to unload Neuron."
echo "If you installed via pipx, also run: pipx uninstall neuron"
