"""Exact matching must work on capitalised keys.

`_resolve_context` lowercased the query and compared it against the graph's
keys, under a comment claiming those keys were lowercased. They are not: on a
real graph 37 nodes out of 52 carry capitals ('Neuron', 'AGENTS',
'DESIGN-CROSSLINKS') and the links preserve them. So `{"neuron"} & {"Neuron"}`
was empty, `lk.source in current` never matched, and the link walk — the graph
itself — was dead for most concepts: EVERY search ended in the vector fallback.

Nobody noticed because the fallback works and returns right answers. The only
visible symptom was `(vector fallback)` on every reply, including a
`get_context(topic="Neuron")` that listed `nodes:Neuron(1)` on the same line —
the node was there, and was treated as absent.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))


def _loaded_graph(nodes=("Neuron", "pre_turn", "AGENTS"),
                  links=(("Neuron", "pre_turn"),)):
    """A graph as it comes out of LOADING, not out of writing.

    `Graph.add_node`/`add_link` go through `_norm` (strip+lower), so a graph
    built with those is lowercased and the bug does not reproduce. The read path
    instead does a plain `self.nodes.append(nd)` (models.py, load), and rows
    written by other routes — the knowledge base ingest produces 'AGENTS',
    'DESIGN-CROSSLINKS', 'neuron/README' — arrive in memory with their own
    capitalisation. That is the real state searches run against.
    """
    from neuron.models import Graph, Node, Link
    g = Graph(); g.turn_count = 10
    for kw in nodes:
        g.nodes.append(Node(keyword=kw, turn=10, topic="t", domain="d",
                            sentiment="neutral", salience=5))
    for s, t in links:
        g.links.append(Link(s, t, "deepening", "medium", "", 10, 10))
    g._rebuild_node_map()
    return g


@pytest.mark.parametrize("query", ["neuron", "Neuron", "NEURON"])
def test_a_capitalised_node_is_found_without_the_vector_fallback(monkeypatch, query):
    pytest.importorskip("mcp"); pytest.importorskip("fastembed")
    import neuron.server as srv
    # The fallback MUST have something to return, or `used_fallback` stays False
    # even when it runs and the assertion cannot tell the two cases apart.
    monkeypatch.setattr(srv, "_search_embeddings", lambda *a, **k: [("AGENTS", 0.9)])
    links, top_nodes, used_fallback, *_ = srv._resolve_context({query}, 1, _loaded_graph(), "")

    assert used_fallback is False, "no exact match: it fell back to vectors"
    assert [(lk.source, lk.target) for lk in links] == [("Neuron", "pre_turn")]
    assert "Neuron" in [kw for kw, _ in top_nodes]


def test_the_real_capitalisation_survives_into_the_results(monkeypatch):
    """Canonicalising also keeps us from inventing names: `related_nodes` gets
    the search keywords as they are, and a lowercase variant would be a concept
    that does not exist in the graph."""
    pytest.importorskip("mcp"); pytest.importorskip("fastembed")
    import neuron.server as srv
    monkeypatch.setattr(srv, "_search_embeddings", lambda *a, **k: [])
    _, top_nodes, *_ = srv._resolve_context({"agents"}, 1, _loaded_graph(), "")

    names = [kw for kw, _ in top_nodes]
    assert "AGENTS" in names and "agents" not in names, names


def test_an_unknown_keyword_still_reaches_the_vector_search(monkeypatch):
    """The other direction: canonicalising must not turn an absent concept into
    a match. What is not there stays lowercased and goes to the vector search,
    which is exactly its job."""
    pytest.importorskip("mcp"); pytest.importorskip("fastembed")
    import neuron.server as srv
    seen = {}
    monkeypatch.setattr(srv, "_search_embeddings",
                        lambda kws, **k: seen.setdefault("kws", list(kws)) and [])
    monkeypatch.setattr(srv._g, "resolve_chain", lambda _c: [None])
    _, _, used_fallback, *_ = srv._resolve_context({"Qualcosa-Che-Non-Esiste"}, 1,
                                                   _loaded_graph(), "")

    assert used_fallback is False        # the fallback ran and found nothing
    assert seen["kws"] == ["qualcosa-che-non-esiste"]


def test_the_canon_map_prefers_a_node_over_a_link_endpoint():
    """On the same key the node wins: the node is the data, the link cites it."""
    pytest.importorskip("mcp")
    import neuron.server as srv
    g = _loaded_graph(nodes=("Bridge",), links=(("BRIDGE", "x"),))

    assert srv._canon_kws(g, {"bridge"}) == {"Bridge"}
