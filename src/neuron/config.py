"""Central configuration helpers — single source of truth for paths & slug.

Stdlib-only, zero neuron imports, so every module (server, manage, setup,
registry) can import it without circular-import risk.

Historically ``_default_graphs_dir()`` / ``_graphs_dir()`` were copy-pasted
verbatim into ``server.py``, ``manage.py`` and ``setup.py`` (analysis P0 #3);
a fix in one never propagated to the others. They now all delegate here.
"""

from __future__ import annotations

import os

__all__ = ["resolve_slug", "default_graphs_dir", "graphs_dir", "env_int", "env_float"]


def env_int(name: str, default: int) -> int:
    """Read an int tunable from the env; fall back to default on unset/malformed."""
    try:
        return int(os.environ.get(name, "").strip() or default)
    except (ValueError, TypeError):
        return default


def env_float(name: str, default: float) -> float:
    """Read a float tunable from the env; fall back to default on unset/malformed."""
    try:
        return float(os.environ.get(name, "").strip() or default)
    except (ValueError, TypeError):
        return default


def resolve_slug() -> str:
    """The install slug (default ``neuron``); lets v5 run beside older majors."""
    return os.environ.get("NEURON_SLUG", "neuron")


def default_graphs_dir() -> str:
    """A STABLE per-user location for the memory graphs.

    The old default was package-relative (``<pkg>/../../graphs``), which when
    installed resolves *inside* the venv (wiped on reinstall) or somewhere
    throwaway — so memory didn't reliably persist across restarts. Use a real
    user-data dir instead.

    Uses NEURON_SLUG (default ``neuron``) so it can run side by side with v4
    without sharing a graph store — their DB schema and default embedding model
    differ, so a shared store would corrupt each other's vectors.
    """
    slug = resolve_slug()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, slug, "graphs")
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, slug, "graphs")


def graphs_dir() -> str:
    """Resolved graph store: ``NS_GRAPHS_DIR`` override, else the per-user
    default (e.g. to keep an existing ``./graphs``). Always normalized."""
    return os.path.normpath(os.environ.get("NS_GRAPHS_DIR") or default_graphs_dir())
