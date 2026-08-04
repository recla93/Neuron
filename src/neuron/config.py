"""Central configuration helpers — single source of truth for paths & slug.

Stdlib-only, zero neuron imports, so every module (server, manage, setup,
registry) can import it without circular-import risk.

Historically ``_default_graphs_dir()`` / ``_graphs_dir()`` were copy-pasted
verbatim into ``server.py``, ``manage.py`` and ``setup.py`` (analysis P0 #3);
a fix in one never propagated to the others. They now all delegate here.
"""

from __future__ import annotations

import os

__all__ = ["resolve_slug", "default_graphs_dir", "graphs_dir", "env_int", "env_float",
           "user_data_dir", "user_env_file", "set_user_env"]


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
    return os.path.join(user_data_dir(), "graphs")


SUITE_DIR = "GrayMatterEnvironment"


def _os_base() -> str:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.join(
            os.path.expanduser("~"), ".local", "share")
    # Vuoto (servizio, scheduled task, env ripulito) darebbe un path RELATIVO,
    # cioe' un graph store nella cwd del processo di turno.
    return base or os.path.expanduser("~")


def user_data_dir() -> str:
    """The per-user Neuron home — the parent of ``graphs``.

    Sta sotto la radice UNICA della suite: ``<base>/GrayMatterEnvironment/<slug>``.
    Prima i tre tool scrivevano in tre radici scollegate e nulla diceva che
    fossero lo stesso prodotto.

    Uno store ESISTENTE nella vecchia posizione vince sempre: cambiare la regola
    non deve poter far sparire una memoria. Il trasloco e' esplicito
    (``gray_matter.paths.migrate_to_suite_root``), non un effetto collaterale
    di un aggiornamento.

    Deliberately NOT keyed on ``NEURON_HOME``: that variable only picks the
    *venv* location in install.ps1/install.sh, and honouring it here would
    silently relocate an existing graph store."""
    slug = resolve_slug()
    base = _os_base()
    new = os.path.join(base, SUITE_DIR, slug)
    legacy = os.path.join(base, slug)
    if not os.path.isdir(new) and os.path.isdir(legacy):
        return legacy
    return new


def user_env_file() -> str:
    """The settings file that survives *any* cwd — unlike a project ``.env``,
    which ``_env._find_env_file`` can only find by walking up from the working
    directory. An MCP client spawns the server from an arbitrary cwd, so
    anything written only to a project .env (embedding model, Turso creds) was
    invisible at runtime. Written by the installer / GUI, read by ``_env``."""
    return os.path.join(user_data_dir(), ".env")


def set_user_env(**values: str) -> str:
    """Merge ``values`` into :func:`user_env_file`, preserving every other key
    (Turso credentials live in the same file). Returns the path written."""
    path = user_env_file()
    existing: dict[str, str] = {}
    order: list[str] = []
    try:
        # utf-8-sig: a PowerShell-written file may carry a BOM (see _env.py).
        with open(path, encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                if key and key not in existing:
                    order.append(key)
                existing[key] = val
    except OSError:
        pass
    for key, val in values.items():
        if key not in existing:
            order.append(key)
        existing[key] = str(val)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for key in order:
            f.write(f"{key}={existing[key]}\n")
    return path


def graphs_dir() -> str:
    """Resolved graph store: ``NS_GRAPHS_DIR`` override, else the per-user
    default (e.g. to keep an existing ``./graphs``). Always normalized."""
    return os.path.normpath(os.environ.get("NS_GRAPHS_DIR") or default_graphs_dir())
