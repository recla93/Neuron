#!/usr/bin/env sh
# Neuron installer (macOS/Linux) — thin launcher for the UNIFIED Gray Matter
# installer. Gray Matter is always the brain: this installs the GM control
# center + Neuron, registers the gateway, deploys hooks and opens the GUI,
# where you manage/verify the tools. One logic, defined once in
# gray_matter/install.sh — this file only finds and launches it.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
for gm in "$HERE/gray_matter" "$HERE/../gray_matter"; do
    if [ -f "$gm/install.sh" ]; then
        GM_PEER_DIR="$HERE" exec sh "$gm/install.sh" "$@"
    fi
done
echo "ERROR: gray_matter not found (bundled ./gray_matter or sibling ../gray_matter)."
echo "Download the full suite, or place the gray_matter repo next to this one."
exit 1
