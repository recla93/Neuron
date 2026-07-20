#!/usr/bin/env sh
# Neuron uninstaller (macOS/Linux) — thin launcher for the UNIFIED Gray Matter
# uninstaller: reap → deregister → remove hooks/code → INTERACTIVE on memory.
# One logic in `gray-matter uninstall`; legacy leftovers (old slug, orphan
# scripts) are covered by its legacy scan.
set -eu
if command -v gray-matter >/dev/null 2>&1; then
    exec gray-matter uninstall "$@"
fi
VPY="${GM_HOME:-$HOME/.local/share/gray-matter}/.venv/bin/python"
[ -x "$VPY" ] && exec "$VPY" -m gray_matter.cli uninstall "$@"
echo "ERROR: gray-matter not found — nothing to uninstall via the unified path."
echo "Manual cleanup: remove the venv dir and the 'neuron5'/'gray-matter' entries from your MCP client configs (.bak backups sit next to them)."
exit 1
