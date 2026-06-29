"""Tests for the Neuron MCP server — models, registry, and server helpers."""

import os
import sys
import tempfile
import pytest

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


# ---------------------------------------------------------------------------
# Package / import smoke tests
# ---------------------------------------------------------------------------

def test_import_package():
    """Package imports and version string is correct."""
    import neuron
    assert hasattr(neuron, "__version__")
    assert neuron.__version__ == "3.3.0"


def test_import_server():
    """Server module imports without error and exposes main()."""
    pytest.importorskip("mcp")
    import neuron.server
    assert hasattr(neuron.server, "main")


def test_import_models():
    """models.py imports correctly and exposes core classes."""
    from neuron.models import Node, Link, Graph
    assert Node
    assert Link
    assert Graph


def test_import_registry():
    """registry.py imports correctly."""
    from neuron.registry import GraphRegistry
    assert GraphRegistry


# ---------------------------------------------------------------------------
# Graph -- node deduplication and normalization
# ---------------------------------------------------------------------------

def test_graph_add_node_dedup_case():
    """add_node deduplicates case-insensitively and max-merges salience."""
    from neuron.models import Graph, Node

    g = Graph()
    g.add_node(Node(keyword="Kotlin Flow", turn=1, topic="t", domain="backend",
                    sentiment="neutral", salience=3))
    g.add_node(Node(keyword="kotlin flow", turn=2, topic="t", domain="backend",
                    sentiment="neutral", salience=7))
    g.add_node(Node(keyword="KOTLIN FLOW", turn=3, topic="t", domain="backend",
                    sentiment="neutral", salience=2))

    assert len(g.nodes) == 1
    assert g.nodes[0].keyword == "kotlin flow"
    assert g.nodes[0].salience == 7  # max of 3, 7, 2


def test_graph_add_node_dedup_strips_whitespace():
    """add_node deduplicates after stripping whitespace."""
    from neuron.models import Graph, Node

    g = Graph()
    g.add_node(Node(keyword="  spring boot  ", turn=1, topic="t", domain="backend",
                    sentiment="neutral", salience=5))
    g.add_node(Node(keyword="spring boot", turn=2, topic="t", domain="backend",
                    sentiment="neutral", salience=4))

    assert len(g.nodes) == 1
    assert g.nodes[0].keyword == "spring boot"
    assert g.nodes[0].salience == 5  # max(5, 4)


def test_graph_get_node_case_insensitive():
    """get_node returns nodes regardless of query case."""
    from neuron.models import Graph, Node

    g = Graph()
    g.add_node(Node(keyword="virtual threads", turn=1, topic="t", domain="backend",
                    sentiment="neutral", salience=4))

    assert g.get_node("virtual threads") is not None
    assert g.get_node("Virtual Threads") is not None
    assert g.get_node("VIRTUAL THREADS") is not None
    assert g.get_node("nonexistent") is None


# ---------------------------------------------------------------------------
# Graph -- link deduplication
# ---------------------------------------------------------------------------

def test_graph_add_link_dedup_same_direction():
    """add_link deduplicates identical links and upgrades weight."""
    from neuron.models import Graph, Node, Link

    g = Graph()
    for kw in ("a", "b"):
        g.add_node(Node(keyword=kw, turn=1, topic="t", domain="general",
                        sentiment="neutral", salience=1))

    g.add_link(Link(source="a", target="b", link_type="deepening", weight="medium",
                    rationale="", created_turn=1, last_active_turn=1))
    g.add_link(Link(source="a", target="b", link_type="deepening", weight="strong",
                    rationale="", created_turn=2, last_active_turn=2))

    assert len(g.links) == 1
    assert g.links[0].weight == "strong"  # upgraded


def test_graph_add_link_dedup_reverse_direction():
    """add_link deduplicates bidirectional links."""
    from neuron.models import Graph, Node, Link

    g = Graph()
    for kw in ("x", "y"):
        g.add_node(Node(keyword=kw, turn=1, topic="t", domain="general",
                        sentiment="neutral", salience=1))

    g.add_link(Link(source="x", target="y", link_type="analogy", weight="medium",
                    rationale="", created_turn=1, last_active_turn=1))
    g.add_link(Link(source="y", target="x", link_type="analogy", weight="tangential",
                    rationale="", created_turn=2, last_active_turn=2))

    assert len(g.links) == 1
    assert g.links[0].weight == "medium"  # stronger kept


# ---------------------------------------------------------------------------
# Graph -- persistence
# ---------------------------------------------------------------------------

def test_graph_save_and_load_sqlite():
    """Graph round-trips to SQLite correctly."""
    from neuron.models import Graph, Node, Link

    g = Graph(session_id="test-session", turn_count=3)
    g.add_node(Node(keyword="persistence", turn=1, topic="db", domain="backend",
                    sentiment="neutral", salience=5))
    g.add_link(Link(source="persistence", target="sqlite", link_type="instance-of",
                    weight="strong", rationale="sqlite is a persistence backend",
                    created_turn=1, last_active_turn=3))

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        g.save_sqlite(db_path, force=True)

        g2 = Graph()
        g2.load_sqlite(db_path)

        assert g2.session_id == "test-session"
        assert g2.turn_count == 3
        assert len(g2.nodes) == 1
        assert g2.nodes[0].keyword == "persistence"
        assert g2.nodes[0].salience == 5
        assert len(g2.links) == 1
        assert g2.links[0].weight == "strong"
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# GraphRegistry -- multi-context and resolve_chain
# ---------------------------------------------------------------------------

def test_registry_resolve_chain_default():
    """resolve_chain on 'default' returns only [default]."""
    from neuron.registry import GraphRegistry

    reg = GraphRegistry(graphs_dir=tempfile.mkdtemp())
    chain = reg.resolve_chain("default")
    assert len(chain) == 1


def test_registry_resolve_chain_nested():
    """resolve_chain on 'java/spring' includes java/spring and default."""
    from neuron.registry import GraphRegistry

    reg = GraphRegistry(graphs_dir=tempfile.mkdtemp())
    reg.get("java/spring")  # create it
    chain = reg.resolve_chain("java/spring")
    names = []
    for g in chain:
        for cname, cg in reg._graphs.items():
            if cg is g:
                names.append(cname)
                break
    assert "java/spring" in names
    assert "default" in names
    assert names.index("java/spring") < names.index("default")


def test_registry_switch_deduplicates():
    """switch() reuses existing context on normalized name match."""
    from neuron.registry import GraphRegistry

    reg = GraphRegistry(graphs_dir=tempfile.mkdtemp())
    reg.switch("backend")
    reg.switch("Backend")
    reg.switch("BACKEND")

    backend_keys = [k for k in reg._graphs if "backend" in k.lower()]
    assert len(backend_keys) == 1


# ---------------------------------------------------------------------------
# _resolve_context -- normalization and inheritance
# ---------------------------------------------------------------------------

def _make_graph_with_data():
    """Helper: Graph with two linked nodes for resolver tests."""
    from neuron.models import Graph, Node, Link
    g = Graph(turn_count=5)
    g.add_node(Node(keyword="kotlin flow", turn=1, topic="async", domain="backend",
                    sentiment="neutral", salience=8))
    g.add_node(Node(keyword="coroutines", turn=2, topic="async", domain="backend",
                    sentiment="neutral", salience=6))
    g.add_link(Link(source="kotlin flow", target="coroutines",
                    link_type="deepening", weight="strong",
                    rationale="kotlin flow is built on coroutines",
                    created_turn=1, last_active_turn=4))
    return g


def _make_registry_with(graphs_dict, active):
    """Helper: build a GraphRegistry populated with given graphs."""
    from neuron.registry import GraphRegistry
    reg = GraphRegistry(graphs_dir=tempfile.mkdtemp())
    reg._graphs.update(graphs_dict)
    reg._active = active
    return reg


def test_resolve_context_normalizes_search_kws():
    """_resolve_context finds links even when topic has wrong case."""
    pytest.importorskip("mcp")
    import neuron.server as srv

    g = _make_graph_with_data()
    old_g = srv._g
    srv._g = _make_registry_with({"default": g}, "default")

    try:
        links, nodes, fallback, inherited, _ = srv._resolve_context(
            {"Kotlin Flow"},   # uppercase -- should still match
            depth=1, g=g, ctx="",
        )
        assert len(links) > 0, "Should find links despite uppercase topic"
        assert not fallback, "Should not fall through to vector fallback"
    finally:
        srv._g = old_g


def test_resolve_context_inheritance():
    """_resolve_context inherits from default when active context is empty."""
    pytest.importorskip("mcp")
    import neuron.server as srv
    from neuron.models import Graph

    default_g = _make_graph_with_data()
    child_g = Graph(turn_count=0)

    old_g = srv._g
    srv._g = _make_registry_with({"default": default_g, "backend": child_g}, "backend")

    try:
        links, nodes, fallback, inherited, _ = srv._resolve_context(
            {"kotlin flow"},
            depth=1, g=child_g, ctx="",
        )
        assert len(links) > 0, "Should find links inherited from default"
        assert inherited == "default", f"Expected 'default', got {inherited!r}"
    finally:
        srv._g = old_g


# ---------------------------------------------------------------------------
# confirm -- salience boost
# ---------------------------------------------------------------------------

def test_confirm_boosts_salience():
    """confirm handler boosts node salience by the given amount."""
    pytest.importorskip("mcp")
    import asyncio
    import neuron.server as srv
    from neuron.models import Graph, Node

    g = Graph(turn_count=2)
    g.add_node(Node(keyword="observability", turn=1, topic="ops", domain="backend",
                    sentiment="neutral", salience=3))

    old_g = srv._g
    srv._g = _make_registry_with({"default": g}, "default")

    async def _run():
        return await srv.call_tool("confirm", {"keywords": ["observability"], "boost": 4})

    try:
        asyncio.run(_run())
        nd = g.get_node("observability")
        assert nd is not None
        assert nd.salience == 7, f"Expected 7, got {nd.salience}"
    finally:
        srv._g = old_g


# ---------------------------------------------------------------------------
# merge -- alias absorption
# ---------------------------------------------------------------------------

def test_merge_absorbs_alias():
    """merge rewires alias links to canonical and removes alias node."""
    pytest.importorskip("mcp")
    import asyncio
    import neuron.server as srv
    from neuron.models import Graph, Node, Link

    g = Graph(turn_count=3)
    for kw, sal in [("kotlin", 5), ("kotlin_lang", 3), ("jvm", 4)]:
        g.add_node(Node(keyword=kw, turn=1, topic="t", domain="backend",
                        sentiment="neutral", salience=sal))
    g.add_link(Link(source="kotlin_lang", target="jvm", link_type="instance-of",
                    weight="medium", rationale="kotlin runs on jvm",
                    created_turn=1, last_active_turn=2))

    old_g = srv._g
    srv._g = _make_registry_with({"default": g}, "default")

    async def _run():
        return await srv.call_tool("merge", {"canonical": "kotlin", "aliases": ["kotlin_lang"]})

    try:
        asyncio.run(_run())
        kws = {nd.keyword for nd in g.nodes}
        assert "kotlin" in kws
        assert "kotlin_lang" not in kws, "Alias should be removed after merge"
        rewired = any(lk.source == "kotlin" and lk.target == "jvm" for lk in g.links)
        assert rewired, "Link from alias should be rewired to canonical"
    finally:
        srv._g = old_g


# ---------------------------------------------------------------------------
# pre_turn -- composite output
# ---------------------------------------------------------------------------

def test_pre_turn_returns_status_and_context():
    """pre_turn returns a status line + compact context."""
    pytest.importorskip("mcp")
    import asyncio
    import neuron.server as srv

    g = _make_graph_with_data()
    old_g = srv._g
    srv._g = _make_registry_with({"default": g}, "default")

    async def _run():
        return await srv.call_tool("pre_turn", {"topic": "kotlin flow", "max_tokens": 200})

    try:
        result = asyncio.run(_run())
        text = result[0].text
        assert "[neuron]" in text, "Should contain status line"
        lines = text.split("\n")
        assert len(lines) >= 2
        assert "links:" in lines[1] or "no context" in lines[1]
    finally:
        srv._g = old_g


def test_pre_turn_empty_graph():
    """pre_turn on empty graph returns status with 'no context'."""
    pytest.importorskip("mcp")
    import asyncio
    import neuron.server as srv
    from neuron.models import Graph

    g = Graph(turn_count=0)
    old_g = srv._g
    srv._g = _make_registry_with({"default": g}, "default")

    async def _run():
        return await srv.call_tool("pre_turn", {"topic": "anything"})

    try:
        result = asyncio.run(_run())
        text = result[0].text
        assert "[neuron]" in text
        assert "no context" in text
    finally:
        srv._g = old_g
