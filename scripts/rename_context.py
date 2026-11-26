"""Rename a context: its rows, its graph file and the pointers that name it.

2026-11-26: `backend` held the whole Gray Matter project (a domain guessed by
the old auto-switch); the context is named after what it holds. Run it with the
daemon STOPPED: a running Neuron holds the graph in memory under the old name.

    python scripts/rename_context.py backend graymatter            # dry run
    python scripts/rename_context.py backend graymatter --apply

Exact names only: `backend/python/qwen/ollama` is another context and is left
alone. A backup of the graph file is taken first (db.snapshot, tag `pre-rename`).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

SIDECARS = ("", "-wal", "-shm", "-tshm")


def _file(graphs: str, ctx: str) -> str:
    return os.path.join(graphs, f"graph_{ctx.replace('/', '__') if ctx != 'default' else 'default'}.db")


def rename(graphs: str, old: str, new: str, apply: bool) -> dict:
    from neuron import db as _db
    old, new = old.lower().strip("/"), new.lower().strip("/")
    src, dst = _file(graphs, old), _file(graphs, new)
    report: dict = {"from": old, "to": new, "file": os.path.basename(src)}
    if not os.path.isfile(src):
        return {**report, "error": "no graph file for the old name"}
    if any(os.path.exists(dst + s) for s in SIDECARS):
        return {**report, "error": "the new name already has a graph file"}
    conn = _db.connect(src)
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    rows = {}
    for t in tables:
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info('{t}')").fetchall()]
        if "context" in cols:
            rows[t] = conn.execute(f"SELECT COUNT(*) FROM '{t}' WHERE context=?", (old,)).fetchone()[0]
    report["rows"] = rows
    ptr = os.path.join(graphs, "_active_context.txt")
    cross = os.path.join(graphs, "_cross_links.json")
    report["pointer"] = os.path.isfile(ptr) and open(ptr, encoding="utf-8").read().strip() == old
    links = json.load(open(cross, encoding="utf-8")) if os.path.isfile(cross) else []
    report["cross_links"] = sum((c.get("source_context") == old) + (c.get("target_context") == old) for c in links)
    if not apply:
        conn.close()
        return report
    conn.close()
    _db.snapshot(src, tag="pre-rename")
    conn = _db.connect(src)
    for t in rows:
        conn.execute(f"UPDATE '{t}' SET context=? WHERE context=?", (new, old))
    conn.commit()
    conn.close()
    for s in SIDECARS:
        if os.path.exists(src + s):
            os.replace(src + s, dst + s)
    if report["pointer"]:
        with open(ptr, "w", encoding="utf-8") as f:
            f.write(new)
    if report["cross_links"]:
        for c in links:
            for k in ("source_context", "target_context"):
                if c.get(k) == old:
                    c[k] = new
        with open(cross, "w", encoding="utf-8", newline="\n") as f:
            json.dump(links, f, ensure_ascii=False, indent=2)
    report["applied"] = True
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("old")
    ap.add_argument("new")
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    a = ap.parse_args(argv)
    from neuron import config
    r = rename(config.graphs_dir(), a.old, a.new, a.apply)
    print(r)
    if not a.apply and "error" not in r:
        print("dry run: nothing written (add --apply)")
    return 1 if "error" in r else 0


if __name__ == "__main__":
    raise SystemExit(main())
