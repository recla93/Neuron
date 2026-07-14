#!/usr/bin/env bash
# install-gui.sh — build the neuron-gui entry point.
# Cross-platform equivalent of Install-GUI.bat: runs pip install -e .
# which triggers the [project.gui-scripts] entry in pyproject.toml.
# On macOS/Linux this creates a `neuron-gui` shell script in the venv's bin/.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"

# Find Python: prefer the installed venv, fall back to system
NEURON_HOME="${NEURON_HOME:-$HOME/.local/share/neuron}"
VENV_PY="$NEURON_HOME/.venv/bin/python"
if [ ! -x "$VENV_PY" ]; then
    VENV_PY="$(command -v python3 || command -v python || true)"
    if [ -z "$VENV_PY" ]; then
        echo "ERROR: no Python found. Install Python 3.10+ first." >&2
        exit 1
    fi
fi

echo ""
echo "  Installing neuron-gui ..."
echo "  Python: $VENV_PY"
echo ""

FINDLINKS=""
[ -d "$HERE/vendor" ] && FINDLINKS="--find-links $HERE/vendor"

# shellcheck disable=SC2086
"$VENV_PY" -m pip install -e "$HERE" $FINDLINKS

# Verify
GUI_EXE="$NEURON_HOME/.venv/bin/neuron-gui"
if [ -x "$GUI_EXE" ] || command -v neuron-gui >/dev/null 2>&1; then
    echo ""
    echo "============================================================"
    echo "  neuron-gui installed."
    echo "  Run:  neuron-gui"
    echo "  Or:   python -m neuron gui"
    echo "============================================================"
else
    echo ""
    echo "  [!] neuron-gui not found. Check: pip show neuron | grep gui-scripts"
fi
