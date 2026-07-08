"""E3.1 — cross-context drift links (Graph.form_drift_link) + E3.2 surfacing.

E3.1 is pure (neuron.models). E3.2 needs the server module (mcp/fastembed).
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import neuron.models as m
from neuron.models import Graph, Node, Link


def _g_with(kw="python"):
    g = Graph()
    g.add_node(Node(keyword=kw, turn=1, topic="t", domain="backend", sentiment="neutral"))
    return g


# --- E3.1 -------------------------------------------------------------------

def test_forms_tangential_drift_with_target_context():
    g = _g_with()
    lk = g.form_drift_link("python", "dough", "cooking", turn=1)
    assert lk is not None
    assert lk.link_type == "drift" and lk.weight == "tangential"
    assert lk.target_context == "cooking" and lk.co_activation_count == 1
    assert lk in g.drift_links()


def test_drift_cooldown_blocks_then_reinforces():
    g = _g_with()
    g.form_drift_link("python", "dough", "cooking", turn=1)
    assert g.form_drift_link("python", "dough", "cooking", turn=3) is None   # 3-1 < 5
    lk = g.form_drift_link("python", "dough", "cooking", turn=6)             # 6-1 >= 5
    assert lk is not None and lk.co_activation_count == 2


def test_drift_pruned_faster_than_tangential():
    g = _g_with()
    drift = g.form_drift_link("python", "dough", "cooking", turn=1)
    drift.inactive_turns = 4          # > DRIFT_EXPIRY_TURNS (3)
    g.add_link(Link(source="python", target="flask", link_type="deepening",
                    weight="tangential", rationale="", created_turn=1, last_active_turn=1))
    normal = next(lk for lk in g.links if lk.target == "flask")
    normal.inactive_turns = 4         # <= TANGENTIAL_EXPIRY_TURNS (5)
    g.prune_tangential()
    assert drift not in g.links       # drift expired at 3
    assert normal in g.links          # normal tangential still alive at 4


def test_drift_excluded_from_active_links_and_spreading():
    g = _g_with()
    lk = g.form_drift_link("python", "dough", "cooking", turn=1)
    lk.weight = "medium"              # even promoted, drift stays out of the normal views
    assert lk not in g.get_active_links()
    assert dict(g.spreading_activation(["python"], k=2)) == {}   # foreign target not walked


def test_drift_target_context_survives_roundtrip():
    g = _g_with()
    g.form_drift_link("python", "dough", "cooking", turn=1)
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd); os.unlink(path)
    try:
        g.save_sqlite(path, context="default")
        g2 = Graph()
        g2.load_sqlite(path, context="default")
        d = g2.drift_links()
        assert len(d) == 1 and d[0].target_context == "cooking"
    finally:
        os.path.exists(path) and os.unlink(path)


# --- E3.2 -------------------------------------------------------------------

def test_drift_surfaces_only_at_depth_3():
    pytest.importorskip("mcp"); pytest.importorskip("fastembed")
    import neuron.server as srv
    g = Graph()
    g.add_node(Node(keyword="python", turn=1, topic="t", domain="backend", sentiment="neutral"))
    g.add_node(Node(keyword="django", turn=1, topic="t", domain="backend", sentiment="neutral"))
    g.add_link(Link(source="python", target="django", link_type="deepening", weight="medium",
                    rationale="", created_turn=1, last_active_turn=1))
    g.form_drift_link("python", "dough", "cooking", turn=1)

    links_d2, *_ = srv._resolve_context({"python"}, 2, g, "")
    assert not any(lk.link_type == "drift" for lk in links_d2)

    links_d3, *_ = srv._resolve_context({"python"}, 3, g, "")
    assert any(lk.link_type == "drift" and lk.target_context == "cooking" for lk in links_d3)
