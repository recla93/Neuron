"""Glama audit fix: `reset` must refuse without confirm=true.

Uses the inner dispatcher `_call_tool_impl` (not the public `call_tool`) on purpose:
`call_tool` appends the one-shot loop-hint and flips a module global, which would
pollute order-dependent tests elsewhere. Here we only want the reset handler.
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))


def test_reset_refuses_without_confirm():
    pytest.importorskip("mcp")
    pytest.importorskip("fastembed")
    import neuron.server as srv
    from neuron.models import Node

    g = srv._g.get()
    g.nodes.clear(); g.links.clear(); g._rebuild_node_map()
    g.add_node(Node(keyword="keep-me", turn=1, topic="t", domain="backend", sentiment="neutral", salience=3))

    orig_save = srv._g.save
    srv._g.save = lambda *a, **k: None
    try:
        out = asyncio.run(srv._call_tool_impl("reset", {}))          # no confirm
        assert "Refused" in out[0].text
        assert g.get_node("keep-me") is not None                # graph intact

        asyncio.run(srv._call_tool_impl("reset", {"confirm": True}))   # explicit
        # reset drops the graph from the registry cache (new object on next get),
        # so re-fetch instead of reusing the now-detached `g` reference.
        assert srv._g.get().get_node("keep-me") is None          # wiped
    finally:
        srv._g.save = orig_save
