"""L2 regression — `store_turn -> open: NotFound`.

Two guards:
1. Storing into a brand-new context whose graphs_dir does not exist yet must not
   crash: `_ensure_parent_dir` creates the tree, through the worker's
   clear+reload cycle. (Reproducible sub-case, sqlite tier.)
2. If the local Turso engine fails every open (the concurrent-open race), the
   `db.connect()` L2 guard must degrade to sqlite3 on the same file, not raise.

The multi-process WAL race itself needs a live daemon + real pyturso to observe;
these lock in the parts that ARE deterministic.
"""
from __future__ import annotations

import os
import sys

from tests._mockdeps import install_mock_deps
install_mock_deps()  # sqlite tier (turso=None), fake fastembed/mcp

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from neuron import db as _db            # noqa: E402
from neuron.registry import GraphRegistry  # noqa: E402
from neuron.models import Node          # noqa: E402


def test_store_new_context_missing_dir(tmp_path):
    graphs = tmp_path / "does" / "not" / "exist" / "graphs"
    reg = GraphRegistry(str(graphs))
    reg.switch("backend/java")                       # the context-switch turn
    g = reg.get("backend/java")
    g.add_node(Node(keyword="spring", turn=1, topic="spring boot",
                    domain="backend", sentiment="neutral"))
    path = reg._db_path("backend/java")
    g.save_sqlite(path, context="backend/java")      # WRITE — must not raise
    assert os.path.exists(path)
    # worker freshness: clear cache, reload from file, write again
    reg._graphs.clear()
    g2 = reg.get("backend/java")
    g2.add_node(Node(keyword="kotlin", turn=2, topic="coroutines",
                     domain="backend", sentiment="neutral"))
    g2.save_sqlite(path, context="backend/java")     # reload+write — must not raise
    assert len(g2.nodes) == 2


def test_local_open_degrades_to_sqlite(tmp_path, monkeypatch):
    """Local pyturso engine present but failing every open -> degrade to sqlite3."""
    class _Boom:
        @staticmethod
        def connect(_path):
            raise OSError("open: NotFound")

    monkeypatch.setattr(_db, "_local_turso", _Boom, raising=False)
    monkeypatch.setattr(_db, "LOCAL_TURSO_ENGINE", True, raising=False)

    conn = _db.connect(str(tmp_path / "g.db"))        # must NOT raise
    conn.execute("CREATE TABLE t(x)")
    conn.execute("INSERT INTO t VALUES (1)")
    assert conn.execute("SELECT x FROM t").fetchone()[0] == 1
