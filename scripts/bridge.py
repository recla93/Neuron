"""Moved into the neuron package — use `python -m neuron bridge`.

Thin shim kept for back-compat with docs/launchers that call scripts/bridge.py.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from neuron.bridge import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
