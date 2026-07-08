"""E2.2 — composite salience-aware node ranking in _resolve_context (ADR-003 #3).

Ranking blends semantic similarity (query→node), normalized salience, and recency.
We monkeypatch _search_embeddings so the sim component is deterministic and the
test needs no real embedding numbers — only mcp/fastembed importability.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))


def _graph():
    from neuron.models import Graph, Node, Link
    g = Graph(); g.turn_count = 10
    for kw, sal in (("hub", 20), ("a", 1), ("b", 1)):
        g.add_node(Node(keyword=kw, turn=10, topic="t", domain="d",
                        sentiment="neutral", salience=sal))
    g.add_link(Link("a", "hub", "deepening", "medium", "", 10, 10))
    g.add_link(Link("b", "hub", "deepening", "medium", "", 10, 10))
    return g


def test_salient_neighbour_tops_when_no_sim(monkeypatch):
    pytest.importorskip("mcp"); pytest.importorskip("fastembed")
    import neuron.server as srv
    monkeypatch.setattr(srv, "_search_embeddings", lambda *a, **k: [])  # sim = 0 for all
    g = _graph()
    _, top_nodes, *_ = srv._resolve_context({"a", "b"}, 2, g, "")
    # hub is a linked neighbour, not a query term, yet its salience floats it up.
    assert top_nodes[0][0] == "hub"


def test_high_sim_node_beats_high_salience_node(monkeypatch):
    pytest.importorskip("mcp"); pytest.importorskip("fastembed")
    import neuron.server as srv
    # 'a' is a perfect semantic match; hub is merely very salient.
    monkeypatch.setattr(srv, "_search_embeddings", lambda *a, **k: [("a", 1.0)])
    g = _graph()
    _, top_nodes, *_ = srv._resolve_context({"a", "b"}, 2, g, "")
    order = [kw for kw, _ in top_nodes]
    assert order.index("a") < order.index("hub")
