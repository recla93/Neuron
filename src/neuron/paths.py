"""SSOT dei path di Neuron — Neuron sa dove stanno i SUOI file.

Separation of Concerns: le location di Neuron (memoria/grafi, sorgente) vivono
qui. `graphs_dir` delega a `config` (che era già la fonte di verità del grafo);
la novità è la *self-knowledge* del sorgente per repair/reinstall, così Gray
Matter la SCOPRE chiamando `source_dir()` invece di hardcodarla.

Stdlib only, zero import pesanti (come config.py).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from neuron import config as _config


def graphs_dir() -> Path:
    """Store dei grafi di Neuron (delega a config, la SSOT storica)."""
    return Path(_config.graphs_dir())


def data_dir() -> Path:
    """Cartella dati di Neuron (il livello slug, genitore di graphs/)."""
    return graphs_dir().parent


def _self_registry() -> Path:
    return data_dir() / "paths.json"


def record_self(source: "str | Path | None" = None) -> dict:
    """Registra la cartella sorgente (repo) di Neuron. La chiama l'installer di
    Neuron (o quello di GM per conto suo). Idempotente."""
    data = {}
    try:
        data = json.loads(_self_registry().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        data = {}
    if source and (Path(source) / "pyproject.toml").exists():
        data["source"] = str(Path(source).resolve())
    data["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        f = _self_registry()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return data


def source_dir() -> Path:
    """Cartella sorgente (repo) di Neuron: quella registrata se c'è, altrimenti
    la posizione del pacchetto installato. Neuron usa il src-layout, quindi il
    repo è due livelli sopra il package (`.../neuron/src/neuron`)."""
    try:
        rec = json.loads(_self_registry().read_text(encoding="utf-8")).get("source")
        if rec and Path(rec).exists():
            return Path(rec)
    except Exception:  # noqa: BLE001
        pass
    pkg = Path(__file__).resolve().parent          # .../src/neuron
    for cand in (pkg.parent.parent, pkg):           # repo (src-layout), else pkg
        if (cand / "pyproject.toml").exists():
            return cand
    return pkg.parent.parent


def data_paths() -> dict:
    """Le location dati di Neuron (per repair/uninstall scoped su Neuron)."""
    return {"neuron_graphs": graphs_dir()}


_OLD_SLUG = "neuron5"


def migrate_graphs(dry_run: bool = False) -> dict:
    """Migrate graph data from the old ``neuron5`` slug to the current ``neuron`` slug.

    Called automatically on first startup after upgrade, or manually via CLI.
    Idempotent: safe to run multiple times.

    Returns a dict with:
      - migrated: True if migration happened
      - old_path: path of the old data
      - new_path: path of the new data
      - error: error message if migration failed

    Safety:
      - Skips if NEURON_SLUG env var is set to neuron5 (user chose old slug)
      - Skips if old path doesn't exist
      - Skips if new path already has data (don't overwrite)
      - Uses shutil.move for atomic rename when possible
    """
    import os
    import shutil
    from pathlib import Path

    result = {"migrated": False, "old_path": "", "new_path": "", "error": ""}

    # If user explicitly chose neuron5, skip migration
    if os.environ.get("NEURON_SLUG") == _OLD_SLUG:
        return result

    # Build old and new paths
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.join(
            os.path.expanduser("~"), ".local", "share")

    old_path = Path(base) / _OLD_SLUG / "graphs"
    new_path = Path(graphs_dir())  # Uses current slug (neuron)

    result["old_path"] = str(old_path)
    result["new_path"] = str(new_path)

    # Skip if old path doesn't exist
    if not old_path.exists():
        return result

    # Skip if new path already has data (don't overwrite)
    if new_path.exists() and any(new_path.iterdir()):
        result["error"] = f"New path already has data: {new_path}"
        return result

    if dry_run:
        result["migrated"] = True
        return result

    try:
        # Create parent directory if needed
        new_path.parent.mkdir(parents=True, exist_ok=True)

        # Move old data to new location
        shutil.move(str(old_path), str(new_path))

        # Try to remove old parent directory if empty
        try:
            old_parent = old_path.parent
            if old_parent.exists() and not any(old_parent.iterdir()):
                old_parent.rmdir()
        except OSError:
            pass  # Not empty or other error, ignore

        result["migrated"] = True
    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)

    return result
