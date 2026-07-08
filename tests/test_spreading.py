"""E2.3 — Graph.spreading_activation(): k-hop activation along links.

Pure (neuron.models), runs anywhere.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from neuron.models import Graph, Node, Link


def _n(g, kw, sal=0):
    g.add_node(Node(keyword=kw, turn=1, topic="t", domain="d", sentiment="neutral", salience=sal))


def _l(g, s, t, weight="medium"):
    g.add_link(Link(source=s, target=t, link_type="deepening", weight=weight,
                    rationale="", created_turn=1, last_active_turn=1))


def test_reaches_two_hops_and_excludes_seed():
    g = Graph()
    for kw in ("seed", "a", "b"):
        _n(g, kw)
    _l(g, "seed", "a", "strong")
    _l(g, "a", "b", "medium")
    out = dict(g.spreading_activation(["seed"], k=2))
    assert "seed" not in out                 # seeds are not their own stimulus
    assert "a" in out and "b" in out         # b is reached at hop 2
    assert out["a"] > out["b"]               # decay: closer node stays hotter


def test_k_limits_depth():
    g = Graph()
    for kw in ("seed", "a", "b"):
        _n(g, kw)
    _l(g, "seed", "a", "strong")
    _l(g, "a", "b", "strong")
    out = dict(g.spreading_activation(["seed"], k=1))
    assert "a" in out and "b" not in out     # only 1 hop


def test_stronger_link_carries_more_activation():
    g = Graph()
    for kw in ("seed", "x", "y"):
        _n(g, kw)
    _l(g, "seed", "x", "strong")
    _l(g, "seed", "y", "tangential")
    out = dict(g.spreading_activation(["seed"], k=1))
    assert out["x"] > out["y"]


def test_salient_node_accumulates_more():
    g = Graph()
    _n(g, "seed")
    _n(g, "p", sal=20)      # salient hub
    _n(g, "q", sal=0)
    _l(g, "seed", "p", "medium")
    _l(g, "seed", "q", "medium")
    out = dict(g.spreading_activation(["seed"], k=1))
    assert out["p"] > out["q"]


def test_no_valid_seed_returns_empty():
    g = Graph()
    _n(g, "seed")
    assert g.spreading_activation(["nonexistent"], k=2) == []
