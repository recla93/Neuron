"""E1.4 — tool MCP `consolidate` + config auto. Via call_tool (version-agnostic)."""
import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))


def _vec(srv, i):
    v = [0.0] * srv.VECTOR_DIM
    v[i] = 1.0
    return v


def test_consolidate_tool_merges_active_graph():
    pytest.importorskip("mcp")
    pytest.importorskip("fastembed")
    import neuron.server as srv
    from neuron.models import Node

    g = srv._g.get()
    g.nodes.clear(); g.links.clear(); g._rebuild_node_map()
    g.add_node(Node(keyword="spring", turn=1, topic="t", domain="backend", sentiment="neutral", salience=3))
    g.add_node(Node(keyword="spring-boot", turn=1, topic="t", domain="backend", sentiment="neutral", salience=5))
    g.get_node("spring").vector = _vec(srv, 0)
    g.get_node("spring-boot").vector = _vec(srv, 0)   # identici -> cos 1.0

    orig_save = srv._g.save
    srv._g.save = lambda *a, **k: None
    try:
        out = asyncio.run(srv.call_tool("consolidate", {}))
        data = json.loads(out[0].text)
        assert any(m["kept"] == "spring" for m in data["merged"])
        assert g.get_node("spring-boot") is None
        assert g.get_node("spring").salience == 8
    finally:
        srv._g.save = orig_save


def test_auto_config_plumbing():
    pytest.importorskip("mcp")
    import neuron.server as srv
    assert isinstance(srv.consolidate_auto, bool)
    assert srv.CONSOLIDATE_EVERY == 20


def test_consolidate_cli_importable():
    # la CLI non deve dipendere dal server all'import del modulo __main__
    import importlib
    mm = importlib.import_module("neuron.__main__")
    assert callable(mm._consolidate_cli) and callable(mm.cli)
