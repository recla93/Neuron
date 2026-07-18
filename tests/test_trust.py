"""B1–B3 — trust: confirm(confidence) → Node.trust → retrieval ranking.

Persistence path mirrors salience T11 Fase 2b: relative delta in the atomic
upsert, so two concurrent writers both count (L1).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))


def _node(kw, sal=0, trust=0.0, turn=10):
    from neuron.models import Node
    return Node(keyword=kw, turn=turn, topic="t", domain="d",
                sentiment="neutral", salience=sal, trust=trust)


def test_trust_roundtrip_sqlite(tmp_path):
    from neuron.models import Graph
    path = str(tmp_path / "g.db")
    g = Graph(); g.turn_count = 1
    g.add_node(_node("kafka", sal=3, trust=1.5))
    g._dirty = True
    g.save_sqlite(path, force=True)
    g2 = Graph(); g2.load_sqlite(path)
    assert g2.get_node("kafka").trust == pytest.approx(1.5)


def test_trust_atomic_delta_two_writers(tmp_path):
    """Two graphs on the same store: both trust increments land (no lost update)."""
    from neuron.models import Graph
    path = str(tmp_path / "shared.db")
    g = Graph(); g.turn_count = 1
    g.add_node(_node("kafka", trust=1.0))
    g._dirty = True
    g.save_sqlite(path, force=True)

    w1 = Graph(); w1.load_sqlite(path)
    w2 = Graph(); w2.load_sqlite(path)
    for w in (w1, w2):
        nd = w.get_node("kafka")
        nd.trust += 0.5
        w.mark_node_dirty("kafka")
        w._dirty = True
        w.save_sqlite(path, force=True)

    g3 = Graph(); g3.load_sqlite(path)
    assert g3.get_node("kafka").trust == pytest.approx(2.0)   # 1.0 + 0.5 + 0.5


def test_trust_never_negative_in_store(tmp_path):
    from neuron.models import Graph
    path = str(tmp_path / "g.db")
    g = Graph(); g.turn_count = 1
    g.add_node(_node("kafka", trust=0.5))
    g._dirty = True
    g.save_sqlite(path, force=True)
    w = Graph(); w.load_sqlite(path)
    w.get_node("kafka").trust -= 5.0   # delta -5 → MAX(0, …) clamps
    w.mark_node_dirty("kafka"); w._dirty = True
    w.save_sqlite(path, force=True)
    g2 = Graph(); g2.load_sqlite(path)
    assert g2.get_node("kafka").trust == 0.0


def test_legacy_store_without_trust_column_loads(tmp_path):
    """Old DB (no trust col): load works, trust defaults to 0."""
    import sqlite3
    from neuron.models import Graph
    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE nodes (id INTEGER PRIMARY KEY AUTOINCREMENT, keyword TEXT, "
        "turn INTEGER, topic TEXT, domain TEXT, sentiment TEXT, salience INTEGER, "
        "entities TEXT DEFAULT '[]', tags TEXT DEFAULT '[]', refs TEXT DEFAULT '[]');"
        "CREATE TABLE links (id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT, "
        "target TEXT, link_type TEXT, weight TEXT, rationale TEXT, created_turn INTEGER, "
        "last_active_turn INTEGER, inactive_turns INTEGER);"
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);")
    conn.execute("INSERT INTO nodes (keyword, turn, topic, domain, sentiment, salience) "
                 "VALUES ('old', 1, 't', 'd', 'neutral', 2)")
    conn.commit(); conn.close()
    g = Graph(); g.load_sqlite(path)
    assert g.get_node("old").trust == 0.0


def test_confirm_confidence_raises_trust():
    pytest.importorskip("mcp")
    import asyncio
    import neuron.server as srv
    from neuron.models import Graph
    g = Graph(); g.turn_count = 1
    g.add_node(_node("kafka"))
    class _NoSave:
        def save(self, *a, **k): pass
    old = srv._g; srv._g = _NoSave()
    try:
        out = asyncio.run(srv._tool_confirm(
            {"keywords": ["kafka"], "confidence": 0.4}, "", g))
        assert g.get_node("kafka").trust == pytest.approx(0.4)
        assert '"confidence": 0.4' in out[0].text
        # clamp: >1 → 1.0
        asyncio.run(srv._tool_confirm({"keywords": ["kafka"], "confidence": 7}, "", g))
    finally:
        srv._g = old
    assert g.get_node("kafka").trust == pytest.approx(1.4)


def test_trust_in_ranking(monkeypatch):
    pytest.importorskip("mcp"); pytest.importorskip("fastembed")
    import neuron.server as srv
    from neuron.models import Graph, Link
    monkeypatch.setattr(srv, "_search_embeddings", lambda *a, **k: [])
    g = Graph(); g.turn_count = 10
    # twins: same salience/turn; only trust differs
    g.add_node(_node("q", sal=1))
    g.add_node(_node("plain", sal=5))
    g.add_node(_node("trusted", sal=5, trust=3.0))
    g.add_link(Link("q", "plain", "deepening", "medium", "", 10, 10))
    g.add_link(Link("q", "trusted", "deepening", "medium", "", 10, 10))
    _, top_nodes, *_ = srv._resolve_context({"q"}, 2, g, "")
    ranked = [k for k, _ in top_nodes]
    assert ranked.index("trusted") < ranked.index("plain")
