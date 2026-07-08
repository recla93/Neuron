"""E2.1 — Hebbian reinforcement: co_activation_count on links + cooldown + weight promotion.

Pure (neuron.models + sqlite), runs anywhere.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import neuron.models as m
from neuron.models import Graph, Node, Link


def _graph(link_weight="tangential"):
    g = Graph()
    g.add_node(Node(keyword="python", turn=1, topic="t", domain="backend", sentiment="neutral"))
    g.add_node(Node(keyword="django", turn=1, topic="t", domain="backend", sentiment="neutral"))
    g.add_node(Node(keyword="docker", turn=1, topic="t", domain="devops", sentiment="neutral"))
    g.add_link(Link(source="python", target="django", link_type="deepening",
                    weight=link_weight, rationale="", created_turn=1, last_active_turn=1))
    return g


def _pd(g):
    return next(lk for lk in g.links if {lk.source, lk.target} == {"python", "django"})


def test_coactivation_bumps_count_and_marks_dirty():
    g = _graph()
    g.reinforce_coactivation(["python", "django"], turn=1)
    lk = _pd(g)
    assert lk.co_activation_count == 1
    assert lk.weight == "tangential"          # below the medium threshold (3)
    assert m.Graph._link_key(lk) in g._dirty_links


def test_cooldown_blocks_rapid_recount():
    g = _graph()
    g.reinforce_coactivation(["python", "django"], turn=10)
    g.reinforce_coactivation(["python", "django"], turn=11)   # 11-10 < 2 → blocked
    assert _pd(g).co_activation_count == 1
    g.reinforce_coactivation(["python", "django"], turn=12)   # 12-10 == 2 → counts
    assert _pd(g).co_activation_count == 2


def test_weight_promotes_at_thresholds():
    g = _graph()
    weights = []
    for t in range(0, 16, 2):   # 8 counts, each ≥ cooldown apart
        g.reinforce_coactivation(["python", "django"], turn=t)
        weights.append((_pd(g).co_activation_count, _pd(g).weight))
    assert (3, "medium") in weights          # tangential → medium at 3
    assert (8, "strong") in weights          # medium → strong at 8


def test_only_reinforces_coactive_existing_links():
    g = _graph()
    g.reinforce_coactivation(["python", "docker"], turn=1)   # django not active
    assert _pd(g).co_activation_count == 0
    g.reinforce_coactivation(["python"], turn=3)             # single keyword → no pair
    assert _pd(g).co_activation_count == 0


def test_never_downgrades_a_strong_link():
    g = _graph(link_weight="strong")
    g.reinforce_coactivation(["python", "django"], turn=1)   # count 1 → would be "tangential"
    lk = _pd(g)
    assert lk.co_activation_count == 1
    assert lk.weight == "strong"             # monotone: never downgraded


def test_count_survives_save_load_roundtrip():
    g = _graph()
    for t in range(0, 6, 2):
        g.reinforce_coactivation(["python", "django"], turn=t)
    assert _pd(g).co_activation_count == 3
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd); os.unlink(path)
    try:
        g.save_sqlite(path, context="default")
        g2 = Graph()
        g2.load_sqlite(path, context="default")
        assert _pd(g2).co_activation_count == 3
        assert _pd(g2).weight == "medium"
    finally:
        os.path.exists(path) and os.unlink(path)
