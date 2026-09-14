"""Centraggio per promiscuita' in OGNI ranking vettoriale.

Misurato 2026-09-14 su 368 nodi: una dozzina di token tecnici corti (`vram`,
`lombok`, `gui`) stanno a 0.36-0.42 di coseno medio da tutti gli altri contro
0.24 del grafo, e `vector_search("lombok")` rispondeva `vram=0.88`. E' il
modello, che parcheggia le parole ignote insieme. La correzione vive in
`search.hub_excess` e si applica dentro `_search_embeddings`, dove passano
pre_turn, get_context, find_candidates e il cross-context: una guardia sola.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))


@pytest.fixture
def srv():
    pytest.importorskip("mcp")
    import neuron.server as s
    return s


def _graph(srv):
    """a, b, c ortogonali; hub = la loro direzione media, vicino a tutti."""
    from neuron.models import Graph, Node
    g = Graph()
    for kw, v in {"a": [1, 0, 0], "b": [0, 1, 0], "c": [0, 0, 1], "hub": [1, 1, 1]}.items():
        g.add_node(Node(keyword=kw, turn=1, topic="t", domain="d", sentiment="neutral", vector=v))
    return g


def _search(srv, g, query_vec, top_n=4):
    orig_turso, orig_emb = srv.TURSO_ENGINE, srv._get_embedding
    srv.TURSO_ENGINE = False                       # tier Python
    srv._get_embedding = lambda text: query_vec
    try:
        return srv._search_embeddings(["q"], top_n=top_n, graph=g)
    finally:
        srv.TURSO_ENGINE, srv._get_embedding = orig_turso, orig_emb


def test_a_hub_no_longer_outranks_the_node_the_query_actually_means(srv):
    """Query a 0.8 da `a` e a 0.81 dall'hub: grezzo vincerebbe l'hub, che pero'
    e' vicino a TUTTO. Col centraggio `a` torna primo e l'hub paga il suo
    eccesso (0.81 - 0.29 = 0.52); `b` a 0.6 non si muove."""
    res = dict(_search(srv, _graph(srv), [0.8, 0.6, 0.0]))
    assert list(res)[0] == "a"
    assert res["a"] == pytest.approx(0.8, abs=0.01)
    assert res["b"] == pytest.approx(0.6, abs=0.01), "un nodo normale non cambia"
    assert res["hub"] < res["b"], "l'hub scende sotto un nodo normale meno simile"


def test_excess_is_centered_and_only_the_penalty_side_applies(srv):
    from neuron.search import hub_excess, hub_penalty
    ex = hub_excess(_graph(srv))
    assert ex["hub"] > 0.25 and all(ex[k] < 0 for k in "abc")
    assert abs(sum(ex.values())) < 1e-5, "centraggio: la somma degli eccessi e' zero"
    assert hub_penalty(ex, "hub") == pytest.approx(ex["hub"])
    assert hub_penalty(ex, "a") == 0.0, "un nodo isolato non viene spinto su"
    assert hub_penalty({"x": 0.04}, "x") == 0.0, "sotto 0.05 e' rumore di misura"


def test_the_excess_is_cached_on_the_graph_until_a_node_arrives(srv):
    from neuron.models import Node
    from neuron.search import hub_excess
    g = _graph(srv)
    first = hub_excess(g)
    assert hub_excess(g) is first, "stesso grafo, stessi vettori: cache"
    g.add_node(Node(keyword="d", turn=1, topic="t", domain="d", sentiment="neutral", vector=[1, 1, 0]))
    again = hub_excess(g)
    assert again is not first and "d" in again


def test_too_few_vectors_means_no_correction_not_a_crash(srv):
    from neuron.models import Graph, Node
    from neuron.search import hub_excess
    g = Graph()
    g.add_node(Node(keyword="solo", turn=1, topic="t", domain="d", sentiment="neutral", vector=[1, 0, 0]))
    assert hub_excess(g) == {}
    assert _search(srv, g, [1, 0, 0]) == [("solo", 1.0)]
