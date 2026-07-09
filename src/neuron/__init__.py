"""Neuron — Semantic memory for AI via MCP."""

# Load .env (if any) BEFORE any submodule — db.py resolves the DB tier from
# TURSO_* at import time, so credentials saved by scripts/connect_turso.py must
# be in os.environ by then. No-op under pytest / NEURON_NO_DOTENV. (T16)
from neuron._env import load_dotenv_once as _load_dotenv_once

_load_dotenv_once()

# Neuron 5 "Synapse" (feat/neuron-bomb); master stays 4.0.0. Keep this line clean
# (value only, no trailing comment) — naive parsers in deploy.ps1 read it verbatim.
__version__ = "5.0.0"
