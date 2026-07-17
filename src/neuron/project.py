"""Project identity + path canonicalization for shared-brain path memory (Fase G).

Why this exists: in a shared Neuron DB a node's identity is ``(context, keyword)``
with no user dimension, so *concepts* merge across users (intended). A file *path*
is an instance, not a concept — merging it blindly confuses references. To keep
paths distinct-yet-mergeable we canonicalize each one to:

    { project_id, path, by, shared }

- ``project_id``: a UUID that lives in a ``.neuron/project.json`` marker at the
  project root. Generated once, it travels with the shared project folder, so all
  collaborators resolve the SAME id **without needing Git**.
- ``path``: POSIX, relative to the marker's directory (never an absolute machine
  path — that would leak the home dir and be useless on another machine).
- ``shared``: True only when a marker was found. With no marker the ref is local
  (belongs in the per-user sidecar, not the shared DB) so nothing merges by accident.

Stdlib only, no ``neuron`` imports — safe to unit-test in isolation.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Optional

MARKER_DIR = ".neuron"
MARKER_FILE = "project.json"
SCHEMA = 1


def find_marker(start: str | os.PathLike) -> Optional[Path]:
    """Walk up from ``start`` (a file or dir) to the nearest ``.neuron/project.json``.
    Returns the marker Path, or None if none exists up to the filesystem root."""
    p = Path(os.path.abspath(start))
    if p.is_file():
        p = p.parent
    for d in (p, *p.parents):
        marker = d / MARKER_DIR / MARKER_FILE
        if marker.is_file():
            return marker
    return None


def project_root(start: str | os.PathLike) -> Optional[Path]:
    """The directory that owns the nearest marker (the root for relative paths)."""
    m = find_marker(start)
    return m.parent.parent if m else None


def read_project_id(start: str | os.PathLike) -> Optional[str]:
    """The project_id from the nearest marker, or None if unmarked/corrupt."""
    m = find_marker(start)
    if m is None:
        return None
    try:
        return json.loads(m.read_text(encoding="utf-8")).get("id") or None
    except Exception:
        return None


def init_project(root: str | os.PathLike) -> str:
    """Create ``<root>/.neuron/project.json`` with a fresh UUID if absent, and
    return the id. Idempotent: an existing marker is read, never overwritten (so a
    teammate who already initialized it keeps the shared id)."""
    root = Path(os.path.abspath(root))
    marker = root / MARKER_DIR / MARKER_FILE
    if marker.is_file():
        existing = read_project_id(root)
        if existing:
            return existing
    pid = str(uuid.uuid4())
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps({"id": pid, "schema": SCHEMA, "created": time.time()},
                   ensure_ascii=False, indent=2),
        encoding="utf-8")
    return pid


def sidecar_dir() -> Path:
    """Per-user LOCAL dir for path memory that has no shared project_id. Mirrors
    neuron.config's per-user data location so it sits beside the graph store and
    never travels to the shared cloud DB."""
    slug = os.environ.get("NEURON_SLUG", "neuron5")
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.join(
            os.path.expanduser("~"), ".local", "share")
    return Path(base) / slug / "path_sidecar"


def canonical_ref(file_path: str | os.PathLike, by: str = "",
                  start: str | os.PathLike | None = None) -> dict:
    """Canonicalize one visited file into a shared-safe ref record.

    With a marker in scope: ``shared=True``, ``path`` is POSIX-relative to the
    project root, ``project_id`` set. Without one: ``shared=False``,
    ``project_id=None`` and ``path`` stays absolute (POSIX) for local-only use —
    the caller routes it to the sidecar, not the shared DB.
    """
    abs_p = Path(os.path.abspath(file_path))
    root = project_root(start if start is not None else abs_p)
    if root is not None:
        try:
            rel = abs_p.relative_to(root).as_posix()
            return {"project_id": read_project_id(root), "path": rel,
                    "by": by, "shared": True}
        except ValueError:
            pass  # file outside the marked root -> treat as local
    return {"project_id": None, "path": abs_p.as_posix(), "by": by, "shared": False}


def canonicalize_references(references, by: str = "",
                           start: str | os.PathLike | None = None) -> list[dict]:
    """Normalize a ``store_turn`` ``references`` list for shared-safe storage.

    Idempotent and conservative: a ref that already carries a ``project_id`` (the
    client canonicalized it) is left untouched; only a **file** ref with an
    **absolute** path and no id is rewritten to project-relative + ``project_id`` +
    ``shared``. url/commit refs and already-relative paths pass through. Fills
    ``by`` (provenance) when missing. Never mutates the caller's dicts."""
    out: list[dict] = []
    for r in references or []:
        if not isinstance(r, dict):
            continue
        r = dict(r)
        if by and not r.get("by"):
            r["by"] = by
        if (r.get("type") == "file" and r.get("path")
                and not r.get("project_id") and os.path.isabs(str(r["path"]))):
            c = canonical_ref(r["path"], by=r.get("by", ""), start=start)
            r["path"] = c["path"]
            r["shared"] = c["shared"]
            if c["shared"]:
                r["project_id"] = c["project_id"]
        out.append(r)
    return out


def _ref_key(r: dict) -> tuple:
    return (r.get("type"), r.get("project_id"), r.get("path"))


def merge_refs(old, new, cap: int = 20) -> list[dict]:
    """Union of two reference lists, de-duplicated on ``(type, project_id, path)``
    so revisiting a concept accumulates its files instead of overwriting them.
    Bounded to ``cap`` (matches store_turn's validation limit)."""
    out = list(old or [])
    seen = {_ref_key(r) for r in out}
    for r in new or []:
        k = _ref_key(r)
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out[:cap]


def render_file_refs(references, limit: int = 3) -> list[str]:
    """Compact file refs for a pre_turn/get_context line: ``path`` plus ``(by)``
    when known. Non-file refs are skipped."""
    out: list[str] = []
    for r in references or []:
        if isinstance(r, dict) and r.get("type") == "file" and r.get("path"):
            s = str(r["path"])
            if r.get("by"):
                s += f" ({r['by']})"
            out.append(s)
    return out[:limit]
