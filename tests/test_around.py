"""`around`: il vicinato di un tema, con quello che ricorda.

pre_turn serve i fatti dei nodi PIU' VICINI; forgotten(near) ordina la banda
media ma solo i dormienti e senza fatti. `around` e' la domanda che mancava per
un dilemma: cosa sta attorno al problema e cosa e' successo li'. E' il materiale
grezzo di gray_matter_brainstorm, che prima usava la coda di una ricerca
normale con le etichette rovesciate.
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))


@pytest.fixture
def srv():
    pytest.importorskip("mcp")
    import neuron.server as s
    return s


def _unit(x, y):
    n = (x * x + y * y) ** 0.5
    return [x / n, y / n] + [0.0] * 382


def _graph(srv, now=100):
    """Query = asse x. Similarita' = cos: obvious 0.95, mid 0.6 / 0.5 / 0.4, noise 0.1."""
    from neuron.models import Graph, Node, Link
    g = Graph()
    g.turn_count = now
    for kw, cos, turn in [("obvious", 0.95, 99), ("bare", 0.6, 99),
                          ("with-fact", 0.5, 99), ("with-link", 0.4, 60), ("noise", 0.1, 99)]:
        g.add_node(Node(keyword=kw, turn=turn, topic="t", domain="d", sentiment="neutral",
                        salience=3, vector=_unit(cos, (1 - cos * cos) ** 0.5)))
    g.add_episode("with-fact", "chose https over wss because Turso rejects the ws handshake", turn=90)
    g.links.append(Link(source="with-link", target="obvious", link_type="cause-effect",
                        weight="strong", rationale="the lock made the copy read-only",
                        created_turn=50, last_active_turn=60, inactive_turns=0))
    return g


def _run(srv, g, hub=None, **args):
    """Query = asse x. `hub` = eccessi di promiscuita' imposti (default: nessuno,
    cosi' i valori esatti nei test non dipendono dalla geometria giocattolo)."""
    orig_emb, orig_hub = srv._get_embedding, srv._hubness
    srv._get_embedding = lambda text: _unit(1.0, 0.0)
    srv._hubness = lambda graph: dict(hub or {})
    try:
        return asyncio.run(srv._tool_around(args, "ai", g))[0].text
    finally:
        srv._get_embedding, srv._hubness = orig_emb, orig_hub


def test_only_the_mid_band_and_those_with_something_first(srv):
    out = _run(srv, _graph(srv), topic="backup")
    names = [l.split()[0] for l in out.splitlines() if l.startswith("  ") and not l.startswith("    ")]
    assert "obvious" not in names, "sopra 0.75 e' gia' in contesto"
    assert "noise" not in names, "sotto 0.30 e' rumore"
    assert names == ["with-fact", "with-link", "bare"], "prima chi porta un fatto o un link"


def test_facts_and_full_rationales_travel_with_the_node(srv):
    out = _run(srv, _graph(srv), topic="backup")
    assert "fact: chose https over wss because Turso rejects the ws handshake" in out
    assert "link: with-link-[cause-effect]->obvious  #the lock made the copy read-only" in out


def test_dormancy_is_marked_so_the_caller_can_recall_or_confirm(srv):
    out = _run(srv, _graph(srv), topic="backup")
    assert "with-link  sim=0.40  salience=3  dormant 40t" in out
    assert "with-fact  sim=0.50  salience=3  active" in out


def test_the_anchor_names_what_the_memory_takes_the_topic_to_be(srv):
    """Il valore non distingue un tema noto da uno ignoto (una frase arriva a
    0.58 in entrambi i casi); il NOME del nodo piu' vicino si'. Va mostrato,
    anche se sta sopra la banda e quindi non e' in lista."""
    out = _run(srv, _graph(srv), topic="backup")
    head = out.splitlines()[0]
    assert "anchor obvious=0.95" in head
    assert "no node above the band" not in head
    from neuron.models import Graph, Node
    g = Graph(); g.turn_count = 1
    g.add_node(Node(keyword="vague", turn=1, topic="t", domain="d", sentiment="neutral",
                    vector=_unit(0.5, (1 - 0.25) ** 0.5)))
    head = _run(srv, g, topic="backup").splitlines()[0]
    assert "anchor vague=0.50" in head and "no node above the band" in head


def test_n_caps_the_rows_and_empty_band_says_so(srv):
    out = _run(srv, _graph(srv), topic="backup", n=1)
    assert out.count("sim=") == 1
    from neuron.models import Graph, Node
    g = Graph(); g.turn_count = 1
    g.add_node(Node(keyword="far", turn=1, topic="t", domain="d", sentiment="neutral",
                    vector=_unit(0.0, 1.0)))
    assert "nothing in the band" in _run(srv, g, topic="backup")


def test_hubness_is_the_excess_closeness_to_the_whole_graph(srv):
    """Un nodo nella direzione media di tutti gli altri e' vicino a tutti:
    eccesso positivo. Gli altri, ortogonali fra loro, sotto la media."""
    from neuron.models import Graph, Node
    g = Graph()
    vecs = {"a": [1, 0, 0], "b": [0, 1, 0], "c": [0, 0, 1], "hub": [1, 1, 1]}
    for kw, v in vecs.items():
        g.add_node(Node(keyword=kw, turn=1, topic="t", domain="d", sentiment="neutral", vector=v))
    h = srv._hubness(g)
    assert h["hub"] > 0.25 and all(h[k] < 0 for k in "abc")
    assert abs(sum(h.values())) < 1e-5, "e' un centraggio: la somma degli eccessi e' zero"


def test_a_hub_pays_its_excess_and_the_output_says_so(srv):
    """Sul vivo: `vram` a 0.40 da 'il daemon non si riavvia' per il suo eccesso
    di 0.13, non per il daemon. Con la penalita' esce dalla banda; un nodo
    normale non cambia."""
    g = _graph(srv)
    out = _run(srv, g, hub={"bare": 0.35}, topic="backup")
    names = [l.split()[0] for l in out.splitlines() if l.startswith("  ") and not l.startswith("    ")]
    assert "bare" not in names, "0.60 - 0.35 = 0.25: sotto la banda"
    assert "with-fact  sim=0.50  salience=3" in out
    out = _run(srv, g, hub={"bare": 0.10}, topic="backup")
    assert "bare  sim=0.50 (hub -0.10)" in out, "resta in banda, ma si vede perche' e' sceso"


def test_around_is_callable_but_not_announced(srv):
    assert "around" in srv._HANDLERS
    assert "around" in srv._ADMIN_TOOLS, "e' materiale per GM, non un tool del loop"
