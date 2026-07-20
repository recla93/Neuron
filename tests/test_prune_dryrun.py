"""F4 — dry_run su prune: anteprima read-only, nessuna mutazione né save."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))


def _graph():
    from neuron.models import Graph, Node, Link
    g = Graph(); g.turn_count = 30
    for kw in ("a", "b"):
        g.add_node(Node(keyword=kw, turn=1, topic="t", domain="d", sentiment="neutral"))
    g.add_link(Link("a", "b", "deepening", "tangential", "", 1, 1, inactive_turns=99))
    return g


def test_expired_tangential_is_readonly():
    g = _graph()
    assert len(g.expired_tangential()) == 1
    assert len(g.links) == 1               # untouched


def test_tool_prune_dry_run():
    pytest.importorskip("mcp")
    import asyncio
    import json
    import neuron.server as srv
    g = _graph()
    calls = []
    class _Spy:
        def save(self, *a, **k): calls.append(a)
    old = srv._g; srv._g = _Spy()
    try:
        out = asyncio.run(srv._tool_prune({"dry_run": True}, "", g))
    finally:
        srv._g = old
    data = json.loads(out[0].text)
    assert data["dry_run"] is True and data["would_prune"] == 1
    assert len(g.links) == 1 and not calls   # niente mutazione, niente save
