"""Neuron Dev Console — standalone, non-invasive diagnostic tool.

Usage:
    python scripts/neuron_console.py          # one-shot summary
    python scripts/neuron_console.py --watch  # refresh every 5s
    python scripts/neuron_console.py --watch=2  # refresh every 2s
"""
import os, sys, time, json, struct
from datetime import datetime

GRAPHS_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "graphs"))
SEED_PATH = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "knowledge", "base_knowledge.db"))

from neuron import db as _db
sqlite3 = _db
ENGINE = _db.ENGINE_NAME


def _fmt(n: int) -> str:
    return f"{n:,}"


def _pct(a: int, b: int) -> str:
    return f"{a/b*100:.0f}%" if b else "—"


def _bar(pct: float, w: int = 20) -> str:
    filled = int(pct / 100 * w)
    return "#" * filled + "." * (w - filled)


def _check_db(path: str, label: str) -> dict:
    info = {"label": label, "ok": False, "nodes": 0, "links": 0, "domains": {},
            "vectors": 0, "valid_links": 0, "dangling_links": 0, "errors": []}
    if not os.path.exists(path):
        info["errors"].append("file not found")
        return info
    size = os.path.getsize(path)
    info["size_kb"] = size / 1024
    if size == 0:
        info["errors"].append("empty file")
        return info
    try:
        conn = sqlite3.connect(path)
        info["nodes"] = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        info["links"] = conn.execute("SELECT COUNT(*) FROM links").fetchone()[0]
        info["vectors"] = conn.execute("SELECT COUNT(*) FROM node_vectors").fetchone()[0]
        info["domains"] = dict(conn.execute(
            "SELECT domain, COUNT(*) FROM nodes GROUP BY domain ORDER BY COUNT(*) DESC"
        ).fetchall())
        valid = conn.execute("""
            SELECT COUNT(*) FROM links l
            WHERE l.source IN (SELECT keyword FROM nodes)
            AND l.target IN (SELECT keyword FROM nodes)
        """).fetchone()[0]
        info["valid_links"] = valid
        info["dangling_links"] = info["links"] - valid
        # vector dimension check
        dims = conn.execute("SELECT dim, COUNT(*) FROM node_vectors GROUP BY dim").fetchall()
        info["vector_dims"] = dict(dims) if dims else {}
        conn.close()
        info["ok"] = True
    except Exception as e:
        info["errors"].append(str(e))
    return info


def _scan_graphs() -> list[dict]:
    results = []
    if os.path.exists(SEED_PATH):
        results.append(_check_db(SEED_PATH, "seed (base_knowledge.db)"))
    if os.path.isdir(GRAPHS_DIR):
        for fname in sorted(os.listdir(GRAPHS_DIR)):
            if fname.startswith("graph_") and fname.endswith(".db"):
                path = os.path.join(GRAPHS_DIR, fname)
                results.append(_check_db(path, fname))
    return results


def _print_report(dbs: list[dict]) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"\n{'='*70}")
    print(f"  Neuron Dev Console  |  {ts}  |  Engine: {ENGINE}")
    print(f"{'='*70}")

    total_nodes = sum(d["nodes"] for d in dbs if d["ok"])
    total_links = sum(d["links"] for d in dbs if d["ok"])
    total_valid = sum(d["valid_links"] for d in dbs if d["ok"])
    total_dangling = sum(d["dangling_links"] for d in dbs if d["ok"])

    print(f"  Databases: {len(dbs)}  |  Nodes: {_fmt(total_nodes)}  |  Links: {_fmt(total_links)}")

    for db in dbs:
        status = "OK" if db["ok"] else "ERR"
        label = db["label"]
        if not db["ok"]:
            print(f"  [{status}] {label}: {'; '.join(db['errors'])}")
            continue

        vpct = _pct(db["vectors"], db["nodes"])
        lpct = _pct(db["valid_links"], db["links"])
        size = db.get("size_kb", 0)
        domains = ", ".join(f"{d}={c}" for d, c in db["domains"].items())
        dims_info = ""
        if db.get("vector_dims"):
            dims_info = " | dims: " + ", ".join(f"{d}={c}" for d, c in db["vector_dims"].items())

        print(f"  [{status}] {label}")
        print(f"         nodes={_fmt(db['nodes'])} links={_fmt(db['links'])} "
              f"vectors={_fmt(db['vectors'])} ({vpct}){dims_info}")
        print(f"         valid links: {_fmt(db['valid_links'])} ({lpct})  "
              f"dangling: {_fmt(db['dangling_links'])}  size={size:.0f}KB")
        print(f"         domains: {domains}")

        # link health bar
        if db["links"] > 0:
            hpct = db["valid_links"] / db["links"] * 100
            bar = _bar(hpct)
            print(f"         link health: {bar} {hpct:.0f}%")

    # summary bars
    if total_links > 0:
        hpct = total_valid / total_links * 100
        bar = _bar(hpct)
        vlpct = _pct(total_valid, total_links)
        print(f"  {'─'*50}")
        print(f"  Overall link health: {bar} {vlpct}")
        print(f"  Dangling links: {_fmt(total_dangling)}/{_fmt(total_links)} "
              f"({total_dangling/total_links*100:.0f}%)")

    print(f"{'='*70}")


def _watch(interval: int) -> None:
    """Continuously refresh the report every `interval` seconds."""
    try:
        while True:
            dbs = _scan_graphs()
            _print_report(dbs)
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n  Exited.")


if __name__ == "__main__":
    watch_interval = None
    for arg in sys.argv[1:]:
        if arg == "--watch":
            watch_interval = 5
        elif arg.startswith("--watch="):
            watch_interval = int(arg.split("=")[1])
    dbs = _scan_graphs()
    _print_report(dbs)
    if watch_interval:
        _watch(watch_interval)
