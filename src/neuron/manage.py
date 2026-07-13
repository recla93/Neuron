"""`neuron manage` — day-to-day management, cross-platform (T63 fase 2, ADR-007).

The lifecycle lives in `neuron setup`; this is everything else, portable:

    neuron manage                 # interactive menu
    neuron manage --overview      # contexts with node/link/turn counts
    neuron manage --export out.json [--context X]
    neuron manage --consolidate [--context X]
    neuron manage --visualize     # graph HTML (repo script if reachable)

Windows keeps the richer Configuration Center (bridge/tunnel/console live);
those need per-OS process plumbing and stay fase 3. Everything here is
stdlib + the neuron package itself, lazy-imported so the menu always opens.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def _graphs_dir() -> str:
    slug = os.environ.get("NEURON_SLUG", "neuron5")
    if os.environ.get("NS_GRAPHS_DIR"):
        return os.environ["NS_GRAPHS_DIR"]
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, slug, "graphs")
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, slug, "graphs")


def _contexts() -> list[str]:
    import glob
    ctxs = {Path(p).stem[len("graph_"):].replace("__", "/")
            for p in glob.glob(os.path.join(_graphs_dir(), "graph_*.db"))}
    try:
        from neuron import db as _db
        if getattr(_db, "REMOTE_TURSO", False):
            conn = _db.connect("")
            try:
                ctxs |= {c for (c,) in conn.execute(
                    "SELECT DISTINCT context FROM nodes").fetchall() if c}
            finally:
                conn.close()
    except Exception:
        pass
    return sorted(ctxs) or ["default"]


def _load(context: str):
    from neuron.models import Graph
    g = Graph()
    path = os.path.join(_graphs_dir(), f"graph_{context.replace('/', '__')}.db")
    g.load_sqlite(path, context=context)
    return g


def do_overview() -> int:
    print(f"\nStore: {_graphs_dir()}")
    try:
        from neuron import db as _db
        print(f"Engine: {getattr(_db, 'ENGINE_NAME', '?')}"
              f"{' (cloud)' if getattr(_db, 'REMOTE_TURSO', False) else ''}")
    except Exception as e:
        print(f"[!] neuron.db not importable: {e}")
        return 1
    print(f"\n{'context':30s} {'nodes':>6} {'links':>6} {'turns':>6}  top concept")
    for c in _contexts():
        try:
            g = _load(c)
            top = max(g.nodes, key=lambda n: n.salience).keyword if g.nodes else "-"
            print(f"{c:30s} {len(g.nodes):>6} {len(g.links):>6} {g.turn_count:>6}  {top}")
        except Exception as e:
            print(f"{c:30s} [!] {e}")
    return 0


def do_export(out: str, context: str) -> int:
    g = _load(context)
    data = g.export()
    Path(out).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] {context}: {len(g.nodes)} nodes / {len(g.links)} links -> {out}")
    return 0


def do_consolidate(context: "str | None") -> int:
    contexts = [context] if context else _contexts()
    for c in contexts:
        g = _load(c)
        before = (len(g.nodes), len(g.links))
        g.consolidate(drop_orphans=True)
        path = os.path.join(_graphs_dir(), f"graph_{c.replace('/', '__')}.db")
        g.save_sqlite(path, context=c)
        print(f"[OK] {c}: {before[0]}->{len(g.nodes)} nodes, {before[1]}->{len(g.links)} links")
    return 0


def do_visualize() -> int:
    """The visualizer script ships in the repo (scripts/); pipx installs may
    not have it — locate it via NEURON_REPO or relative to a source checkout."""
    cands = []
    if os.environ.get("NEURON_REPO"):
        cands.append(Path(os.environ["NEURON_REPO"]) / "scripts" / "generate_graph_html.py")
    cands.append(Path(__file__).resolve().parent.parent.parent / "scripts" / "generate_graph_html.py")
    script = next((p for p in cands if p.exists()), None)
    if script is None:
        print("[!] generate_graph_html.py not found. Set NEURON_REPO to your source\n"
              "    checkout, or run it from the repo / Configuration Center.")
        return 1
    return subprocess.call([sys.executable, str(script)])


def _menu() -> int:
    while True:
        print("\n=== Neuron manage ===\n"
              "  1) Overview (contexts, counts, engine)\n"
              "  2) Export a context to JSON\n"
              "  3) Consolidate (merge near-duplicates, drop orphans)\n"
              "  4) Graph visualizer (HTML)\n"
              "  5) Doctor (health check)\n"
              "  6) Exit")
        try:
            ch = input("> ").strip()
        except EOFError:
            return 0
        if ch == "1":
            do_overview()
        elif ch == "2":
            c = input("context [default]: ").strip() or "default"
            do_export(f"neuron-{c.replace('/', '_')}.json", c)
        elif ch == "3":
            c = input("context [ALL]: ").strip() or None
            do_consolidate(c)
        elif ch == "4":
            do_visualize()
        elif ch == "5":
            from neuron.clients import doctor
            lines, problems = doctor(os.environ.get("NEURON_SLUG", "neuron5"), sys.executable)
            for ln in lines:
                print(ln)
            print(f"{problems} problem(s)." if problems else "All good.")
        else:
            return 0


def main(argv: list[str]) -> int:
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(prog="neuron manage")
    ap.add_argument("--overview", action="store_true")
    ap.add_argument("--export", metavar="OUT")
    ap.add_argument("--consolidate", action="store_true")
    ap.add_argument("--visualize", action="store_true")
    ap.add_argument("--context", default=None)
    a = ap.parse_args(argv)
    if a.overview:
        return do_overview()
    if a.export:
        return do_export(a.export, a.context or "default")
    if a.consolidate:
        return do_consolidate(a.context)
    if a.visualize:
        return do_visualize()
    return _menu()
