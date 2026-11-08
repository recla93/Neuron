"""E2.5 — _stimulus_block(): compact piggyback stimulus on tool responses.

Needs the server module (mcp/fastembed importable); the ranking itself is pure.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))


def _linked_graph():
    from neuron.models import Graph, Node, Link
    g = Graph()
    g.add_node(Node(keyword="seed", turn=1, topic="t", domain="d", sentiment="neutral"))
    g.add_node(Node(keyword="x", turn=1, topic="t", domain="d", sentiment="neutral"))
    g.add_link(Link(source="seed", target="x", link_type="deepening", weight="strong",
                    rationale="", created_turn=1, last_active_turn=1))
    g.add_fact("idea", "x could replace the polling loop", ["x"], 1)
    return g


def test_stimulus_emitted_above_threshold():
    pytest.importorskip("mcp"); pytest.importorskip("fastembed")
    import neuron.server as srv
    block = srv._stimulus_block(_linked_graph(), ["seed"])
    assert "🧠 [idea] x could replace the polling loop (seed ⇢ x" in block, block
    assert len(block) <= srv.STIMULUS_MAX_CHARS


def test_stimulus_empty_when_no_activation():
    pytest.importorskip("mcp"); pytest.importorskip("fastembed")
    import neuron.server as srv
    from neuron.models import Graph, Node
    g = Graph()
    g.add_node(Node(keyword="seed", turn=1, topic="t", domain="d", sentiment="neutral"))
    assert srv._stimulus_block(g, ["seed"]) == ""       # isolated seed → nothing to nudge


def test_stimulus_empty_when_flash_disabled(monkeypatch):
    pytest.importorskip("mcp"); pytest.importorskip("fastembed")
    import neuron.server as srv
    monkeypatch.setattr(srv, "flash_enabled", False)
    assert srv._stimulus_block(_linked_graph(), ["seed"]) == ""


def test_a_reached_node_without_a_fact_gives_no_stimulus():
    """ADR-016 D2 in Neuron: "🧠 sessione ⇢ commit (recall)" was two names and
    nothing to act on (live, 2026-11-08). A fact or nothing."""
    pytest.importorskip("mcp"); pytest.importorskip("fastembed")
    import neuron.server as srv
    g = _linked_graph()
    for f in list(g.facts.values()):
        f["status"] = "superseded"
    g.add_fact("habit", "the user pushes only after a usage test", ["x"], 1)   # personal: never a spark
    assert srv._stimulus_block(g, ["seed"]) == ""
