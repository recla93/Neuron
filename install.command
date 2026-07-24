#!/bin/sh
# Neuron — click-and-go installer (macOS/Linux). Double-click me.
cd "$(dirname "$0")" && sh install.sh
echo; printf "Done. Press Enter to close."; read -r _
