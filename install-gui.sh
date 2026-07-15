#!/usr/bin/env bash
# install-gui.sh — ensure neuron-gui is available on macOS/Linux.
#
# Calls install.sh to set up Neuron + the neuron-gui entry point,
# then creates a Desktop shortcut. Idempotent: safe to re-run.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"

# Run the main installer with --yes to skip prompts
exec "$HERE/install.sh" --yes
